"""
api/demo.py
============
Démo interactive de l'API PIPOnto.
Exemples d'utilisation commentés — à copier dans vos propres scripts.

Usage :
    python3 api/demo.py                     # démo complète
    python3 api/demo.py --scenario covid    # COVID-19 seulement
    python3 api/demo.py --scenario senegal  # Sénégal seulement
    python3 api/demo.py --scenario compare  # Comparaison de modèles
"""

import json
import requests
import argparse

BASE = "http://localhost:8000"


def pretty(data, indent=4):
    print(json.dumps(data, indent=indent, ensure_ascii=False, default=str))


def header(title):
    print(f"\n{'━'*60}")
    print(f"  {title}")
    print(f"{'━'*60}")


# ── Scénario 1 : Recherche COVID-19 ──────────────────────────────────────────

def scenario_covid():
    header("Scénario 1 — Recherche de modèles COVID-19 SEIR")

    # Recherche
    print("\n📋 Recherche : COVID-19, SEIR, avec code disponible")
    resp = requests.get(f"{BASE}/models/search", params={
        "disease":  "COVID",
        "formalism": "SEIR",
        "has_code": "true",
        "limit":    5,
    })
    data = resp.json()
    print(f"   → {data['total']} modèles trouvés")
    for m in data["models"][:3]:
        print(f"   • {m['model_id']}")
        print(f"     {m.get('disease_name')} | {m.get('formalism')} | "
              f"pays: {m.get('countries', [])}")
        if m.get("doi"):
            print(f"     DOI: https://doi.org/{m['doi']}")

    if not data["models"]:
        print("   (Aucun résultat — vérifiez que des modèles COVID sont validés)")
        return

    # Prendre le premier modèle pour la suite
    model_id = data["models"][0]["model_id"]
    print(f"\n🔍 Fiche complète : {model_id}")
    resp = requests.get(f"{BASE}/models/{model_id}")
    m = resp.json()
    print(f"   Titre article : {(m.get('ref_title') or '—')[:70]}...")
    print(f"   Auteurs : {(m.get('ref_authors') or '—')[:60]}...")
    print(f"   Journal : {m.get('ref_journal') or '—'} ({m.get('ref_year')})")
    print(f"   Paramètres :")
    for p in m.get("parameters", [])[:5]:
        print(f"     {p['symbol']:8s} = {p['default_value']:8.4f}  [{p.get('unit','')}]"
              f"  ({p['param_type']})")

    # Simulation avec ce modèle
    print(f"\n🧮 Simulation avec {model_id} — N=68M (France)")
    resp = requests.post(f"{BASE}/simulate", json={
        "model_id": model_id,
        "N":        68_000_000,
        "I0":       100,
        "days":     365,
    })
    sim = resp.json()
    print(f"   {sim.get('summary', 'Pas de résumé')}")
    print(f"   Formule utilisée  : {sim.get('formalism')}")
    print(f"   Paramètres : {sim.get('parameters_used')}")


# ── Scénario 2 : Contexte Sénégal ────────────────────────────────────────────

def scenario_senegal():
    header("Scénario 2 — Modèles épidémiologiques pour le Sénégal")

    print("\n📋 Modèles avec géographie Sénégal (SN)")
    resp = requests.get(f"{BASE}/models/search", params={
        "country": "SN",
        "limit": 10,
    })
    data = resp.json()
    print(f"   → {data['total']} modèles pour le Sénégal")
    for m in data["models"]:
        print(f"   • {m['model_id']}")
        print(f"     {m.get('disease_name')} | {m.get('formalism')} | "
              f"conf={m.get('extraction_confidence'):.2f}")

    print("\n📋 Maladies avec modèles disponibles")
    resp = requests.get(f"{BASE}/diseases", params={"with_models_only": "true"})
    for d in resp.json()["diseases"][:8]:
        print(f"   • {d['name_en']:30s}  {d['model_count']:3d} modèles  "
              f"[{d.get('transmission_route', '—')}]")


# ── Scénario 3 : Comparaison SIR vs SEIR ─────────────────────────────────────

