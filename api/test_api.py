"""
api/test_api.py
================
Script de test complet pour l'API PIPOnto.
Teste les 7 endpoints avec des exemples réels.

Usage :
    # L'API doit être lancée avant
    uvicorn api.main:app --port 8000

    # Puis dans un autre terminal
    cd ~/piponto
    source venv/bin/activate
    python3 api/test_api.py

    # Tester un seul endpoint
    python3 api/test_api.py --endpoint search
    python3 api/test_api.py --endpoint simulate
"""

import sys
import json
import time
import argparse
import requests
from datetime import datetime

BASE = "http://localhost:8000"
OK   = "✅"
FAIL = "❌"
WARN = "⚠️ "

passed = 0
failed = 0
results = []


def test(name, method, path, body=None, params=None,
         expect_status=200, expect_keys=None, description=""):
    global passed, failed
    url = f"{BASE}{path}"
    try:
        t0 = time.time()
        if method == "GET":
            resp = requests.get(url, params=params, timeout=10)
        else:
            resp = requests.post(url, json=body, timeout=15)
        elapsed = round((time.time() - t0) * 1000)

        ok = resp.status_code == expect_status
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        # Vérifier les clés attendues
        key_ok = True
        if expect_keys and ok:
            for key in expect_keys:
                if key not in data:
                    key_ok = False
                    print(f"     {WARN} Clé manquante : '{key}'")

        status = OK if (ok and key_ok) else FAIL
        if ok and key_ok:
            passed += 1
        else:
            failed += 1

        print(f"  {status}  {name:<45} [{resp.status_code}]  {elapsed}ms")
        if description:
            print(f"       → {description}")

        # Afficher un résumé de la réponse
        if ok and data:
            _preview(data)

        results.append({
            "name": name, "status": resp.status_code,
            "ok": ok and key_ok, "ms": elapsed
        })
        return data

    except requests.exceptions.ConnectionError:
        print(f"  {FAIL}  {name}")
        print(f"       → ERREUR : Impossible de se connecter à {BASE}")
        print(f"       → Vérifiez que l'API est lancée :")
        print(f"         cd ~/piponto && uvicorn api.main:app --port 8000")
        failed += 1
        sys.exit(1)
    except Exception as e:
        print(f"  {FAIL}  {name} — {e}")
        failed += 1
        return {}


def _preview(data):
    """Affiche un aperçu concis de la réponse."""
    if isinstance(data, dict):
        for key, val in list(data.items())[:4]:
            if isinstance(val, list):
                print(f"       {key}: [{len(val)} éléments]")
            elif isinstance(val, dict):
                print(f"       {key}: {{...}}")
            elif isinstance(val, str) and len(val) > 80:
                print(f"       {key}: {val[:77]}...")
            else:
                print(f"       {key}: {val}")


def separator(title):
    print(f"\n  {'─'*60}")
    print(f"  {title}")
    print(f"  {'─'*60}")


# ══════════════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════════════

