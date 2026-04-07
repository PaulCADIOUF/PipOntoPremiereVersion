"""
pipeline/pdf_extractor.py
==========================
Extracteur PDF automatique pour la bibliothèque PIPOnto.

Flux pour chaque article open access :
    1. Télécharge le PDF (depuis pdf_url ou PMC)
    2. Extrait le texte (pdfminer.six)
    3. Applique les patterns de pdf_patterns.py
    4. Génère une fiche modèle dans piponto.models
    5. Insère les paramètres, compartiments, géographies, populations, mots-clés

Résultat :
    models : fiche PENDING à ~70% complète
    parameters, compartments, geographic_scopes, population_contexts, keywords : pré-remplis

Usage :
    cd ~/piponto/pipeline
    source ~/piponto/venv/bin/activate

    # Test sur 5 articles (dry run)
    python3 pdf_extractor.py --dry-run --limit 5

    # Lancer sur tous les articles OA
    python3 pdf_extractor.py --run

    # Lancer sur une maladie spécifique
    python3 pdf_extractor.py --run --disease COVID19

    # Voir les statistiques après extraction
    python3 pdf_extractor.py --stats
"""

import os
import re
import time
import json
import logging
import argparse
import hashlib
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv

# ── Import modules PIPOnto ────────────────────────────────────────────────────
from pdf_patterns import (
    ExtractedModel, ExtractedParam,
    detect_formalism, detect_model_type, detect_geographies,
    detect_population, detect_compartments, detect_code,
    detect_interventions, extract_parameters,
    build_model_id, build_model_name,
    compute_extraction_confidence,
    COMPARTMENT_DEFINITIONS,
)

# ── Configuration ─────────────────────────────────────────────────────────────
load_dotenv(Path.home() / "piponto" / ".env")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "piponto@research.org")

# Dossier où sauvegarder les PDFs téléchargés
PDF_CACHE_DIR = Path.home() / "piponto" / "data" / "pdfs"
PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
log_dir = Path.home() / "piponto" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_dir / f"pdf_extractor_{datetime.now():%Y%m%d_%H%M%S}.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("piponto.pdf")

import requests


# ══════════════════════════════════════════════════════════════════════════════
# TÉLÉCHARGEMENT PDF
# ══════════════════════════════════════════════════════════════════════════════

PMC_PDF_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
EUROPEPMC_URL = "https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"

HEADERS = {
    "User-Agent": f"PIPOnto/1.0 (mailto:{NCBI_EMAIL}; research tool)",
    "Accept": "application/pdf,*/*",
}


def _get_cache_path(url: str) -> Path:
    """Chemin de cache pour une URL donnée."""
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    return PDF_CACHE_DIR / f"{h}.pdf"


def _get_pmcid_from_pmid(pmid: str) -> Optional[str]:
    """Récupère le PMCID depuis un PMID via l'API NCBI."""
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",
            params={
                "dbfrom": "pubmed", "db": "pmc",
                "id": pmid, "retmode": "json",
                "tool": "PIPOnto", "email": NCBI_EMAIL,
            },
            timeout=15,
        )
        data = resp.json()
        links = data.get("linksets", [{}])[0].get("linksetdbs", [])
        for link in links:
            if link.get("dbto") == "pmc":
                ids = link.get("links", [])
                if ids:
                    return f"PMC{ids[0]}"
        return None
    except Exception:
        return None


