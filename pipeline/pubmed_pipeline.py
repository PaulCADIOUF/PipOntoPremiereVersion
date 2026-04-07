"""
pipeline/pubmed_pipeline.py
============================
Pipeline principal PubMed → PostgreSQL pour PIPOnto.

Flux :
    1. Pour chaque maladie définie dans pubmed_queries.py :
       a. ESearch  → récupère les PMIDs correspondants
       b. EFetch   → récupère les métadonnées XML (titre, auteurs, DOI, abstract)
       c. Filtre   → score de pertinence par mots-clés
       d. Insert   → insère dans piponto.model_references (si score ≥ seuil)
    2. Génère un rapport CSV de tout ce qui a été collecté
    3. Supporte la reprise (ne re-télécharge pas les DOIs déjà en base)

Dépendances :
    pip install biopython requests psycopg2-binary python-dotenv

Configuration :
    Fichier ~/piponto/.env avec DB_* et PUBMED_API_KEY (optionnel mais recommandé)
    Clé gratuite sur : https://www.ncbi.nlm.nih.gov/account/

Limites API NCBI :
    Sans clé : 3 requêtes/seconde
    Avec clé  : 10 requêtes/seconde
"""

import os
import re
import time
import json
import logging
import csv
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from dotenv import load_dotenv

from pubmed_queries import ALL_QUERIES, DiseaseQuery

# ── Chargement configuration ──────────────────────────────────────────────────
load_dotenv(Path.home() / "piponto" / ".env")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}
PUBMED_API_KEY = os.getenv("PUBMED_API_KEY", "")
NCBI_EMAIL     = os.getenv("NCBI_EMAIL", "piponto@research.org")

# ── URLs API NCBI E-utilities ─────────────────────────────────────────────────
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# ── Paramètres du pipeline ────────────────────────────────────────────────────
RELEVANCE_THRESHOLD = 0.25   # abaissé de 0.30 → 0.25 après calibration COVID   # score minimum pour insérer en base
BATCH_SIZE          = 100    # articles par appel EFetch
SLEEP_BETWEEN_CALLS = 0.35   # secondes (< 3/s sans clé, < 10/s avec clé)

# ── Logging ───────────────────────────────────────────────────────────────────
log_dir = Path.home() / "piponto" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_dir / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("piponto.pipeline")


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURES DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ArticleRecord:
    """Article récupéré depuis PubMed, avant insertion en base."""
    pmid:           str
    doi:            Optional[str]
    title:          str
    authors:        str           # "Nom1 A, Nom2 B, ..."
    authors_list:   list[dict]    # [{"name": "...", "affiliation": "..."}]
    journal:        str
    year:           int
    volume:         Optional[str]
    issue:          Optional[str]
    pages:          Optional[str]
    abstract:       str
    keywords_mesh:  list[str]
    github_url:     Optional[str]
    open_access:    bool

    # Champs calculés par le filtre
    disease_key:    str = ""
    relevance_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class PipelineStats:
    """Statistiques d'une exécution du pipeline."""
    disease_key:    str
    query_total:    int = 0      # PMIDs trouvés par ESearch
    fetched:        int = 0      # articles récupérés par EFetch
    relevant:       int = 0      # articles passant le filtre de pertinence
    inserted:       int = 0      # articles insérés en base
    skipped_dup:    int = 0      # déjà en base (DOI dupliqué)
    skipped_low:    int = 0      # score trop bas
    errors:         int = 0      # erreurs d'insertion


# ══════════════════════════════════════════════════════════════════════════════
# CONNEXION POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════

def get_db_connection():
    """Retourne une connexion PostgreSQL. Lève une exception si échec."""
    try:
        import psycopg2
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except ImportError:
        raise RuntimeError(
            "psycopg2 non installé. Exécutez : pip install psycopg2-binary"
        )
    except Exception as e:
        raise RuntimeError(f"Connexion PostgreSQL impossible : {e}\n"
                           f"Vérifiez le fichier ~/.env et que PostgreSQL est démarré.")


def get_existing_dois(conn) -> set[str]:
    """Récupère tous les DOIs déjà en base pour éviter les doublons."""
    with conn.cursor() as cur:
        cur.execute("SELECT doi FROM piponto.model_references WHERE doi IS NOT NULL")
        return {row[0] for row in cur.fetchall()}