def run_tests(endpoint_filter=None):
    print(f"\n{'═'*65}")
    print(f"  PIPOnto API — Tests automatiques")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  {BASE}")
    print(f"{'═'*65}")

    # ── 1. SANTÉ ─────────────────────────────────────────────────────────────
    if not endpoint_filter or endpoint_filter == "health":
        separator("Endpoint 1 : GET /  (santé)")
        data = test(
            "Santé de l'API",
            "GET", "/",
            expect_keys=["status", "db_connected", "validated_models"],
            description="Connexion BD + comptage modèles validés"
        )
        if data.get("status") == "ok":
            print(f"       🗄️  BD connectée : {data.get('db_connected')}")
            print(f"       📚 Modèles validés : {data.get('validated_models')}")

    # ── 2. STATS ──────────────────────────────────────────────────────────────
    if not endpoint_filter or endpoint_filter == "stats":
        separator("Endpoint 2 : GET /stats  (statistiques)")
        data = test(
            "Statistiques globales",
            "GET", "/stats",
            expect_keys=["validated_models", "by_formalism", "by_disease", "top_countries"],
            description="Distribution par formalisme, maladie, pays"
        )
        if data.get("by_formalism"):
            top = sorted(data["by_formalism"].items(), key=lambda x: -x[1])[:3]
            print(f"       Top formalismes : " +
                  ", ".join(f"{k}={v}" for k, v in top))
        if data.get("by_disease"):
            top_d = data["by_disease"][:3]
            print(f"       Top maladies : " +
                  ", ".join(f"{d['disease']}={d['count']}" for d in top_d))

    # ── 3. MALADIES ───────────────────────────────────────────────────────────
    if not endpoint_filter or endpoint_filter == "diseases":
        separator("Endpoint 3 : GET /diseases  (maladies)")
        data = test(
            "Liste toutes les maladies",
            "GET", "/diseases",
            expect_keys=["diseases", "total"],
            description="24 maladies OMS avec caractéristiques"
        )
        test(
            "Filtrer maladies avec modèles",
            "GET", "/diseases",
            params={"with_models_only": "true"},
            description="Uniquement les maladies ayant ≥1 modèle validé"
        )
        if data.get("diseases"):
            first = data["diseases"][0]
            print(f"       Exemple : {first.get('name_en')} "
                  f"({first.get('model_count')} modèles)")

    # ── 4. RECHERCHE ──────────────────────────────────────────────────────────
    if not endpoint_filter or endpoint_filter == "search":
        separator("Endpoint 4 : GET /models/search  (recherche)")

        # Recherche basique
        test(
            "Recherche par maladie : COVID-19",
            "GET", "/models/search",
            params={"disease": "COVID", "limit": 5},
            expect_keys=["models", "total"],
            description="Tous les modèles COVID-19"
        )
        # SEIR seulement
        data = test(
            "Recherche SEIR + pays France",
            "GET", "/models/search",
            params={"formalism": "SEIR", "country": "FR", "limit": 5},
            description="Modèles SEIR validés pour la France"
        )
        # Avec code
        test(
            "Modèles avec code disponible",
            "GET", "/models/search",
            params={"has_code": "true", "limit": 10},
            description="has_code=true → 35 dépôts GitHub"
        )
        # Stochastiques
        test(
            "Modèles stochastiques",
            "GET", "/models/search",
            params={"model_type": "STOCHASTIC", "limit": 5},
            description="Type STOCHASTIC uniquement"
        )
        # ABM Sénégal
        test(
            "ABM validés empiriquement",
            "GET", "/models/search",
            params={"formalism": "ABM", "empirical": "true"},
            description="Agent-Based Models avec validation empirique"
        )
        # Pagination
        test(
            "Pagination (page 2)",
            "GET", "/models/search",
            params={"limit": 10, "offset": 10},
            description="Offset=10 pour simuler la page 2"
        )
        # Sans résultats — ne doit pas planter
        test(
            "Recherche sans résultats (maladie fictive)",
            "GET", "/models/search",
            params={"disease": "MALADIE_INEXISTANTE_XYZ"},
            expect_keys=["models", "total"],
            description="Doit retourner models=[] total=0"
        )

        # Récupérer un model_id réel pour les tests suivants
        all_models = test(
            "Récupérer la liste complète (sans filtre)",
            "GET", "/models/search",
            params={"limit": 3},
        )
        model_id = None
        if all_models.get("models"):
            model_id = all_models["models"][0]["model_id"]
            print(f"       🔑 model_id pour tests suivants : {model_id}")

        return model_id

    return None


