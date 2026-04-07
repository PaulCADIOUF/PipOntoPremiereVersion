"""
pipeline/fix_open_access.py
============================
Corrige le champ open_access pour tous les articles déjà en base.

3 méthodes de détection, dans l'ordre :

    1. PMC ID (PubMed Central)
       Si l'article a un PMCID → full text gratuit sur Europe PMC / PMC
       C'est le signal le plus fiable : ~40% des articles scientifiques récents.

    2. Revues entièrement open-access connues
       Liste des revues gold OA en épidémiologie/modélisation :
       PLoS ONE, Scientific Reports, eLife, BMC*, Frontiers*, etc.
       Détection par nom de revue dans la base.

    3. API Unpaywall (gratuite, aucune clé requise — juste un email)
       https://unpaywall.org/products/api
       Consulte le DOI → retourne si un PDF légal gratuit existe
       Couvre green OA (preprint), hybrid OA, gold OA.
       Limite : 100 000 req/jour gratuit.

Résultat attendu :
    - Avant : ~0 articles open_access=true
    - Après  : ~600-900 articles open_access=true (35-55% typique en épidémiologie)

Usage :
    cd ~/piponto/pipeline
    source ~/piponto/venv/bin/activate

    # Étape 1 : détection rapide (PMC + revues) — sans API, instantané
    python3 fix_open_access.py --method fast

    # Étape 2 : Unpaywall pour les articles non résolus par l'étape 1
    python3 fix_open_access.py --method unpaywall

    # Tout en une fois (recommandé)
    python3 fix_open_access.py --method all

    # Voir les résultats sans modifier la base
    python3 fix_open_access.py --dry-run
"""

import os
import sys
import time
import logging
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / "piponto" / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("piponto.oa_fix")

# ── Config ────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}
NCBI_EMAIL   = os.getenv("NCBI_EMAIL", "piponto@research.org")
UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}?email={email}"

# ══════════════════════════════════════════════════════════════════════════════
# LISTE DES REVUES ENTIÈREMENT OPEN-ACCESS
# ══════════════════════════════════════════════════════════════════════════════

# Revues gold OA connues en épidémiologie / modélisation mathématique
# Détection insensible à la casse, correspondance partielle (ILIKE)
OA_JOURNALS = [
    # PLoS family
    "plos one",
    "plos computational biology",
    "plos medicine",
    "plos neglected tropical diseases",
    "plos pathogens",
    # BMC family
    "bmc infectious diseases",
    "bmc medicine",
    "bmc public health",
    "malaria journal",
    "parasites & vectors",
    # Scientific Reports / Nature family OA
    "scientific reports",
    "communications medicine",
    "npj digital medicine",
    # Frontiers family
    "frontiers in public health",
    "frontiers in epidemiology",
    "frontiers in medicine",
    "frontiers in microbiology",
    # eLife
    "elife",
    # Infectious Disease Modelling (Elsevier OA)
    "infectious disease modelling",
    # Journal of Mathematical Biology (hybrid mais souvent OA)
    # Eurosurveillance (ECDC — gold OA)
    "eurosurveillance",
    # Wellcome Open Research
    "wellcome open research",
    # F1000 Research
    "f1000research",
    # medRxiv / bioRxiv (preprints — green OA)
    "medrxiv",
    "biorxiv",
    # MDPI family
    "viruses",
    "pathogens",
    "vaccines",
    "international journal of environmental research and public health",
    "mathematics",
    # Gates Open Research
    "gates open research",
    # Tropical Medicine and Infectious Disease (MDPI)
    "tropical medicine and infectious disease",
]

# ══════════════════════════════════════════════════════════════════════════════
# CONNEXION BD
# ══════════════════════════════════════════════════════════════════════════════