def get_existing_pmids(conn) -> set[str]:
    """Récupère tous les PMIDs déjà en base."""
    with conn.cursor() as cur:
        cur.execute("SELECT pubmed_id FROM piponto.model_references WHERE pubmed_id IS NOT NULL")
        return {row[0] for row in cur.fetchall()}


def get_disease_id(conn, disease_key: str) -> Optional[int]:
    """Retourne le disease_id PostgreSQL depuis la clé PIPOnto."""
    with conn.cursor() as cur:
        # Cherche dans uri_m8
        cur.execute(
            "SELECT disease_id FROM piponto.diseases WHERE uri_m8 LIKE %s",
            (f"%#{disease_key}",)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        # Fallback : cherche dans name_en
        cur.execute(
            "SELECT disease_id FROM piponto.diseases WHERE name_en ILIKE %s",
            (f"%{disease_key.replace('_', ' ')}%",)
        )
        row = cur.fetchone()
        return row[0] if row else None


# ══════════════════════════════════════════════════════════════════════════════
# API NCBI
# ══════════════════════════════════════════════════════════════════════════════

def _ncbi_params(extra: dict) -> dict:
    """Paramètres de base pour toutes les requêtes NCBI."""
    params = {
        "tool":  "PIPOnto",
        "email": NCBI_EMAIL,
        "retmode": "xml",
    }
    if PUBMED_API_KEY:
        params["api_key"] = PUBMED_API_KEY
    params.update(extra)
    return params


def esearch(query: str, max_results: int, retstart: int = 0) -> list[str]:
    """
    Appelle ESearch pour obtenir les PMIDs correspondant à une requête.
    Retourne une liste de PMIDs (strings).
    """
    params = _ncbi_params({
        "db":       "pubmed",
        "term":     query,
        "retmax":   min(max_results, 500),   # max 500 par appel
        "retstart": retstart,
        "usehistory": "n",
    })
    try:
        resp = requests.get(ESEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        pmids = [id_elem.text for id_elem in root.findall(".//Id") if id_elem.text]
        count_elem = root.find(".//Count")
        total = int(count_elem.text) if count_elem is not None else 0
        logger.debug(f"ESearch : {len(pmids)} PMIDs récupérés (total={total})")
        return pmids, total
    except Exception as e:
        logger.error(f"Erreur ESearch : {e}")
        return [], 0


def efetch_batch(pmids: list[str]) -> str:
    """
    Appelle EFetch pour un lot de PMIDs.
    Retourne le XML brut.
    """
    params = _ncbi_params({
        "db":      "pubmed",
        "id":      ",".join(pmids),
        "rettype": "abstract",
    })
    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=60)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.error(f"Erreur EFetch (batch {len(pmids)} PMIDs) : {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# PARSING XML PUBMED
# ══════════════════════════════════════════════════════════════════════════════

def _text(elem, path: str, default: str = "") -> str:
    """Extrait le texte d'un sous-élément XML."""
    node = elem.find(path)
    return (node.text or "").strip() if node is not None else default


def _find_doi(article_elem) -> Optional[str]:
    """Extrait le DOI depuis les ELocationID ou ArticleId."""
    for loc in article_elem.findall(".//ELocationID[@EIdType='doi']"):
        if loc.text:
            return loc.text.strip()
    for aid in article_elem.findall(".//ArticleId[@IdType='doi']"):
        if aid.text:
            return aid.text.strip()
    return None


def _find_github(abstract: str) -> Optional[str]:
    """Détecte une URL GitHub dans l'abstract."""
    m = re.search(r'https?://github\.com/[\w\-]+/[\w\-\.]+', abstract)
    return m.group(0) if m else None


def _parse_authors(article_elem) -> tuple[str, list[dict]]:
    """Retourne (authors_str, authors_list)."""
    authors_list = []
    for author in article_elem.findall(".//Author"):
        last  = _text(author, "LastName")
        first = _text(author, "ForeName") or _text(author, "Initials")
        affil = _text(author, ".//AffiliationInfo/Affiliation")
        if last:
            name = f"{last} {first}".strip()
            authors_list.append({"name": name, "affiliation": affil})

    if not authors_list:
        # CollectiveName (consortiums)
        coll = article_elem.find(".//CollectiveName")
        if coll is not None and coll.text:
            authors_list = [{"name": coll.text.strip(), "affiliation": ""}]

    authors_str = ", ".join(a["name"] for a in authors_list[:8])
    if len(authors_list) > 8:
        authors_str += " et al."
    return authors_str, authors_list


def _parse_abstract(article_elem) -> str:
    """Concatène tous les blocs AbstractText."""
    parts = []
    for block in article_elem.findall(".//AbstractText"):
        label = block.get("Label", "")
        text  = (block.text or "").strip()
        if text:
            parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts)


def parse_pubmed_xml(xml_text: str, disease_key: str) -> list[ArticleRecord]:
    """
    Parse le XML PubMed et retourne une liste d'ArticleRecord.
    """
    records = []
    if not xml_text:
        return records

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"Erreur parsing XML PubMed : {e}")
        return records

    for article_elem in root.findall(".//PubmedArticle"):
        try:
            pmid = _text(article_elem, ".//PMID")
            if not pmid:
                continue

            title    = _text(article_elem, ".//ArticleTitle")
            journal  = _text(article_elem, ".//Journal/Title")
            abstract = _parse_abstract(article_elem)
            doi      = _find_doi(article_elem)
            github   = _find_github(abstract)
            authors_str, authors_list = _parse_authors(article_elem)

            # Année
            year_text = (
                _text(article_elem, ".//PubDate/Year") or
                _text(article_elem, ".//PubDate/MedlineDate", "0000")[:4]
            )
            try:
                year = int(year_text)
            except ValueError:
                year = 0

            # Volume / Issue / Pages
            volume = _text(article_elem, ".//JournalIssue/Volume") or None
            issue  = _text(article_elem, ".//JournalIssue/Issue") or None
            pages  = _text(article_elem, ".//MedlinePgn") or None

            # Mots-clés MeSH
            mesh_keywords = [
                kw.text.strip()
                for kw in article_elem.findall(".//MeshHeading/DescriptorName")
                if kw.text
            ]

            # Open access (détection heuristique)
            open_access = any(
                "open access" in (pub_type.text or "").lower()
                for pub_type in article_elem.findall(".//PublicationType")
            )

            if title and year > 1960:
                records.append(ArticleRecord(
                    pmid=pmid,
                    doi=doi,
                    title=title,
                    authors=authors_str,
                    authors_list=authors_list,
                    journal=journal,
                    year=year,
                    volume=volume,
                    issue=issue,
                    pages=pages,
                    abstract=abstract,
                    keywords_mesh=mesh_keywords,
                    github_url=github,
                    open_access=open_access,
                    disease_key=disease_key,
                ))
        except Exception as e:
            logger.warning(f"Erreur parsing article PMID={pmid}: {e}")
            continue

    return records


