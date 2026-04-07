"""
piponto_nlp/tests_nlp.py
=========================
Suite de tests et démonstration du module NLP PIPOnto.

Tests couverts :
    - Cas principal : "simule COVID-19 à Paris chez les écoliers"
    - Cas Sénégal   : "paludisme dans la région de Thiès"
    - Cas grippe    : "modélise la grippe saisonnière chez les personnes âgées"
    - Cas ambigu    : maladie sans géographie
    - Cas inconnu   : terme hors dictionnaire
    - Cas anglais   : "simulate influenza in schools"

Usage :
    python3 tests_nlp.py           # tous les tests
    python3 tests_nlp.py --demo    # affichage détaillé du cas principal
"""

import sys
import json
from nlp_extractor import PIPOntoNLPExtractor
from sparql_generator import SPARQLQueryGenerator

extractor = PIPOntoNLPExtractor()
sparql_gen = SPARQLQueryGenerator()


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES D'AFFICHAGE
# ══════════════════════════════════════════════════════════════════════════════

def separator(title: str, char: str = "═", width: int = 70):
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}")


def print_result(result, show_sparql: bool = False):
    """Affiche un résultat d'extraction de façon lisible."""
    print(f"\n  📥 Requête    : '{result.query_raw}'")
    print(f"  🔧 Normalisée : '{result.query_normalized}'")

    print(f"\n  ─── Entités extraites ({len(result.entities)}) ───")
    for e in result.entities:
        icon = {
            "Disease": "🦠", "Geography": "📍", "Population": "👥",
            "Intervention": "🏥", "TimeFrame": "⏱️",
        }.get(e.category, "•")
        print(f"    {icon} [{e.category}] '{e.label}'")
        if e.uri_m8:
            print(f"         uri_m8 → {e.uri_m8.split('#')[-1]}")
        if e.uri_m2:
            print(f"         uri_m2 → {e.uri_m2.split('#')[-1]}")
        print(f"         confiance : {e.confidence:.2f}")

    print(f"\n  ─── Modèles M2 candidats ───")
    if result.candidate_model_uris:
        for i, cand in enumerate(result.candidate_model_uris):
            medal = ["🥇", "🥈", "🥉", " 4."][min(i, 3)]
            print(f"    {medal} [{cand['relevance_score']:.3f}] "
                  f"{cand['label']}")
            print(f"         uri → {cand['uri'].split('#')[-1]}")
    else:
        print("    ⚠️  Aucun modèle candidat trouvé")

    print(f"\n  ─── Confiance globale : {result.global_confidence:.3f} ───")
    if result.warnings:
        print(f"\n  ─── Avertissements ───")
        for w in result.warnings:
            print(f"    {w}")

    if show_sparql:
        queries = sparql_gen.generate_all(result)
        print(f"\n  ─── Requêtes SPARQL générées ({len(queries)}) ───")
        for name, q in queries.items():
            print(f"\n  [{name}]")
            # Afficher seulement les premières lignes pour lisibilité
            lines = q.strip().split("\n")
            preview = lines[:12]
            print("\n".join(f"    {l}" for l in preview))
            if len(lines) > 12:
                print(f"    ... ({len(lines) - 12} lignes supplémentaires)")


# ══════════════════════════════════════════════════════════════════════════════
# CAS DE TEST
# ══════════════════════════════════════════════════════════════════════════════

TEST_CASES = [
    # (requête, maladie_attendue, geo_attendue, pop_attendue, nb_candidats_min)
    (
        "simule COVID-19 à Paris chez les écoliers",
        "COVID19", "Paris", "SchoolChildren", 3,
    ),
    (
        "modélise le paludisme dans la région de Thiès",
        "Malaria", "Thies", "RuralPop_Thies", 1,
    ),
    (
        "simule la grippe saisonnière chez les personnes âgées en France",
        "SeasonalInfluenza", "France", "ElderlyPop_France", 1,
    ),
    (
        "COVID-19 avec confinement",
        "COVID19", None, None, 1,
    ),
    (
        "simulate influenza in schools",
        "SeasonalInfluenza", None, "SchoolChildren", 1,
    ),
    (
        "modélise la rougeole à Montréal",
        "Measles", "Montreal", None, 1,
    ),
    (
        "simule une épidémie de dengue",
        "Dengue", None, None, 0,
    ),
]


def run_tests() -> tuple[int, int]:
    """Exécute tous les tests. Retourne (réussis, total)."""
    separator("TESTS UNITAIRES — Module NLP PIPOnto", "═")
    passed = 0
    total = len(TEST_CASES)

    for i, (query, exp_disease, exp_geo, exp_pop, min_candidates) in \
            enumerate(TEST_CASES, 1):
        result = extractor.extract(query)
        errors = []

        # Vérifier maladie
        actual_disease = (result.disease.uri_m8.split("#")[-1]
                          if result.disease else None)
        if exp_disease and actual_disease != exp_disease:
            errors.append(
                f"maladie: attendu={exp_disease}, obtenu={actual_disease}"
            )

        # Vérifier géographie
        actual_geo = (result.geography.uri_m8.split("#")[-1]
                      if result.geography and result.geography.uri_m8 else None)
        if exp_geo and actual_geo != exp_geo:
            errors.append(f"géo: attendu={exp_geo}, obtenu={actual_geo}")

        # Vérifier population
        actual_pop = (result.population.uri_m8.split("#")[-1]
                      if result.population and result.population.uri_m8 else None)
        if exp_pop and actual_pop != exp_pop:
            errors.append(f"pop: attendu={exp_pop}, obtenu={actual_pop}")

        # Vérifier candidats
        if len(result.candidate_model_uris) < min_candidates:
            errors.append(
                f"candidats: attendu≥{min_candidates}, "
                f"obtenu={len(result.candidate_model_uris)}"
            )

        if not errors:
            passed += 1
            status = "✅"
        else:
            status = "❌"

        print(f"\n  Test {i:02d} {status} — '{query[:50]}'")
        if errors:
            for err in errors:
                print(f"           ↳ {err}")
        else:
            candidates_str = (
                f"{result.candidate_model_uris[0]['label']} "
                f"({result.candidate_model_uris[0]['relevance_score']:.3f})"
                if result.candidate_model_uris else "aucun"
            )
            print(f"           ↳ confiance={result.global_confidence:.3f} "
                  f"| modèle principal: {candidates_str}")

    separator(f"RÉSULTATS : {passed}/{total} tests réussis",
              "─" if passed < total else "═")
    return passed, total


def run_demo():
    """Démonstration complète du cas principal avec SPARQL."""
    separator("DÉMONSTRATION COMPLÈTE — Cas d'usage principal", "═")

    query = "simule COVID-19 à Paris chez les écoliers"
    result = extractor.extract(query)
    print_result(result, show_sparql=True)

    separator("EXPORT JSON (format API REST)", "─")
    d = result.to_dict()
    print(json.dumps(d, ensure_ascii=False, indent=2))

    separator("PARAMÈTRES POUR REQUÊTE SPARQL", "─")
    params = result.to_sparql_params()
    for k, v in params.items():
        short = v.split("#")[-1] if v and "#" in v else v
        print(f"  {k:25s} → {short}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    else:
        passed, total = run_tests()
        print()
        if passed == total:
            run_demo()
        sys.exit(0 if passed == total else 1)
