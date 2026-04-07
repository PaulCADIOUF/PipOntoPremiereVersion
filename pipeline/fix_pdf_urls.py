"""
pipeline/fix_pdf_urls.py
=========================
Trouve les pdf_url manquants pour les 1179 articles open_access=TRUE sans url.

Stratégie en 3 passes :
    1. Europe PMC  → PMCID connu → URL directe  (~60% des cas)
    2. NCBI EFetch → chercher PMCID depuis PMID  (~20% supplémentaires)
    3. Unpaywall   → DOI → URL PDF               (~10% supplémentaires)

Résultat attendu : ~800-1000 articles avec pdf_url utilisables

Usage :
    python3 fix_pdf_urls.py --method all
    python3 fix_pdf_urls.py --method europepmc   # rapide, ~5 min
    python3 fix_pdf_urls.py --method unpaywall   # lent, ~30 min
    python3 fix_pdf_urls.py --dry-run
"""

import os, re, time, logging, argparse, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / "piponto" / ".env")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("piponto.fix_urls")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}
NCBI_EMAIL  = os.getenv("NCBI_EMAIL", "piponto@research.org")
NCBI_APIKEY = os.getenv("PUBMED_API_KEY", "")

HEADERS = {"User-Agent": f"PIPOnto/1.0 (mailto:{NCBI_EMAIL})"}


def get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def fetch_articles_without_url(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT reference_id, pubmed_id, doi
            FROM piponto.model_references
            WHERE open_access = TRUE
              AND pdf_url IS NULL
            ORDER BY reference_id
        """)
        return cur.fetchall()


def update_pdf_url(conn, ref_id, pdf_url, dry_run=False):
    if dry_run:
        return
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE piponto.model_references
            SET pdf_url = %s WHERE reference_id = %s
        """, (pdf_url, ref_id))


# ── PASSE 1 : Europe PMC via PMID ────────────────────────────────────────────

EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_PDF = "https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"

def get_pmcid_batch(pmids: list[str]) -> dict[str, str]:
    """Retourne {pmid: pmcid} pour une liste de PMIDs via Europe PMC."""
    if not pmids:
        return {}
    query = " OR ".join(f"EXT_ID:{p}" for p in pmids)
    try:
        resp = requests.get(EPMC_URL, params={
            "query": query, "format": "json",
            "resultType": "core", "pageSize": len(pmids),
        }, headers=HEADERS, timeout=20)
        data = resp.json()
        result = {}
        for article in data.get("resultList", {}).get("result", []):
            pmid  = article.get("pmid")
            pmcid = article.get("pmcid")
            if pmid and pmcid:
                result[str(pmid)] = pmcid
        return result
    except Exception as e:
        logger.debug(f"Europe PMC batch error: {e}")
        return {}


# ── PASSE 2 : NCBI EFetch pour PMCID ─────────────────────────────────────────

def get_pmcid_ncbi(pmids: list[str]) -> dict[str, str]:
    """Via NCBI elink : PMID → PMCID."""
    if not pmids:
        return {}
    import xml.etree.ElementTree as ET
    params = {
        "dbfrom": "pubmed", "db": "pmc",
        "id": ",".join(pmids), "retmode": "xml",
        "tool": "PIPOnto", "email": NCBI_EMAIL,
    }
    if NCBI_APIKEY:
        params["api_key"] = NCBI_APIKEY
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",
            params=params, timeout=20)
        root = ET.fromstring(resp.text)
        result = {}
        for linkset in root.findall(".//LinkSet"):
            id_elem = linkset.find(".//IdList/Id")
            if id_elem is None:
                continue
            pmid = id_elem.text
            for link in linkset.findall(".//LinkSetDb[DbTo='pmc']/Link/Id"):
                result[pmid] = f"PMC{link.text}"
                break
        return result
    except Exception as e:
        logger.debug(f"NCBI elink error: {e}")
        return {}


# ── PASSE 3 : Unpaywall ───────────────────────────────────────────────────────