# ══════════════════════════════════════════════════════════════════════════════
# FILTRE DE PERTINENCE
# ══════════════════════════════════════════════════════════════════════════════

def compute_relevance_score(
    record: ArticleRecord,
    query: DiseaseQuery,
) -> tuple[float, list[str]]:
    """
    Calcule un score de pertinence 0-1 pour un article.

    Critères pondérés :
        0.40 — mots-clés obligatoires dans titre+abstract
        0.20 — termes de modélisation dans titre
        0.20 — paramètres numériques mentionnés
        0.10 — code disponible (GitHub)
        0.10 — calibration sur données réelles mentionnée
    """
    text_full  = f"{record.title} {record.abstract}".lower()
    text_title = record.title.lower()

    matched = []
    score   = 0.0

    # 1. Mots-clés obligatoires (40%)
    required_matches = [
        kw for kw in query.required_keywords
        if kw.lower() in text_full
    ]
    if required_keywords_ratio := len(required_matches) / max(len(query.required_keywords), 1):
        score += 0.40 * min(required_keywords_ratio * 1.5, 1.0)
        matched.extend(required_matches)

    # 2. Termes de modélisation dans le TITRE (20%)
    model_title_terms = [
        "seir", "sir", "seis", "seirs", "seird", "compartmental",
        "mathematical model", "epidemic model", "transmission model",
        "agent-based", "individual-based", "stochastic model",
        "network model", "metapopulation"
    ]
    title_model_hits = [t for t in model_title_terms if t in text_title]
    if title_model_hits:
        score += 0.20
        matched.extend(title_model_hits[:3])

    # 3. Paramètres numériques mentionnés (20%)
    param_terms = [
        "basic reproduction number", "r0", "r₀",
        "transmission rate", "beta", "β",
        "recovery rate", "gamma", "γ",
        "incubation", "serial interval", "generation time",
        "calibrated", "estimated", "fitted",
        "least squares", "mcmc", "bayesian"
    ]
    param_hits = [t for t in param_terms if t in text_full]
    if param_hits:
        score += 0.20 * min(len(param_hits) / 3, 1.0)
        matched.extend(param_hits[:3])

    # 4. Code disponible (10%)
    code_terms = ["github", "code available", "source code", "zenodo",
                  "software", "netlogo", "python", "gama", "covasim"]
    if record.github_url or any(t in text_full for t in code_terms):
        score += 0.10
        matched.append("code_available")

    # 5. Calibration sur données réelles (10%)
    calib_terms = [
        "calibrated", "fitted to", "validated against",
        "empirical data", "real data", "surveillance data",
        "reported cases", "hospitalization data"
    ]
    if any(t in text_full for t in calib_terms):
        score += 0.10
        matched.append("empirically_calibrated")

    # Pénalité si mots exclus (proportionnelle au nombre de hits)
    n_excluded = sum(1 for excl in query.exclude_keywords if excl.lower() in text_full)
    if n_excluded == 1:
        score *= 0.75    # pénalité légère pour 1 mot exclu
        matched.append("⚠️ excluded_keyword_minor")
    elif n_excluded >= 2:
        score *= 0.40    # pénalité forte si 2+ mots exclus
        matched.append(f"⚠️ excluded_keywords_x{n_excluded}")

    return round(min(score, 1.0), 3), list(set(matched))