def run_detail_tests(model_id):
    if not model_id:
        print(f"\n  {WARN} Pas de model_id disponible — tests detail/params ignorés")
        return

    # ── 5. DÉTAIL ─────────────────────────────────────────────────────────────
    separator(f"Endpoint 5 : GET /models/{{id}}  (détail)")
    data = test(
        f"Fiche complète : {model_id[:40]}",
        "GET", f"/models/{model_id}",
        expect_keys=["model_id", "formalism", "parameters",
                     "compartments", "geographic_scopes"],
        description="Tous les champs + paramètres + géos"
    )
    if data.get("parameters"):
        syms = [p["symbol"] for p in data["parameters"]]
        print(f"       Paramètres : {', '.join(syms)}")
    if data.get("geographic_scopes"):
        codes = [g["country_code"] for g in data["geographic_scopes"] if g.get("country_code")]
        print(f"       Pays : {', '.join(codes[:5])}")

    # Modèle inexistant → 404
    test(
        "Modèle inexistant → 404",
        "GET", "/models/MODELE_FICTIF_XYZ_2099",
        expect_status=404,
        description="Doit retourner HTTP 404"
    )

    # ── 6. PARAMÈTRES ────────────────────────────────────────────────────────
    separator(f"Endpoint 6 : GET /models/{{id}}/params")
    data = test(
        f"Paramètres : {model_id[:40]}",
        "GET", f"/models/{model_id}/params",
        expect_keys=["simulation_ready", "simulation_dict"],
        description="Paramètres formatés pour simulation"
    )
    if data.get("simulation_dict"):
        d = data["simulation_dict"]
        items = list(d.items())[:3]
        print(f"       simulation_dict : " +
              ", ".join(f"{k}={v.get('value') if isinstance(v,dict) else v}"
                        for k, v in items))
    print(f"       simulation_ready : {data.get('simulation_ready')}")
    if data.get("missing_for_sim"):
        print(f"       Manquants : {data.get('missing_for_sim')}")

    return data


def run_simulate_tests(model_id, params_data=None):
    # ── 7. SIMULATION ─────────────────────────────────────────────────────────
    separator("Endpoint 7 : POST /simulate  (simulation)")

    # Mode 1 : Paramètres manuels SEIR
    data = test(
        "SEIR manuel — COVID standard",
        "POST", "/simulate",
        body={
            "formalism": "SEIR",
            "N": 1_000_000,
            "beta": 0.31,
            "gamma": 0.143,
            "sigma": 0.196,
            "I0": 10,
            "days": 365,
        },
        expect_keys=["peak_infected", "peak_day", "attack_rate",
                     "R0_effective", "time_series", "summary"],
        description="β=0.31, γ=0.143, σ=0.196 — valeurs COVID typiques"
    )
    if data.get("summary"):
        print(f"       {data['summary']}")

    # Mode 1b : SIR simple
    test(
        "SIR manuel — grippe saisonnière",
        "POST", "/simulate",
        body={
            "formalism": "SIR",
            "N": 500_000,
            "beta": 0.5,
            "gamma": 0.25,
            "I0": 5,
            "days": 180,
        },
        description="R₀=2.0 grippe — durée 180 jours"
    )

    # Mode 1c : SEIRD avec mortalité
    data2 = test(
        "SEIRD manuel — avec mortalité",
        "POST", "/simulate",
        body={
            "formalism": "SEIRD",
            "N": 10_000_000,
            "beta": 0.35,
            "gamma": 0.1,
            "sigma": 0.2,
            "mu": 0.005,
            "I0": 50,
            "days": 400,
        },
        description="Inclut compartiment D (décès)"
    )
    if data2.get("time_series", {}).get("D"):
        total_dead = data2["time_series"]["D"][-1]
        print(f"       Décès totaux : {total_dead:,}")

    # Mode 1d : SEIRS avec réinfection
    test(
        "SEIRS — perte d'immunité (ω=0.01)",
        "POST", "/simulate",
        body={
            "formalism": "SEIRS",
            "N": 1_000_000,
            "beta": 0.4,
            "gamma": 0.2,
            "sigma": 0.3,
            "omega": 0.01,
            "days": 730,
        },
        description="Vagues de réinfection sur 2 ans"
    )

    # Mode 2 : Depuis un modèle de la bibliothèque
    if model_id:
        test(
            f"Depuis bibliothèque : {model_id[:35]}",
            "POST", "/simulate",
            body={
                "model_id": model_id,
                "N": 1_000_000,
                "days": 365,
            },
            description="Paramètres récupérés automatiquement depuis la BD"
        )

    # Erreur : gamma manquant
    test(
        "Erreur : gamma manquant → 422",
        "POST", "/simulate",
        body={"formalism": "SIR", "N": 1000, "beta": 0.3},
        expect_status=422,
        description="Doit retourner HTTP 422 Unprocessable Entity"
    )

    # Vérifier la structure time_series
    if data.get("time_series"):
        ts = data["time_series"]
        comps = [k for k in ts if k != "t"]
        n_pts = len(ts.get("t", []))
        print(f"\n       time_series : {n_pts} points — compartiments : {comps}")


