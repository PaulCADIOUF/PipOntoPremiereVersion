"""
pipeline/pipeline_runner.py
============================
Interface en ligne de commande pour le pipeline PubMed PIPOnto.

Commandes disponibles :

    # Voir toutes les requêtes configurées
    python3 pipeline_runner.py list

    # Test sans insérer en base (recommandé avant le vrai lancement)
    python3 pipeline_runner.py test
    python3 pipeline_runner.py test --diseases COVID19 Malaria

    # Lancement réel pour une maladie
    python3 pipeline_runner.py run --diseases COVID19

    # Lancement réel pour toutes les maladies (long — plusieurs heures)
    python3 pipeline_runner.py run --all

    # Voir les statistiques de la base actuelle
    python3 pipeline_runner.py stats

    # Exporter tous les articles en CSV (pour validation manuelle)
    python3 pipeline_runner.py export

Usage typique pour commencer :
    1. python3 pipeline_runner.py list          → voir les requêtes
    2. python3 pipeline_runner.py test          → vérifier connexion + dry run
    3. python3 pipeline_runner.py run --diseases COVID19 SeasonalInfluenza
    4. python3 pipeline_runner.py stats         → vérifier ce qui a été inséré
    5. python3 pipeline_runner.py export        → exporter pour validation
"""

import sys
import argparse
import json
import csv
from pathlib import Path
from datetime import datetime

from pubmed_queries import ALL_QUERIES, print_summary
from pubmed_pipeline import PubMedPipeline, get_db_connection, DB_CONFIG


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDES
# ══════════════════════════════════════════════════════════════════════════════

def cmd_list(args):
    """Affiche toutes les requêtes configurées."""
    print_summary()
    print("Utilisez ces clés avec : python3 pipeline_runner.py run --diseases COVID19 Malaria\n")


def cmd_test(args):
    """Test complet : connexion BD + dry run des requêtes demandées."""
    print("\n" + "═"*60)
    print("  TEST DU PIPELINE — DRY RUN (aucune insertion)")
    print("═"*60)

    # Test connexion BD
    print("\n1. Test connexion PostgreSQL...")
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM piponto.diseases")
            n_diseases = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM piponto.model_references")
            n_refs = cur.fetchone()[0]
        conn.close()
        print(f"   ✅ Connexion OK — {n_diseases} maladies, {n_refs} références en base")
    except Exception as e:
        print(f"   ❌ Connexion échouée : {e}")
        print(f"   Vérifiez ~/.env et que PostgreSQL est démarré")
        print(f"   sudo systemctl status postgresql")
        sys.exit(1)

    # Test requêtes PubMed (dry run)
    print("\n2. Test requêtes PubMed (dry run)...")
    diseases = args.diseases if args.diseases else list(ALL_QUERIES.keys())[:3]
    print(f"   Maladies testées : {', '.join(diseases)}")

    pipeline = PubMedPipeline(dry_run=True)
    pipeline.run(diseases)
    print("\n✅ Test terminé. Si tout est vert, lancez le vrai pipeline avec :")
    print(f"   python3 pipeline_runner.py run --diseases {' '.join(diseases)}\n")


def cmd_run(args):
    """Lance le pipeline réel (insère en base)."""
    if args.all:
        diseases = None   # toutes les maladies
        print(f"\n⚠️  Lancement COMPLET — {len(ALL_QUERIES)} maladies")
        print("   Durée estimée : 2-4 heures (selon vitesse réseau et clé API)")
        print("   Appuyez Ctrl+C pour arrêter proprement à tout moment\n")
    else:
        if not args.diseases:
            print("❌ Spécifiez --diseases COVID19 Malaria ...")
            print("   ou --all pour toutes les maladies")
            print("\nClés disponibles :")
            for key in ALL_QUERIES:
                print(f"  {key}")
            sys.exit(1)
        diseases = args.diseases
        print(f"\n▶  Lancement pour : {', '.join(diseases)}")

    pipeline = PubMedPipeline(dry_run=False)
    pipeline.run(diseases)