def get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def fetch_articles_to_check(conn, only_false: bool = True):
    """
    Retourne les articles à vérifier.
    only_false=True : seulement ceux marqués open_access=false (défaut)
    only_false=False : tous les articles
    """
    with conn.cursor() as cur:
        if only_false:
            cur.execute("""
                SELECT reference_id, pubmed_id, doi, journal, title
                FROM piponto.model_references
                WHERE open_access = FALSE
                ORDER BY reference_id
            """)
        else:
            cur.execute("""
                SELECT reference_id, pubmed_id, doi, journal, title
                FROM piponto.model_references
                ORDER BY reference_id
            """)
        return cur.fetchall()


def update_open_access(conn, reference_id: int, is_oa: bool,
                       pdf_url: str = None, dry_run: bool = False) -> bool:
    """Met à jour open_access (et pdf_url si disponible)."""
    if dry_run:
        return True
    with conn.cursor() as cur:
        if pdf_url:
            cur.execute("""
                UPDATE piponto.model_references
                SET open_access = %s, pdf_url = %s
                WHERE reference_id = %s
            """, (is_oa, pdf_url, reference_id))
        else:
            cur.execute("""
                UPDATE piponto.model_references
                SET open_access = %s
                WHERE reference_id = %s
            """, (is_oa, reference_id))
    return True


# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 1 : PMC ID via ESearch NCBI
# ══════════════════════════════════════════════════════════════════════════════

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def check_pmc_batch(pmids: list[str]) -> set[str]:
    """
    Vérifie si une liste de PMIDs ont un article dans PubMed Central (= open access).
    Retourne l'ensemble des PMIDs qui ont un PMCID.
    """
    if not pmids:
        return set()

    # EFetch pour récupérer les ArticleId de type 'pmc'
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "PIPOnto",
        "email": NCBI_EMAIL,
    }
    api_key = os.getenv("PUBMED_API_KEY", "")
    if api_key:
        params["api_key"] = api_key

    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=30)
        resp.raise_for_status()

        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)

        oa_pmids = set()
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            if pmid_elem is None:
                continue
            pmid = pmid_elem.text

            # Chercher un ArticleId de type 'pmc'
            for aid in article.findall(".//ArticleId[@IdType='pmc']"):
                if aid.text:
                    oa_pmids.add(pmid)
                    break

            # Aussi chercher dans OtherID
            for oid in article.findall(".//OtherID"):
                if oid.text and oid.text.startswith("PMC"):
                    oa_pmids.add(pmid)
                    break

        return oa_pmids
    except Exception as e:
        logger.warning(f"Erreur PMC check : {e}")
        return set()


# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 2 : REVUES OA CONNUES
# ══════════════════════════════════════════════════════════════════════════════

def check_oa_journal(journal: str) -> bool:
    """Vérifie si la revue est dans la liste des revues entièrement OA."""
    if not journal:
        return False
    journal_lower = journal.lower()
    return any(oa_j in journal_lower for oa_j in OA_JOURNALS)


# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 3 : API UNPAYWALL
# ══════════════════════════════════════════════════════════════════════════════