def download_pdf(pdf_url: str, pmid: Optional[str] = None) -> Optional[bytes]:
    """
    Télécharge un PDF depuis son URL.
    Essaie d'abord pdf_url, puis Europe PMC si le PMID est disponible.
    Utilise un cache disque pour ne pas re-télécharger.
    Retourne les bytes du PDF ou None si échec.
    """
    # Essai 1 : URL directe depuis la base
    if pdf_url:
        cache = _get_cache_path(pdf_url)
        if cache.exists():
            return cache.read_bytes()

        try:
            resp = requests.get(pdf_url, headers=HEADERS, timeout=30,
                                allow_redirects=True)
            if resp.status_code == 200 and b'%PDF' in resp.content[:10]:
                cache.write_bytes(resp.content)
                return resp.content
        except Exception as e:
            logger.debug(f"Téléchargement direct échoué ({pdf_url[:60]}): {e}")

    # Essai 2 : Europe PMC (via PMID → PMCID)
    if pmid:
        pmcid = _get_pmcid_from_pmid(pmid)
        if pmcid:
            cache = _get_cache_path(pmcid)
            if cache.exists():
                return cache.read_bytes()

            for url_tpl in [EUROPEPMC_URL, PMC_PDF_URL]:
                url = url_tpl.format(pmcid=pmcid)
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=30,
                                        allow_redirects=True)
                    if resp.status_code == 200 and b'%PDF' in resp.content[:10]:
                        cache.write_bytes(resp.content)
                        return resp.content
                except Exception:
                    continue
            time.sleep(0.3)

    return None


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION TEXTE PDF
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extrait le texte d'un PDF en utilisant pdfminer.six.
    Retourne le texte brut, ou chaîne vide si échec.
    """
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        from pdfminer.layout import LAParams

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name

        laparams = LAParams(
            line_overlap=0.5,
            char_margin=2.0,
            line_margin=0.5,
            word_margin=0.1,
        )
        text = pdfminer_extract(tmp_path, laparams=laparams, maxpages=20)
        os.unlink(tmp_path)

        # Nettoyage basique
        text = re.sub(r'\n{3,}', '\n\n', text)   # réduire les lignes vides multiples
        text = re.sub(r'[ \t]+', ' ', text)        # espaces multiples
        return text.strip()

    except ImportError:
        logger.error("pdfminer.six non installé : pip install pdfminer.six")
        return ""
    except Exception as e:
        logger.debug(f"Erreur extraction PDF : {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION COMPLÈTE DEPUIS TEXTE
# ══════════════════════════════════════════════════════════════════════════════

def extract_model_from_text(
    text: str,
    title: str,
    authors: str,
    year: int,
    disease_key: str,
    disease_name: str,
) -> ExtractedModel:
    """
    Applique tous les patterns sur le texte extrait du PDF.
    Retourne un ExtractedModel pré-rempli.
    """
    # Texte combiné titre + contenu (titre amplifié car très informatif)
    full_text = f"{title} {title} {title}\n{text}"

    model = ExtractedModel()

    # 1. Formalisme
    model.formalism, _ = detect_formalism(full_text)

    # 2. Type déterministe/stochastique
    model.model_type = detect_model_type(full_text)

    # 3. Structure spatiale
    spatial_patterns = [
        ("METAPOPULATION", r'\b(?:metapopulation|multi[- ]patch|spatial)\b'),
        ("NETWORK",        r'\bcontact\s+network\b'),
        ("GRID",           r'\b(?:grid|lattice|cellular\s+automata)\b'),
    ]
    for struct, pattern in spatial_patterns:
        if re.search(pattern, full_text, re.IGNORECASE):
            model.spatial_struct = struct
            break

    # 4. Age-structuré ?
    model.is_age_struct = bool(re.search(
        r'\b(?:age[- ]structured|age\s+group|age\s+cohort|'
        r'contact\s+matrix|POLYMOD|age[- ]specific)\b',
        full_text, re.IGNORECASE
    ))

    # 5. Multi-souches ?
    model.is_multi_strain = bool(re.search(
        r'\b(?:variant|strain|serotype|multi[- ]strain)\b',
        full_text, re.IGNORECASE
    ))

    # 6. Interventions ?
    model.has_interventions = detect_interventions(full_text)

    # 7. Paramètres
    model.params = extract_parameters(full_text)

    # 8. Compartiments
    model.compartments = detect_compartments(model.formalism, full_text)

    # 9. Géographie
    geos = detect_geographies(full_text)
    model.countries    = [g["iso"]  for g in geos]
    model.country_names= [g["name"] for g in geos]
    if geos and geos[0]["pop"]:
        model.population_size = geos[0]["pop"]

    # 10. Population
    model.population_type = detect_population(full_text)

    # 11. Code source
    (model.github_url,
     model.zenodo_url,
     platform,
     model.has_code,
     model.code_license) = detect_code(full_text)

    if platform:
        model.platform = platform
    elif model.formalism == "ABM":
        model.platform = "PYTHON"  # Covasim / Mesa défaut
    else:
        model.platform = "PYTHON"  # défaut

    # 12. Score de confiance
    model.extraction_confidence = compute_extraction_confidence(model)

    return model


# ══════════════════════════════════════════════════════════════════════════════
# INSERTION POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════

def get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def get_disease_id(conn, disease_key: str) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT disease_id FROM piponto.diseases WHERE uri_m8 LIKE %s",
            (f"%#{disease_key}",)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "SELECT disease_id FROM piponto.diseases WHERE name_en ILIKE %s",
            (f"%{disease_key.replace('_', ' ')}%",)
        )
        row = cur.fetchone()
        return row[0] if row else None


def make_unique_model_id(conn, base_id: str) -> str:
    """Garantit l'unicité du model_id en ajoutant un suffixe si nécessaire."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model_id FROM piponto.models WHERE model_id LIKE %s",
            (f"{base_id}%",)
        )
        existing = {row[0] for row in cur.fetchall()}

    if base_id not in existing:
        return base_id
    for i in range(2, 20):
        candidate = f"{base_id}_v{i}"
        if candidate not in existing:
            return candidate
    return f"{base_id}_{datetime.now().strftime('%H%M%S')}"


