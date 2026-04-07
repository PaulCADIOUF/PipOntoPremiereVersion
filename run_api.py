#!/usr/bin/env python3
"""
run_api.py
===========
Script de lancement de l'API REST PIPOnto.

Usage :
    cd ~/piponto
    source venv/bin/activate
    python3 run_api.py              # développement (port 8000)
    python3 run_api.py --port 8080  # autre port
    python3 run_api.py --prod       # production (workers multiples)
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / "piponto" / ".env")


def check_dependencies():
    missing = []
    for pkg in ["fastapi", "uvicorn", "psycopg2", "scipy", "pydantic"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"❌ Packages manquants : {', '.join(missing)}")
        print(f"   pip install {' '.join(missing)}")
        sys.exit(1)
    print("✅ Dépendances OK")


def check_db():
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "piponto_db"),
            user=os.getenv("DB_USER", "piponto_user"),
            password=os.getenv("DB_PASSWORD", "piponto2025"),
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE validation_status='VALIDATED') AS validated,
                COUNT(*) AS total
            FROM piponto.models
        """)
        validated, total = cur.fetchone()
        conn.close()
        print(f"✅ PostgreSQL OK — {validated} modèles validés / {total} total")
        return True
    except Exception as e:
        print(f"⚠️  PostgreSQL inaccessible : {e}")
        print("   L'API démarrera mais les endpoints BD retourneront des erreurs")
        return False


def main():
    parser = argparse.ArgumentParser(description="Lance l'API PIPOnto")
    parser.add_argument("--port",    type=int, default=8000)
    parser.add_argument("--host",    default="0.0.0.0")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--prod",    action="store_true",
                        help="Mode production (workers=4, no reload)")
    parser.add_argument("--no-check", action="store_true",
                        help="Ignorer les vérifications au démarrage")
    args = parser.parse_args()

    print(f"\n{'═'*55}")
    print(f"  🧬  PIPOnto — API REST FastAPI")
    print(f"{'═'*55}")

    if not args.no_check:
        check_dependencies()
        check_db()

    if args.prod:
        workers = args.workers if args.workers > 1 else 4
        reload  = False
    else:
        workers = 1
        reload  = True

    print(f"\n  Port     : {args.port}")
    print(f"  Mode     : {'production' if args.prod else 'développement'}")
    print(f"  Workers  : {workers}")
    print(f"  Reload   : {reload}")
    print(f"\n  Documentation interactive :")
    print(f"  → http://localhost:{args.port}/docs      (Swagger UI)")
    print(f"  → http://localhost:{args.port}/redoc     (ReDoc)")
    print(f"  → http://localhost:{args.port}/          (Health check)")
    print(f"\n  Logs en temps réel :")
    print(f"  → Ctrl+C pour arrêter")
    print(f"{'═'*55}\n")

    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