def check_unpaywall(doi: str) -> tuple[bool, str]:
    """
    Interroge l'API Unpaywall pour savoir si un PDF légal gratuit existe.
    Retourne (is_open_access, pdf_url).

    API Unpaywall :
        - Gratuite, pas de clé requise
        - Limite : 100 000 req/jour
        - Rate limit recommandé : 10 req/seconde max
        - Documentation : https://unpaywall.org/products/api
    """
    if not doi:
        return False, None

    url = UNPAYWALL_URL.format(doi=doi.strip(), email=NCBI_EMAIL)
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            return False, None   # DOI non trouvé dans Unpaywall
        if resp.status_code == 422:
            return False, None   # DOI invalide

        resp.raise_for_status()
        data = resp.json()

        is_oa = data.get("is_oa", False)
        pdf_url = None

        if is_oa:
            # Chercher le meilleur lien PDF
            best_oa = data.get("best_oa_location", {})
            if best_oa:
                pdf_url = best_oa.get("url_for_pdf") or best_oa.get("url")

            # Si pas de best_oa_location, chercher dans oa_locations
            if not pdf_url:
                for loc in data.get("oa_locations", []):
                    if loc.get("url_for_pdf"):
                        pdf_url = loc["url_for_pdf"]
                        break

        return is_oa, pdf_url

    except requests.exceptions.Timeout:
        logger.debug(f"Timeout Unpaywall pour DOI: {doi}")
        return False, None
    except Exception as e:
        logger.debug(f"Erreur Unpaywall {doi}: {e}")
        return False, None


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class OpenAccessFixer:

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.conn = get_conn()
        self.stats = {
            "total":        0,
            "already_oa":   0,
            "fixed_pmc":    0,
            "fixed_journal":0,
            "fixed_unpaywall": 0,
            "still_closed": 0,
        }

    def run(self, method: str = "all"):
        """
        method : "fast" | "unpaywall" | "all"
            fast       = PMC + revues OA (sans appel Unpaywall — rapide)
            unpaywall  = Unpaywall uniquement pour ceux non résolus
            all        = fast puis unpaywall (recommandé)
        """
        logger.info(f"\n{'═'*60}")
        logger.info(f"  Open Access Fixer — méthode={method} dry_run={self.dry_run}")
        logger.info(f"{'═'*60}")

        articles = fetch_articles_to_check(self.conn, only_false=True)
        self.stats["total"] = len(articles)
        logger.info(f"  {len(articles)} articles à vérifier (open_access=FALSE)")

        if not articles:
            logger.info("  Rien à faire — tous déjà marqués.")
            return

        unresolved = []   # articles non résolus par méthode fast

        # ── Méthode fast : PMC + revues ───────────────────────────────────────
        if method in ("fast", "all"):
            logger.info(f"\n  ── Étape 1 : PMC ID + revues OA connues ──")

            # Check revues (instantané, pas de réseau)
            journal_oa_count = 0
            pmids_to_check = []

            for ref_id, pmid, doi, journal, title in articles:
                if check_oa_journal(journal):
                    update_open_access(self.conn, ref_id, True, dry_run=self.dry_run)
                    self.stats["fixed_journal"] += 1
                    journal_oa_count += 1
                else:
                    if pmid:
                        pmids_to_check.append((ref_id, pmid, doi, journal, title))
                    else:
                        unresolved.append((ref_id, pmid, doi, journal, title))

            if not self.dry_run:
                self.conn.commit()
            logger.info(f"  Revues OA connues : {journal_oa_count} articles détectés")

            # Check PMC par lots de 50
            pmc_oa_count = 0
            for i in range(0, len(pmids_to_check), 50):
                batch = pmids_to_check[i:i+50]
                pmid_list = [row[1] for row in batch if row[1]]

                oa_pmids = check_pmc_batch(pmid_list)
                time.sleep(0.4)   # respect limite NCBI

                for ref_id, pmid, doi, journal, title in batch:
                    if pmid in oa_pmids:
                        # Construire l'URL PMC
                        pmc_url = None
                        update_open_access(self.conn, ref_id, True, pmc_url,
                                           dry_run=self.dry_run)
                        self.stats["fixed_pmc"] += 1
                        pmc_oa_count += 1
                    else:
                        unresolved.append((ref_id, pmid, doi, journal, title))

                if not self.dry_run and i % 200 == 0:
                    self.conn.commit()

                if i % 200 == 0:
                    logger.info(f"  PMC : {i+len(batch)}/{len(pmids_to_check)} vérifiés "
                                f"({pmc_oa_count} OA trouvés)...")

            if not self.dry_run:
                self.conn.commit()
            logger.info(f"  PMC Central : {pmc_oa_count} articles détectés")
            logger.info(f"  Après étape 1 : {len(unresolved)} articles non résolus")

        else:
            # Si méthode=unpaywall uniquement, tout est non résolu
            unresolved = articles

        # ── Méthode Unpaywall ─────────────────────────────────────────────────
        if method in ("unpaywall", "all") and unresolved:
            logger.info(f"\n  ── Étape 2 : API Unpaywall ({len(unresolved)} articles) ──")
            logger.info(f"  Durée estimée : ~{len(unresolved) // 10 // 60 + 1} minutes")

            unpaywall_count = 0
            for idx, (ref_id, pmid, doi, journal, title) in enumerate(unresolved, 1):
                if not doi:
                    self.stats["still_closed"] += 1
                    continue

                is_oa, pdf_url = check_unpaywall(doi)

                if is_oa:
                    update_open_access(self.conn, ref_id, True, pdf_url,
                                       dry_run=self.dry_run)
                    self.stats["fixed_unpaywall"] += 1
                    unpaywall_count += 1
                else:
                    self.stats["still_closed"] += 1

                # Progression + commit périodique
                if idx % 50 == 0:
                    logger.info(f"  Unpaywall : {idx}/{len(unresolved)} "
                                f"({unpaywall_count} OA trouvés)...")
                    if not self.dry_run:
                        self.conn.commit()

                time.sleep(0.12)   # ~8 req/s — safe pour Unpaywall

            if not self.dry_run:
                self.conn.commit()
            logger.info(f"  Unpaywall : {unpaywall_count} articles OA détectés")

        # ── Rapport final ─────────────────────────────────────────────────────
        self._print_report()
        self.conn.close()

    def _print_report(self):
        total_fixed = (self.stats["fixed_pmc"] +
                       self.stats["fixed_journal"] +
                       self.stats["fixed_unpaywall"])
        oa_rate = total_fixed / max(self.stats["total"], 1) * 100

        logger.info(f"\n{'═'*60}")
        logger.info(f"  RAPPORT FINAL — Open Access Fix")
        logger.info(f"{'═'*60}")
        logger.info(f"  Articles vérifiés     : {self.stats['total']}")
        logger.info(f"  ── via revues OA      : {self.stats['fixed_journal']}")
        logger.info(f"  ── via PMC Central    : {self.stats['fixed_pmc']}")
        logger.info(f"  ── via Unpaywall      : {self.stats['fixed_unpaywall']}")
        logger.info(f"  Total open access     : {total_fixed} ({oa_rate:.0f}%)")
        logger.info(f"  Toujours fermés       : {self.stats['still_closed']}")
        logger.info(f"{'═'*60}")
        if self.dry_run:
            logger.info(f"  ⚠️  DRY RUN — aucune modification en base")
        else:
            logger.info(f"  ✅ Base mise à jour")

        logger.info(f"\n  Pour télécharger les PDFs OA :")
        logger.info(f"  → python3 pipeline_runner.py export   (CSV avec pdf_url)")
        logger.info(f"  → Les PDFs OA seront téléchargés par l'extracteur PDF")