def insert_model(conn, model: ExtractedModel, ref_id: int,
                 disease_id: int, disease_key: str,
                 title: str, authors: str, year: int,
                 dry_run: bool = False) -> Optional[str]:
    """Insère le modèle et toutes ses données liées. Retourne le model_id."""

    base_id = build_model_id(model.formalism, disease_key, authors, year)
    model_id = make_unique_model_id(conn, base_id)

    geo_names = model.country_names[:2] if model.country_names else []
    name = build_model_name(
        model.formalism,
        disease_key.replace("_", " "),
        authors, year, geo_names
    )

    # Sécuriser les valeurs enum avant INSERT
    VALID_PLATFORMS = {'PYTHON','R','MATLAB','GAMA','NETLOGO','REPAST',
                       'MESA','JULIA','C_CPP','JAVA','MATHEMATICA','OTHER'}
    VALID_FORMALISMS = {'SIR','SIS','SIRS','SEIR','SEIRS','SEIS','SEIRD',
                        'SEIRHD','MSIR','MSEIR','ABM','NETWORK','METAPOPULATION',
                        'IBM','STOCHASTIC_SIR','STOCHASTIC_SEIR','BRANCHING_PROCESS',
                        'RENEWAL_EQUATION','BAYESIAN','MIXED','OTHER'}
    VALID_PARAM_TYPES = {'TRANSMISSION_RATE','RECOVERY_RATE','INCUBATION_RATE',
                         'WANING_IMMUNITY_RATE','MORTALITY_RATE','BIRTH_RATE',
                         'NATURAL_DEATH_RATE','VACCINATION_RATE','HOSPITALIZATION_RATE',
                         'R0','SERIAL_INTERVAL','GENERATION_TIME','CASE_FATALITY_RATE',
                         'CONTACT_RATE','VECTOR_BITING_RATE','VECTOR_COMPETENCE','OTHER'}

    if model.platform not in VALID_PLATFORMS:
        logger.debug(f"Platform invalide '{model.platform}' → 'OTHER'")
        model.platform = 'OTHER'
    if model.formalism not in VALID_FORMALISMS:
        logger.debug(f"Formalism invalide '{model.formalism}' → 'OTHER'")
        model.formalism = 'OTHER'
    for p in model.params:
        if p.param_type not in VALID_PARAM_TYPES:
            p.param_type = 'OTHER'

    # URI M2 PIPOnto
    uri_m2 = f"http://www.pacadi.org/these/piponto/module2#{model_id}"

    if dry_run:
        logger.info(f"    [DRY RUN] Would insert: {model_id}")
        logger.info(f"    name={name}")
        logger.info(f"    formalism={model.formalism}, type={model.model_type}")
        logger.info(f"    params={[p.symbol for p in model.params]}")
        logger.info(f"    geos={model.country_names}")
        logger.info(f"    confidence={model.extraction_confidence:.2f}")
        return model_id

    # Chaque insertion dans sa propre transaction
    try:
      with conn.cursor() as cur:
        # ── Table models ─────────────────────────────────────────────────────
        cur.execute("""
            INSERT INTO piponto.models (
                model_id, reference_id, disease_id,
                name, short_name, description,
                formalism, model_type, spatial_structure,
                is_age_structured, is_multi_strain, has_interventions,
                platform, has_code, code_license, implementation_url,
                primary_population,
                is_empirically_validated,
                uri_m2, validation_status,
                extracted_by, extraction_confidence, extraction_notes
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s,
                %s,
                %s, 'PENDING',
                'AUTO_PDF', %s, %s
            )
            ON CONFLICT (model_id) DO NOTHING
            RETURNING model_id
        """, (
            model_id, ref_id, disease_id,
            name[:300], model_id[:100],
            f"Extrait automatiquement depuis : {title[:200]}",
            model.formalism, model.model_type, model.spatial_struct,
            model.is_age_struct, model.is_multi_strain, model.has_interventions,
            model.platform, model.has_code, model.code_license,
            model.github_url or model.zenodo_url,
            model.population_type,
            len(model.params) > 0,   # empirically_validated si des params trouvés
            uri_m2,
            model.extraction_confidence,
            json.dumps(model.extraction_notes[:5]) if model.extraction_notes else None,
        ))
        if not cur.fetchone():
            return None   # conflit model_id

        # ── Table parameters ─────────────────────────────────────────────────
        for p in model.params:
            try:
                cur.execute("""
                    INSERT INTO piponto.parameters (
                        model_id, param_type, symbol,
                        name_en, name_fr,
                        default_value, min_value, max_value,
                        confidence_interval_low, confidence_interval_high,
                        unit, time_unit,
                        is_estimated, estimation_method, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s)
                """, (
                    model_id, p.param_type, p.symbol,
                    None, None,   # noms remplis lors validation manuelle
                    p.value, p.value_min, p.value_max,
                    p.ci_low, p.ci_high,
                    p.unit, p.time_unit,
                    p.is_estimated, "auto_extraction",
                    p.context[:300] if p.context else None,
                ))
            except Exception as e:
                logger.debug(f"Param insert error ({p.symbol}): {e}")

        # ── Table compartments ───────────────────────────────────────────────
        for i, symbol in enumerate(model.compartments):
            if symbol not in COMPARTMENT_DEFINITIONS:
                continue
            name_en, name_fr, is_inf, is_rec, is_dead = COMPARTMENT_DEFINITIONS[symbol]
            try:
                cur.execute("""
                    INSERT INTO piponto.compartments (
                        model_id, symbol, name_en, name_fr,
                        is_infectious, is_recovered, is_dead, ordering
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (model_id, symbol) DO NOTHING
                """, (model_id, symbol, name_en, name_fr,
                      is_inf, is_rec, is_dead, i + 1))
            except Exception as e:
                logger.debug(f"Compartment insert error ({symbol}): {e}")

        # ── Table geographic_scopes ──────────────────────────────────────────
        for i, (iso, name_geo) in enumerate(zip(model.countries, model.country_names)):
            scope_level = "city" if model.population_size and model.population_size < 5_000_000 else "country"
            try:
                cur.execute("""
                    INSERT INTO piponto.geographic_scopes (
                        model_id, scope_level, country_code, country_name,
                        population_size, is_primary_scope
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    model_id, scope_level, iso[:2], name_geo[:100],
                    model.population_size if i == 0 else None,
                    i == 0,  # premier = territoire principal
                ))
            except Exception as e:
                logger.debug(f"Geo scope insert error ({iso}): {e}")

        # ── Table population_contexts ────────────────────────────────────────
        try:
            cur.execute("""
                INSERT INTO piponto.population_contexts (
                    model_id, population_type, description,
                    uri_m2, uri_m8
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                model_id, model.population_type,
                f"Population détectée automatiquement depuis le PDF",
                f"http://www.pacadi.org/these/piponto/module2#Pop_{model.population_type.title()}",
                f"http://www.pacadi.org/these/piponto/module8#{model.population_type.title()}",
            ))
        except Exception as e:
            logger.debug(f"Pop context insert error: {e}")

        # ── Table keywords ────────────────────────────────────────────────────
        keywords_to_insert = []
        # Depuis le formalisme
        keywords_to_insert.append((model.formalism.lower(), 1.0, "auto"))
        # Depuis les compartiments
        for c in model.compartments:
            keywords_to_insert.append((c, 0.8, "auto"))
        # Depuis les pays
        for geo in model.country_names[:3]:
            keywords_to_insert.append((geo.lower(), 0.9, "auto"))
        # Depuis le type de population
        keywords_to_insert.append((model.population_type.lower(), 0.9, "auto"))
        # Paramètres détectés
        for p in model.params:
            keywords_to_insert.append((p.symbol, 0.7, "auto"))

        for kw, weight, source in keywords_to_insert:
            try:
                cur.execute("""
                    INSERT INTO piponto.keywords (model_id, keyword, weight, source)
                    VALUES (%s, %s, %s, %s)
                """, (model_id, kw[:100], weight, source))
            except Exception:
                pass

        # ── Mise à jour code_artifacts si GitHub détecté ─────────────────────
        if model.github_url:
            try:
                cur.execute("""
                    INSERT INTO piponto.code_artifacts (
                        model_id, artifact_type, url,
                        platform, is_runnable
                    ) VALUES (%s, 'repository', %s, %s, TRUE)
                    ON CONFLICT DO NOTHING
                """, (model_id, model.github_url, model.platform))
            except Exception as e:
                logger.debug(f"Code artifact insert error: {e}")

    except Exception as e:
        conn.rollback()
        raise   # re-raise pour que _process_article le capture avec le bon message
    return model_id


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class PDFExtractorPipeline:

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.conn = get_conn()
        self.stats = {
            "total":         0,
            "downloaded":    0,
            "text_ok":       0,
            "models_created":0,
            "params_found":  0,
            "geos_found":    0,
            "code_found":    0,
            "skipped":       0,
            "errors":        0,
        }

    def run(self, disease_filter: Optional[str] = None,
            limit: Optional[int] = None):
        """Lance l'extraction sur tous les articles OA."""

        logger.info(f"\n{'═'*60}")
        logger.info(f"  PDF Extractor — {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        logger.info(f"{'═'*60}")

        # Récupérer les articles OA non encore traités
        articles = self._fetch_articles(disease_filter, limit)
        self.stats["total"] = len(articles)
        logger.info(f"  {len(articles)} articles à traiter")

        for i, article in enumerate(articles, 1):
            try:
                self._process_article(article, i, len(articles))
            except KeyboardInterrupt:
                logger.info("\n⚡ Interruption — arrêt propre")
                break
            except Exception as e:
                import traceback
                logger.error(f"  Erreur article #{i}: {type(e).__name__}: {e}")
                logger.debug(traceback.format_exc())
                self.stats["errors"] += 1
                continue

        if not self.dry_run:
            self.conn.commit()
        self.conn.close()
        self._print_report()

    def _fetch_articles(self, disease_filter=None, limit=None) -> list:
        """Récupère les articles OA sans modèle extrait."""
        with self.conn.cursor() as cur:
            query = """
                SELECT
                    r.reference_id,
                    r.pubmed_id,
                    r.doi,
                    r.title,
                    r.authors,
                    r.year,
                    r.pdf_url,
                    r.abstract,
                    -- Déduire la maladie depuis les mots-clés dans le titre
                    CASE
                        WHEN r.title ILIKE '%COVID%' OR r.title ILIKE '%SARS-CoV%'
                            THEN 'COVID19'
                        WHEN r.title ILIKE '%influenza%' OR r.title ILIKE '%flu%'
                            THEN 'SeasonalInfluenza'
                        WHEN r.title ILIKE '%malaria%' OR r.title ILIKE '%plasmodium%'
                            THEN 'Malaria'
                        WHEN r.title ILIKE '%tuberculosis%' OR r.title ILIKE '% TB %'
                            THEN 'Tuberculosis'
                        WHEN r.title ILIKE '%measles%' OR r.title ILIKE '%rougeole%'
                            THEN 'Measles'
                        WHEN r.title ILIKE '%dengue%'
                            THEN 'Dengue'
                        WHEN r.title ILIKE '%ebola%'
                            THEN 'Ebola'
                        WHEN r.title ILIKE '%HIV%' OR r.title ILIKE '% AIDS%'
                            THEN 'HIV'
                        WHEN r.title ILIKE '%cholera%'
                            THEN 'Cholera'
                        WHEN r.title ILIKE '%mpox%' OR r.title ILIKE '%monkeypox%'
                            THEN 'Mpox'
                        ELSE 'Generic'
                    END AS disease_key
                FROM piponto.model_references r
                WHERE r.open_access = TRUE
                  AND r.pdf_url IS NOT NULL
                  -- Exclure les articles déjà traités
                  AND NOT EXISTS (
                    SELECT 1 FROM piponto.models m
                    WHERE m.reference_id = r.reference_id
                  )
                ORDER BY r.year DESC, r.reference_id
            """
            if limit:
                query += f" LIMIT {limit}"

            cur.execute(query)
            rows = cur.fetchall()

        # Filtrer par maladie si demandé
        if disease_filter:
            rows = [r for r in rows if r[-1] == disease_filter]

        return rows

    def _process_article(self, article, idx: int, total: int):
        """Traite un article : télécharge, extrait, insère."""
        (ref_id, pmid, doi, title, authors,
         year, pdf_url, abstract, disease_key) = article

        logger.info(f"\n  [{idx}/{total}] {year} | {(authors or '')[:40]}...")
        logger.info(f"    {(title or '')[:80]}...")

        # ── 1. Téléchargement PDF ─────────────────────────────────────────────
        pdf_bytes = download_pdf(pdf_url, pmid)
        if not pdf_bytes:
            logger.info(f"    ⚠️  PDF non téléchargeable — extraction depuis abstract")
            # Fallback : utiliser l'abstract comme texte de substitution
            text = abstract or ""
            if len(text) < 100:
                self.stats["skipped"] += 1
                return
        else:
            self.stats["downloaded"] += 1
            logger.info(f"    ✓ PDF téléchargé ({len(pdf_bytes)//1024} Ko)")
            text = extract_text_from_pdf(pdf_bytes)

        if len(text) < 50:
            logger.info(f"    ⚠️  Texte insuffisant ({len(text)} chars) — ignoré")
            self.stats["skipped"] += 1
            return

        self.stats["text_ok"] += 1

        # ── 2. Extraction des données ─────────────────────────────────────────
        disease_name = disease_key.replace("_", " ").replace("19", "-19")
        model = extract_model_from_text(
            text=text,
            title=title or "",
            authors=authors or "Unknown",
            year=year or 2000,
            disease_key=disease_key,
            disease_name=disease_name,
        )

        if model.params:
            self.stats["params_found"] += 1
        if model.countries:
            self.stats["geos_found"] += 1
        if model.has_code:
            self.stats["code_found"] += 1

        logger.info(
            f"    → formalism={model.formalism} | "
            f"params={len(model.params)} | "
            f"geos={model.country_names[:2]} | "
            f"confidence={model.extraction_confidence:.2f}"
        )

        # ── 3. Récupérer disease_id ───────────────────────────────────────────
        disease_id = get_disease_id(self.conn, disease_key)
        if not disease_id:
            # Fallback : utiliser la maladie 1 (générique)
            disease_id = 1
            logger.debug(f"    ⚠️  disease_id non trouvé pour {disease_key} — fallback=1")

        # ── 4. Insertion ──────────────────────────────────────────────────────
        model_id = insert_model(
            conn=self.conn,
            model=model,
            ref_id=ref_id,
            disease_id=disease_id,
            disease_key=disease_key,
            title=title or "",
            authors=authors or "Unknown",
            year=year or 2000,
            dry_run=self.dry_run,
        )

        if model_id:
            self.stats["models_created"] += 1
            if not self.dry_run:
                self.conn.commit()   # commit après chaque article réussi
                if self.stats["models_created"] % 20 == 0:
                    logger.info(f"    ✓ {self.stats['models_created']} modèles insérés")
        else:
            self.stats["skipped"] += 1

        time.sleep(0.5)   # respecter les serveurs PDF

    def _print_report(self):
        logger.info(f"\n{'═'*60}")
        logger.info(f"  RAPPORT FINAL — PDF Extractor")
        logger.info(f"{'═'*60}")
        logger.info(f"  Articles traités      : {self.stats['total']}")
        logger.info(f"  PDFs téléchargés      : {self.stats['downloaded']}")
        logger.info(f"  Texte extrait OK      : {self.stats['text_ok']}")
        logger.info(f"  Modèles créés         : {self.stats['models_created']}")
        logger.info(f"  Avec paramètres (β,γ) : {self.stats['params_found']}")
        logger.info(f"  Avec géographie       : {self.stats['geos_found']}")
        logger.info(f"  Avec code GitHub      : {self.stats['code_found']}")
        logger.info(f"  Ignorés               : {self.stats['skipped']}")
        logger.info(f"  Erreurs               : {self.stats['errors']}")
        logger.info(f"{'═'*60}")
        if self.stats['models_created'] > 0:
            logger.info(f"\n  ✅ Prochaine étape : validation manuelle")
            logger.info(f"     python3 pipeline_runner.py stats")
            logger.info(f"     python3 pipeline_runner.py export")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def cmd_stats(conn):
    """Affiche les stats d'extraction PDF."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total_models,
                SUM(CASE WHEN validation_status='PENDING' THEN 1 END) as pending,
                SUM(CASE WHEN validation_status='VALIDATED' THEN 1 END) as validated,
                SUM(CASE WHEN has_code THEN 1 END) as with_code,
                ROUND(AVG(extraction_confidence)::numeric, 3) as avg_confidence,
                COUNT(DISTINCT formalism) as distinct_formalisms
            FROM piponto.models
        """)
        row = cur.fetchone()
        if row:
            labels = ["Total modèles", "En attente", "Validés",
                      "Avec code", "Confiance moy.", "Formalismes"]
            print(f"\n{'═'*50}")
            print(f"  Modèles extraits — statistiques")
            print(f"{'═'*50}")
            for label, val in zip(labels, row):
                print(f"  {label:<25} : {val}")

        cur.execute("""
            SELECT formalism, COUNT(*) as n
            FROM piponto.models
            GROUP BY formalism
            ORDER BY n DESC
        """)
        print(f"\n  Distribution des formalismes :")
        for formalism, n in cur.fetchall():
            bar = "█" * min(n // 2, 30)
            print(f"  {formalism:<20} : {bar} ({n})")

        cur.execute("""
            SELECT COUNT(*) FROM piponto.parameters
        """)
        n_params = cur.fetchone()[0]
        print(f"\n  Paramètres insérés : {n_params}")
        print(f"{'═'*50}\n")


def main():
    parser = argparse.ArgumentParser(
        description="PIPOnto — Extracteur PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--run",      action="store_true", help="Lancer l'extraction")
    grp.add_argument("--dry-run",  action="store_true", help="Simuler sans insérer")
    grp.add_argument("--stats",    action="store_true", help="Voir statistiques")

    parser.add_argument("--disease", metavar="KEY",
                        help="Filtrer par maladie (ex: COVID19)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Limiter à N articles (test)")
    args = parser.parse_args()

    if args.stats:
        conn = get_conn()
        cmd_stats(conn)
        conn.close()
        return

    pipeline = PDFExtractorPipeline(dry_run=args.dry_run)
    pipeline.run(disease_filter=args.disease, limit=args.limit)


if __name__ == "__main__":
    main()