# ══════════════════════════════════════════════════════════════════════════════
# INSERTION POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════

INSERT_REFERENCE_SQL = """
INSERT INTO piponto.model_references (
    doi, pubmed_id, title, authors, authors_list,
    journal, year, volume, issue, pages, abstract,
    open_access, github_url
)
VALUES (
    %(doi)s, %(pubmed_id)s, %(title)s, %(authors)s, %(authors_list)s,
    %(journal)s, %(year)s, %(volume)s, %(issue)s, %(pages)s, %(abstract)s,
    %(open_access)s, %(github_url)s
)
ON CONFLICT (doi) DO NOTHING
RETURNING reference_id;
"""

INSERT_KEYWORD_SQL = """
INSERT INTO piponto.keywords (model_id, keyword, language, weight, source)
SELECT NULL, %s, 'en', %s, 'pubmed_mesh'
WHERE NOT EXISTS (
    SELECT 1 FROM piponto.keywords
    WHERE model_id IS NULL AND keyword = %s
);
"""

INSERT_LOG_SQL = """
INSERT INTO piponto.extraction_log (
    reference_id, extraction_method, extractor_version,
    extractor_name, raw_extraction, confidence_scores, final_status
)
VALUES (%s, 'AUTO_NLP', '1.0', 'PubMed Pipeline',
        %s::jsonb, %s::jsonb, 'PENDING');
"""