def run_performance_test():
    """Mini benchmark — latence des endpoints principaux."""
    separator("Benchmark de performance")
    endpoints = [
        ("GET /", "GET", "/", None, None),
        ("GET /stats", "GET", "/stats", None, None),
        ("GET /diseases", "GET", "/diseases", None, None),
        ("GET /models/search (simple)", "GET", "/models/search",
         None, {"disease": "COVID", "limit": 10}),
        ("POST /simulate (SEIR)", "POST", "/simulate",
         {"formalism":"SEIR","N":1000000,"beta":0.31,"gamma":0.143,"sigma":0.196,"days":365},
         None),
    ]
    times = []
    for name, method, path, body, params in endpoints:
        t0 = time.time()
        try:
            if method == "GET":
                requests.get(f"{BASE}{path}", params=params, timeout=10)
            else:
                requests.post(f"{BASE}{path}", json=body, timeout=15)
            ms = round((time.time() - t0) * 1000)
            times.append(ms)
            emoji = "🟢" if ms < 100 else "🟡" if ms < 500 else "🔴"
            print(f"  {emoji}  {name:<40} {ms:>5} ms")
        except Exception as e:
            print(f"  ❌  {name} — {e}")
    if times:
        print(f"\n       Moyenne : {sum(times)//len(times)} ms  |  "
              f"Max : {max(times)} ms  |  Min : {min(times)} ms")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Tests API PIPOnto")
    parser.add_argument("--endpoint",
        choices=["health","stats","diseases","search",
                 "detail","params","simulate","perf","all"],
        default="all")
    parser.add_argument("--base", default="http://localhost:8000",
        help="URL de base de l'API")
    args = parser.parse_args()

    global BASE
    BASE = args.base
    ef = None if args.endpoint == "all" else args.endpoint

    # Tests principaux
    model_id = run_tests(ef)

    # Tests qui nécessitent un model_id
    if ef in (None, "detail", "params", "simulate"):
        if model_id is None:
            # Récupérer un model_id si pas encore fait
            try:
                resp = requests.get(f"{BASE}/models/search",
                                    params={"limit": 1}, timeout=5)
                models = resp.json().get("models", [])
                model_id = models[0]["model_id"] if models else None
            except Exception:
                pass

        if ef in (None, "detail", "params"):
            params_data = run_detail_tests(model_id)
        if ef in (None, "simulate"):
            run_simulate_tests(model_id)

    # Benchmark
    if ef in (None, "perf"):
        run_performance_test()

    # Rapport final
    print(f"\n{'═'*65}")
    print(f"  RÉSULTAT FINAL")
    print(f"{'═'*65}")
    print(f"  {OK} Réussis  : {passed}")
    print(f"  {FAIL} Échoués  : {failed}")
    print(f"  Total    : {passed + failed}")
    rate = round(passed / max(passed + failed, 1) * 100)
    bar = "█" * (rate // 5) + "░" * (20 - rate // 5)
    print(f"  [{bar}] {rate}%")
    print(f"{'═'*65}\n")

    if failed > 0:
        print(f"  Endpoints Swagger : {BASE}/docs")
        sys.exit(1)


if __name__ == "__main__":
    main()