# ══════════════════════════════════════════════════════════════════════════════
# VÉRIFICATION : colonne pdf_url existe ?
# ══════════════════════════════════════════════════════════════════════════════

def ensure_pdf_url_column(conn):
    """Ajoute la colonne pdf_url si elle n'existe pas encore."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'piponto'
              AND table_name = 'model_references'
              AND column_name = 'pdf_url'
        """)
        if not cur.fetchone():
            logger.info("  Ajout colonne pdf_url dans model_references...")
            cur.execute("""
                ALTER TABLE piponto.model_references
                ADD COLUMN pdf_url TEXT DEFAULT NULL
            """)
            cur.execute("""
                COMMENT ON COLUMN piponto.model_references.pdf_url IS
                'URL directe du PDF open access (depuis Unpaywall ou PMC)'
            """)
            conn.commit()
            logger.info("  ✅ Colonne pdf_url ajoutée")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="PIPOnto — Correcteur Open Access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--method",
        choices=["fast", "unpaywall", "all"],
        default="all",
        help=(
            "fast=PMC+revues (rapide, ~2min) | "
            "unpaywall=API Unpaywall (~15min) | "
            "all=les deux (recommandé)"
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule sans modifier la base"
    )
    args = parser.parse_args()

    # Vérifier/créer la colonne pdf_url
    conn = get_conn()
    ensure_pdf_url_column(conn)
    conn.close()

    fixer = OpenAccessFixer(dry_run=args.dry_run)
    fixer.run(method=args.method)


if __name__ == "__main__":
    main()