def insert_article(
    conn,
    record: ArticleRecord,
    disease_id: Optional[int],
) -> Optional[int]:
    """
    Insère un article dans model_references.
    Retourne le reference_id créé, ou None si doublon.
    """
    with conn.cursor() as cur:
        cur.execute(INSERT_REFERENCE_SQL, {
            "doi":          record.doi,
            "pubmed_id":    record.pmid,
            "title":        record.title[:1000],
            "authors":      record.authors[:500],
            "authors_list": json.dumps(record.authors_list, ensure_ascii=False),
            "journal":      record.journal[:300],
            "year":         record.year if record.year > 0 else None,
            "volume":       record.volume,
            "issue":        record.issue,
            "pages":        record.pages,
            "abstract":     record.abstract[:5000] if record.abstract else None,
            "open_access":  record.open_access,
            "github_url":   record.github_url,
        })
        row = cur.fetchone()
        if row is None:
            return None    # doublon DOI — ON CONFLICT DO NOTHING

        reference_id = row[0]

        # Log d'extraction
        raw_data = json.dumps({
            "pmid": record.pmid,
            "disease_key": record.disease_key,
            "matched_keywords": record.matched_keywords,
        }, ensure_ascii=False)
        confidence = json.dumps({
            "relevance": record.relevance_score,
            "has_doi": record.doi is not None,
            "has_abstract": len(record.abstract) > 100,
            "has_github": record.github_url is not None,
        })
        cur.execute(INSERT_LOG_SQL, (reference_id, raw_data, confidence))

        return reference_id


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class PubMedPipeline:
    """
    Pipeline complet PubMed → PostgreSQL.

    Usage :
        pipeline = PubMedPipeline()
        pipeline.run()            # toutes les maladies
        pipeline.run(["COVID19"]) # maladies spécifiques
    """

    def __init__(self, dry_run: bool = False):
        """
        dry_run=True : simule sans insérer en base (test).
        """
        self.dry_run = dry_run
        self.conn = None
        self.existing_dois: set[str] = set()
        self.existing_pmids: set[str] = set()
        self.all_stats: list[PipelineStats] = []
        self.output_dir = Path.home() / "piponto" / "data"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not dry_run:
            logger.info("Connexion à PostgreSQL...")
            self.conn = get_db_connection()
            self.existing_dois  = get_existing_dois(self.conn)
            self.existing_pmids = get_existing_pmids(self.conn)
            logger.info(f"Base connectée — {len(self.existing_dois)} DOIs existants")
        else:
            logger.info("Mode DRY RUN — aucune insertion en base")

        # Vitesse selon disponibilité de la clé API
        self.sleep_time = 0.12 if PUBMED_API_KEY else 0.35
        if PUBMED_API_KEY:
            logger.info(f"Clé API NCBI détectée — vitesse maximale (10 req/s)")
        else:
            logger.warning(
                "Pas de clé API NCBI — vitesse limitée (3 req/s). "
                "Créez une clé gratuite sur https://www.ncbi.nlm.nih.gov/account/"
            )

    def run(self, disease_keys: Optional[list[str]] = None):
        """
        Lance le pipeline pour toutes les maladies (ou une liste spécifique).
        """
        queries_to_run = {}
        if disease_keys:
            for key in disease_keys:
                if key in ALL_QUERIES:
                    queries_to_run[key] = ALL_QUERIES[key]
                else:
                    logger.warning(f"Clé inconnue : {key} — ignorée")
        else:
            queries_to_run = ALL_QUERIES

        logger.info(f"\n{'═'*60}")
        logger.info(f"  PIPOnto Pipeline PubMed — {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        logger.info(f"  {len(queries_to_run)} requêtes à traiter")
        logger.info(f"{'═'*60}\n")

        for disease_key, query in queries_to_run.items():
            try:
                stats = self._process_disease(disease_key, query)
                self.all_stats.append(stats)
            except KeyboardInterrupt:
                logger.info("\n⚡ Interruption utilisateur — arrêt propre")
                break
            except Exception as e:
                logger.error(f"Erreur fatale pour {disease_key} : {e}")
                continue

        if self.conn and not self.dry_run:
            self.conn.commit()
            self.conn.close()

        self._print_final_report()
        self._export_csv()

    def _process_disease(self, disease_key: str, query: DiseaseQuery) -> PipelineStats:
        """Traite une maladie complète : search → fetch → filter → insert."""
        stats = PipelineStats(disease_key=disease_key)
        logger.info(f"\n{'─'*60}")
        logger.info(f"  Traitement : {query.name_en} [{disease_key}]")
        logger.info(f"{'─'*60}")

        # Récupération disease_id PostgreSQL
        disease_id = None
        if self.conn:
            disease_id = get_disease_id(self.conn, disease_key)
            if disease_id:
                logger.info(f"  disease_id PostgreSQL : {disease_id}")
            else:
                logger.warning(f"  ⚠️  disease_id non trouvé pour {disease_key} — "
                                "articles insérés sans lien disease")

        # ── ESearch : récupérer les PMIDs ────────────────────────────────────
        all_pmids = []
        retstart = 0
        max_r = query.max_results

        while retstart < max_r:
            batch_size = min(500, max_r - retstart)
            pmids, total = esearch(query.pubmed_query, batch_size, retstart)
            if not pmids:
                break
            all_pmids.extend(pmids)
            stats.query_total = total
            logger.info(f"  ESearch : +{len(pmids)} PMIDs (offset={retstart}, total={total})")

            retstart += len(pmids)
            if len(pmids) < batch_size:
                break   # plus de résultats disponibles
            time.sleep(self.sleep_time)

        # Filtrer les PMIDs déjà en base
        new_pmids = [
            p for p in all_pmids
            if p not in self.existing_pmids
        ]
        logger.info(f"  {len(all_pmids)} PMIDs trouvés — {len(new_pmids)} nouveaux")

        if not new_pmids:
            logger.info("  Aucun nouveau PMID — maladie déjà à jour")
            return stats

        # ── EFetch : récupérer les métadonnées par lots ───────────────────────
        all_records: list[ArticleRecord] = []
        for i in range(0, len(new_pmids), BATCH_SIZE):
            batch = new_pmids[i:i + BATCH_SIZE]
            logger.info(f"  EFetch lot {i//BATCH_SIZE + 1} : {len(batch)} articles...")
            xml_text = efetch_batch(batch)
            records  = parse_pubmed_xml(xml_text, disease_key)
            all_records.extend(records)
            stats.fetched += len(records)
            time.sleep(self.sleep_time)

        logger.info(f"  {stats.fetched} articles parsés")

        # ── Filtre de pertinence ──────────────────────────────────────────────
        relevant_records = []
        for record in all_records:
            score, matched = compute_relevance_score(record, query)
            record.relevance_score   = score
            record.matched_keywords  = matched

            if score >= RELEVANCE_THRESHOLD:
                relevant_records.append(record)
                stats.relevant += 1
            else:
                stats.skipped_low += 1

        logger.info(f"  {stats.relevant} articles pertinents (score ≥ {RELEVANCE_THRESHOLD})")

        # Trier par score décroissant
        relevant_records.sort(key=lambda r: r.relevance_score, reverse=True)

        # ── Insertion en base ─────────────────────────────────────────────────
        if not self.dry_run:
            for record in relevant_records:
                # Vérifier doublon DOI
                if record.doi and record.doi in self.existing_dois:
                    stats.skipped_dup += 1
                    continue

                try:
                    ref_id = insert_article(self.conn, record, disease_id)
                    if ref_id:
                        stats.inserted += 1
                        self.existing_dois.add(record.doi or "")
                        self.existing_pmids.add(record.pmid)
                        if stats.inserted % 20 == 0:
                            self.conn.commit()
                            logger.info(f"  ✓ {stats.inserted} articles insérés...")
                    else:
                        stats.skipped_dup += 1
                except Exception as e:
                    logger.error(f"  Erreur insertion PMID={record.pmid} : {e}")
                    stats.errors += 1
                    self.conn.rollback()

            self.conn.commit()

        else:
            # Dry run : afficher les 5 meilleurs
            logger.info(f"  DRY RUN — top 5 articles (score décroissant) :")
            for r in relevant_records[:5]:
                logger.info(f"    [{r.relevance_score:.3f}] {r.year} | {r.authors[:40]}...")
                logger.info(f"           {r.title[:80]}...")

        logger.info(
            f"  ✅ {disease_key} terminé — "
            f"insérés={stats.inserted}, doublons={stats.skipped_dup}, "
            f"score_bas={stats.skipped_low}, erreurs={stats.errors}"
        )
        return stats

    def _print_final_report(self):
        """Affiche le rapport final."""
        logger.info(f"\n{'═'*60}")
        logger.info(f"  RAPPORT FINAL — Pipeline PubMed PIPOnto")
        logger.info(f"{'═'*60}")
        total_inserted = sum(s.inserted for s in self.all_stats)
        total_fetched  = sum(s.fetched  for s in self.all_stats)
        total_relevant = sum(s.relevant for s in self.all_stats)
        logger.info(f"  Articles récupérés    : {total_fetched}")
        logger.info(f"  Articles pertinents   : {total_relevant}")
        logger.info(f"  Articles insérés en BD: {total_inserted}")
        logger.info(f"{'─'*60}")
        for s in self.all_stats:
            status = "✅" if s.errors == 0 else "⚠️"
            logger.info(
                f"  {status} {s.disease_key:<30} "
                f"insérés={s.inserted:3d}  doublons={s.skipped_dup:3d}  "
                f"bas={s.skipped_low:3d}  err={s.errors}"
            )
        logger.info(f"{'═'*60}\n")

    def _export_csv(self):
        """Exporte un récapitulatif CSV des statistiques."""
        if not self.all_stats:
            return
        csv_path = self.output_dir / f"pipeline_stats_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "disease_key", "query_total", "fetched", "relevant",
                "inserted", "skipped_dup", "skipped_low", "errors"
            ])
            writer.writeheader()
            for s in self.all_stats:
                writer.writerow(asdict(s))
        logger.info(f"  Statistiques exportées : {csv_path}")