def cmd_stats(args):
    """Affiche les statistiques de la base de données."""
    print("\n" + "═"*60)
    print("  STATISTIQUES — Bibliothèque PIPOnto")
    print("═"*60)

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:

            # Stats globales
            cur.execute("SELECT * FROM piponto.v_library_stats")
            row = cur.fetchone()
            if row:
                cols = [desc[0] for desc in cur.description]
                print("\n  Stats globales :")
                for col, val in zip(cols, row):
                    print(f"    {col:<35} : {val}")

            # Références par statut
            print("\n  Références par statut de validation :")
            cur.execute("""
                SELECT final_status::text, COUNT(*) as total
                FROM piponto.extraction_log
                GROUP BY final_status
                ORDER BY total DESC
            """)
            for status, count in cur.fetchall():
                print(f"    {status:<20} : {count}")

            # Top 10 revues
            print("\n  Top 10 revues :")
            cur.execute("""
                SELECT journal, COUNT(*) as n
                FROM piponto.model_references
                WHERE journal IS NOT NULL
                GROUP BY journal
                ORDER BY n DESC
                LIMIT 10
            """)
            for journal, n in cur.fetchall():
                print(f"    ({n:3d}) {journal[:60]}")

            # Distribution par année
            print("\n  Distribution temporelle (articles par décennie) :")
            cur.execute("""
                SELECT (year/10)*10 AS decade, COUNT(*) as n
                FROM piponto.model_references
                WHERE year IS NOT NULL AND year > 1960
                GROUP BY decade
                ORDER BY decade
            """)
            for decade, n in cur.fetchall():
                bar = "█" * min(n // 2, 40)
                print(f"    {decade}s : {bar} ({n})")

            # Articles avec GitHub
            cur.execute("""
                SELECT COUNT(*) FROM piponto.model_references
                WHERE github_url IS NOT NULL
            """)
            n_github = cur.fetchone()[0]
            print(f"\n  Articles avec code GitHub disponible : {n_github}")

        conn.close()
        print("\n" + "═"*60 + "\n")

    except Exception as e:
        print(f"❌ Erreur : {e}")
        sys.exit(1)


def cmd_export(args):
    """Exporte tous les articles en CSV pour validation manuelle."""
    output_dir = Path.home() / "piponto" / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:

            # Export model_references complet
            cur.execute("""
                SELECT
                    r.reference_id,
                    r.pmid_export,
                    r.doi,
                    r.title,
                    r.authors,
                    r.journal,
                    r.year,
                    r.github_url,
                    r.open_access,
                    l.final_status,
                    l.confidence_scores
                FROM piponto.model_references r
                LEFT JOIN piponto.extraction_log l
                    ON l.reference_id = r.reference_id
                ORDER BY r.year DESC, r.reference_id
            """)

            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]

        conn.close()

        # CSV principal
        csv_path = output_dir / f"references_export_{timestamp}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(zip(cols, row)))

        print(f"\n✅ Export terminé : {len(rows)} articles")
        print(f"   Fichier : {csv_path}")
        print(f"\n   Colonnes disponibles pour validation manuelle :")
        for col in cols:
            print(f"     - {col}")
        print(f"\n   Ouvrez dans LibreOffice Calc ou Excel pour valider.\n")

    except Exception as e:
        # Hack pour le cas où la colonne s'appelle pubmed_id
        print(f"Note: Ajustement requête export...")
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        r.reference_id,
                        r.pubmed_id,
                        r.doi,
                        r.title,
                        r.authors,
                        r.journal,
                        r.year,
                        r.github_url,
                        r.open_access,
                        COALESCE(l.final_status::text, 'PENDING') AS status,
                        COALESCE(l.confidence_scores::text, '{}') AS confidence
                    FROM piponto.model_references r
                    LEFT JOIN piponto.extraction_log l
                        ON l.reference_id = r.reference_id
                    ORDER BY r.year DESC
                """)
                rows = cur.fetchall()
                cols = [desc[0] for desc in cur.description]
            conn.close()

            csv_path = output_dir / f"references_export_{timestamp}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=cols)
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(zip(cols, [str(v) if v is not None else "" for v in row])))

            print(f"\n✅ Export : {len(rows)} articles → {csv_path}\n")
        except Exception as e2:
            print(f"❌ Erreur export : {e2}")
            sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="PIPOnto — Pipeline PubMed",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="Lister toutes les requêtes configurées")

    # test
    p_test = subparsers.add_parser("test", help="Test dry-run sans insertion")
    p_test.add_argument("--diseases", nargs="+", metavar="KEY",
                        help="Maladies à tester (défaut: 3 premières)")

    # run
    p_run = subparsers.add_parser("run", help="Lancer le pipeline réel")
    grp = p_run.add_mutually_exclusive_group(required=True)
    grp.add_argument("--diseases", nargs="+", metavar="KEY",
                     help="Maladies spécifiques (ex: COVID19 Malaria)")
    grp.add_argument("--all", action="store_true",
                     help="Toutes les maladies")

    # stats
    subparsers.add_parser("stats", help="Statistiques de la base")

    # export
    subparsers.add_parser("export", help="Exporter les articles en CSV")

    args = parser.parse_args()

    dispatch = {
        "list":   cmd_list,
        "test":   cmd_test,
        "run":    cmd_run,
        "stats":  cmd_stats,
        "export": cmd_export,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