def scenario_compare():
    header("Scénario 3 — Comparaison SIR vs SEIR vs SEIRD")

    N = 1_000_000
    params_communs = {"N": N, "beta": 0.31, "gamma": 0.143, "I0": 10, "days": 365}

    modeles = [
        ("SIR",   {**params_communs, "formalism": "SIR"}),
        ("SEIR",  {**params_communs, "formalism": "SEIR",  "sigma": 0.196}),
        ("SEIRD", {**params_communs, "formalism": "SEIRD", "sigma": 0.196, "mu": 0.003}),
    ]

    print(f"\n  Population N={N:,}  |  β=0.31  γ=0.143  (COVID typique)\n")
    print(f"  {'Modèle':<10} {'Pic infectieux':>16} {'Jour pic':>9} "
          f"{'Taux attaque':>14} {'R₀ eff.':>9}")
    print(f"  {'─'*10} {'─'*16} {'─'*9} {'─'*14} {'─'*9}")

    for name, body in modeles:
        resp = requests.post(f"{BASE}/simulate", json=body)
        if resp.status_code != 200:
            print(f"  {name:<10} ERREUR : {resp.json().get('detail','?')}")
            continue
        s = resp.json()
        print(f"  {name:<10} {s['peak_infected']:>16,} {s['peak_day']:>9} "
              f"{s['attack_rate']*100:>13.1f}% {s['R0_effective']:>9.2f}")

    # SEIRS — réinfection
    print(f"\n  SEIRS (perte immunité ω=0.01) — 2 ans")
    resp = requests.post(f"{BASE}/simulate", json={
        **params_communs,
        "formalism": "SEIRS",
        "sigma": 0.196,
        "omega": 0.01,
        "days": 730,
    })
    if resp.status_code == 200:
        s = resp.json()
        print(f"  → {s['summary']}")


# ── Scénario 4 : Stats globales ───────────────────────────────────────────────

def scenario_stats():
    header("Scénario 4 — Statistiques de la bibliothèque PIPOnto")

    resp = requests.get(f"{BASE}/stats")
    data = resp.json()

    print(f"\n  Modèles validés     : {data.get('validated_models')}")
    print(f"  Avec code source    : {data.get('models_with_code')}")
    print(f"  Validés empiriquem. : {data.get('empirically_validated')}")
    print(f"  Confiance moyenne   : {data.get('avg_confidence'):.3f}")

    print(f"\n  Distribution par formalisme :")
    by_f = sorted(data.get("by_formalism", {}).items(), key=lambda x: -x[1])
    for f, n in by_f[:8]:
        bar = "▓" * (n * 20 // max(v for _, v in by_f))
        print(f"  {f:<22} {bar:<20} {n:>4}")

    print(f"\n  Top 8 maladies :")
    for d in data.get("by_disease", [])[:8]:
        print(f"  {d['disease']:<35} {d['count']:>4} modèles")

    print(f"\n  Top 5 pays :")
    for c in data.get("top_countries", [])[:5]:
        print(f"  [{c['code']}] {c['country']:<30} {c['count']:>4} modèles")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario",
        choices=["covid", "senegal", "compare", "stats", "all"],
        default="all")
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()
    BASE = args.base

    # Vérifier que l'API répond
    try:
        health = requests.get(f"{BASE}/", timeout=3).json()
        print(f"🧬 PIPOnto API — statut : {health.get('status')}")
        print(f"   Modèles validés : {health.get('validated_models')}")
    except Exception:
        print(f"❌ API inaccessible à {BASE}")
        print(f"   Lancez d'abord : python3 run_api.py")
        exit(1)

    scenarios = {
        "covid":   scenario_covid,
        "senegal": scenario_senegal,
        "compare": scenario_compare,
        "stats":   scenario_stats,
    }

    if args.scenario == "all":
        for fn in scenarios.values():
            fn()
    else:
        scenarios[args.scenario]()

    print(f"\n{'━'*60}")
    print(f"  Documentation complète : {BASE}/docs")
    print(f"{'━'*60}\n")