def get_unpaywall_url(doi: str) -> str | None:
    try:
        resp = requests.get(
            f"https://api.unpaywall.org/v2/{doi.strip()}",
            params={"email": NCBI_EMAIL},
            headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("is_oa"):
            return None
        best = data.get("best_oa_location", {})
        return best.get("url_for_pdf") or best.get("url")
    except Exception:
        return None


# ── PIPELINE PRINCIPAL ────────────────────────────────────────────────────────

def run(method="all", dry_run=False):
    conn = get_conn()
    articles = fetch_articles_without_url(conn)
    logger.info(f"Articles sans pdf_url : {len(articles)}")

    stats = {"epmc": 0, "ncbi": 0, "unpaywall": 0, "failed": 0}

    # ── Passe 1 + 2 : PMC ────────────────────────────────────────────────────
    if method in ("europepmc", "all"):
        logger.info("── Passe 1 : Europe PMC (par lots de 50) ──")
        pmid_articles = [(ref_id, pmid, doi)
                         for ref_id, pmid, doi in articles if pmid]
        unresolved = []

        for i in range(0, len(pmid_articles), 50):
            batch = pmid_articles[i:i+50]
            pmids = [str(r[1]) for r in batch]

            # Europe PMC d'abord
            pmcid_map = get_pmcid_batch(pmids)
            # NCBI en fallback
            if len(pmcid_map) < len(pmids):
                missing = [p for p in pmids if p not in pmcid_map]
                pmcid_map.update(get_pmcid_ncbi(missing))

            for ref_id, pmid, doi in batch:
                pmcid = pmcid_map.get(str(pmid))
                if pmcid:
                    pdf_url = EPMC_PDF.format(pmcid=pmcid)
                    update_pdf_url(conn, ref_id, pdf_url, dry_run)
                    stats["epmc"] += 1
                else:
                    unresolved.append((ref_id, pmid, doi))

            if not dry_run and i % 200 == 0:
                conn.commit()

            if i % 200 == 0:
                logger.info(f"  PMC : {i+len(batch)}/{len(pmid_articles)} "
                            f"({stats['epmc']} URLs trouvées)")
            time.sleep(0.3)

        if not dry_run:
            conn.commit()
        logger.info(f"  → {stats['epmc']} URLs via Europe PMC/NCBI")

        # Mettre à jour la liste des articles sans URL
        articles = unresolved + [(ref_id, pmid, doi)
                                  for ref_id, pmid, doi in articles if not pmid]

    # ── Passe 3 : Unpaywall ──────────────────────────────────────────────────
    if method in ("unpaywall", "all"):
        doi_articles = [(ref_id, pmid, doi)
                        for ref_id, pmid, doi in articles if doi]
        logger.info(f"── Passe 2 : Unpaywall ({len(doi_articles)} articles avec DOI) ──")
        logger.info(f"  Durée estimée : ~{len(doi_articles)//8//60+1} min")

        for idx, (ref_id, pmid, doi) in enumerate(doi_articles, 1):
            pdf_url = get_unpaywall_url(doi)
            if pdf_url:
                update_pdf_url(conn, ref_id, pdf_url, dry_run)
                stats["unpaywall"] += 1
            else:
                stats["failed"] += 1

            if idx % 50 == 0:
                logger.info(f"  Unpaywall : {idx}/{len(doi_articles)} "
                            f"({stats['unpaywall']} URLs trouvées)")
                if not dry_run:
                    conn.commit()
            time.sleep(0.13)

        if not dry_run:
            conn.commit()
        logger.info(f"  → {stats['unpaywall']} URLs via Unpaywall")

    total = stats["epmc"] + stats["ncbi"] + stats["unpaywall"]
    logger.info(f"\n{'═'*55}")
    logger.info(f"  RAPPORT — fix_pdf_urls")
    logger.info(f"{'═'*55}")
    logger.info(f"  Via Europe PMC/NCBI  : {stats['epmc']}")
    logger.info(f"  Via Unpaywall        : {stats['unpaywall']}")
    logger.info(f"  Total URLs ajoutées  : {total}")
    logger.info(f"  Sans URL (payant)    : {stats['failed']}")
    if dry_run:
        logger.info(f"  ⚠️  DRY RUN — aucune modification")
    logger.info(f"\n  Prochaine étape :")
    logger.info(f"  python3 pdf_extractor.py --run")
    logger.info(f"{'═'*55}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--method",
        choices=["europepmc","unpaywall","all"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(method=args.method, dry_run=args.dry_run)
