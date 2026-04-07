"""
pipeline/pubmed_queries.py
==========================
Définitions des requêtes PubMed pour les 15 maladies infectieuses prioritaires.

Chaque requête est construite pour cibler spécifiquement les articles décrivant
des MODÈLES MATHÉMATIQUES avec des PARAMÈTRES NUMÉRIQUES.

Stratégie de requête :
    - Termes MeSH officiels (champ [MeSH Terms])
    - Termes libres dans titre/abstract (champ [tiab])
    - Filtre sur type d'article : pas de reviews générales
    - Filtre temporel : 1970-2025 (modèles modernes)

Format : { disease_key: DiseaseQuery }
"""

from dataclasses import dataclass, field


@dataclass
class DiseaseQuery:
    """Configuration d'une requête PubMed pour une maladie."""
    disease_key:    str          # clé unique (= disease_id futur)
    name_fr:        str
    name_en:        str
    icd10:          str
    # Requête PubMed principale
    pubmed_query:   str
    # Mots-clés OBLIGATOIRES dans titre ou abstract (filtre post-récupération)
    required_keywords: list[str]
    # Mots-clés qui DISQUALIFIENT un article (trop générique, hors sujet)
    exclude_keywords:  list[str] = field(default_factory=list)
    # Nombre max d'articles à récupérer pour cette maladie
    max_results:    int = 200
    # Score de priorité OMS (1=haute, 2=moyenne, 3=basse)
    who_priority:   int = 1


# ══════════════════════════════════════════════════════════════════════════════
# REQUÊTES PAR MALADIE
# ══════════════════════════════════════════════════════════════════════════════

DISEASE_QUERIES: dict[str, DiseaseQuery] = {

    # ── COVID-19 ──────────────────────────────────────────────────────────────
    "COVID19": DiseaseQuery(
        disease_key="COVID19",
        name_fr="COVID-19",
        name_en="COVID-19",
        icd10="U07.1",
        pubmed_query=(
            '("COVID-19"[MeSH Terms] OR "SARS-CoV-2"[tiab] OR "COVID-19"[tiab]) '
            'AND ("mathematical model"[tiab] OR "compartmental model"[tiab] '
            'OR "SEIR"[tiab] OR "SEIRD"[tiab] OR "agent-based"[tiab] '
            'OR "transmission model"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "basic reproduction number"[tiab] OR "R0"[tiab]) '
            'AND ("2020"[pdat] : "2025"[pdat])'
        ),
        required_keywords=[
            "model", "seir", "sir", "compartment", "transmission",
            "reproduction", "sars-cov-2", "covid", "epidemic", "simulation",
            "stochastic", "agent-based", "network model", "parameters",
        ],
        exclude_keywords=[
            # Plus stricts : seulement les vrais hors-sujet
            "randomized clinical trial",
            "systematic review and meta-analysis",
            "case series only",
        ],
        max_results=300,
        who_priority=1,
    ),

    # ── GRIPPE SAISONNIÈRE ────────────────────────────────────────────────────
    "SeasonalInfluenza": DiseaseQuery(
        disease_key="SeasonalInfluenza",
        name_fr="Grippe saisonnière",
        name_en="Seasonal Influenza",
        icd10="J11",
        pubmed_query=(
            '("Influenza, Human"[MeSH Terms] OR "influenza"[tiab] '
            'OR "seasonal flu"[tiab]) '
            'AND ("mathematical model"[tiab] OR "SEIR"[tiab] OR "SIR model"[tiab] '
            'OR "transmission model"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "basic reproduction number"[tiab] OR "calibrated"[tiab]) '
            'AND ("1990"[pdat] : "2025"[pdat])'
        ),
        required_keywords=[
            "influenza", "flu", "model", "transmission", "seir", "sir",
            "reproduction", "parameters", "epidemic", "pandemic"
        ],
        exclude_keywords=[
            "clinical trial", "antiviral", "vaccine immunogenicity",
            "meta-analysis", "case series"
        ],
        max_results=200,
        who_priority=1,
    ),

    # ── GRIPPE PANDÉMIQUE ─────────────────────────────────────────────────────
    "PandemicInfluenza": DiseaseQuery(
        disease_key="PandemicInfluenza",
        name_fr="Grippe pandémique",
        name_en="Pandemic Influenza",
        icd10="J09",
        pubmed_query=(
            '("Influenza Pandemic"[tiab] OR "pandemic influenza"[tiab] '
            'OR "H1N1"[tiab] OR "H5N1"[tiab]) '
            'AND ("mathematical model"[tiab] OR "SEIR"[tiab] '
            'OR "transmission model"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "R0"[tiab] OR "reproduction number"[tiab])'
        ),
        required_keywords=[
            "pandemic", "influenza", "h1n1", "model", "transmission",
            "reproduction", "seir", "parameters"
        ],
        exclude_keywords=["clinical", "antiviral", "meta-analysis"],
        max_results=150,
        who_priority=1,
    ),

    # ── TUBERCULOSE ───────────────────────────────────────────────────────────
    "Tuberculosis": DiseaseQuery(
        disease_key="Tuberculosis",
        name_fr="Tuberculose",
        name_en="Tuberculosis",
        icd10="A15",
        pubmed_query=(
            '("Tuberculosis"[MeSH Terms] OR "tuberculosis"[tiab] OR "TB"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "SEIR"[tiab] OR "compartmental"[tiab] '
            'OR "dynamic model"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "basic reproduction number"[tiab] OR "calibrated"[tiab])'
        ),
        required_keywords=[
            "tuberculosis", "tb", "model", "transmission",
            "reproduction", "parameters", "latent", "reactivation"
        ],
        exclude_keywords=["drug resistance only", "clinical trial", "meta-analysis"],
        max_results=200,
        who_priority=1,
    ),

    # ── ROUGEOLE ──────────────────────────────────────────────────────────────
    "Measles": DiseaseQuery(
        disease_key="Measles",
        name_fr="Rougeole",
        name_en="Measles",
        icd10="B05",
        pubmed_query=(
            '("Measles"[MeSH Terms] OR "measles"[tiab] OR "rubeola"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "SIR"[tiab] OR "SEIR"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "basic reproduction number"[tiab] '
            'OR "vaccination"[tiab] OR "herd immunity"[tiab])'
        ),
        required_keywords=[
            "measles", "model", "transmission", "reproduction",
            "vaccination", "herd immunity", "seir", "sir"
        ],
        exclude_keywords=["clinical", "diagnosis", "meta-analysis"],
        max_results=150,
        who_priority=1,
    ),

    # ── PALUDISME ─────────────────────────────────────────────────────────────
    "Malaria": DiseaseQuery(
        disease_key="Malaria",
        name_fr="Paludisme",
        name_en="Malaria",
        icd10="B50",
        pubmed_query=(
            '("Malaria"[MeSH Terms] OR "malaria"[tiab] OR "plasmodium"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "Ross-Macdonald"[tiab] OR "vectorial capacity"[tiab] '
            'OR "SEIR"[tiab] OR "compartmental"[tiab] OR "agent-based"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "reproduction number"[tiab] OR "biting rate"[tiab] '
            'OR "calibrated"[tiab])'
        ),
        required_keywords=[
            "malaria", "plasmodium", "model", "transmission",
            "mosquito", "vector", "reproduction", "parameters"
        ],
        exclude_keywords=["drug treatment only", "clinical trial", "meta-analysis"],
        max_results=200,
        who_priority=1,
    ),

    # ── DENGUE ────────────────────────────────────────────────────────────────
    "Dengue": DiseaseQuery(
        disease_key="Dengue",
        name_fr="Dengue",
        name_en="Dengue fever",
        icd10="A90",
        pubmed_query=(
            '("Dengue"[MeSH Terms] OR "dengue fever"[tiab] OR "dengue virus"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "compartmental"[tiab] OR "vector-borne"[tiab] '
            'OR "SEIR"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "basic reproduction number"[tiab] OR "Aedes"[tiab])'
        ),
        required_keywords=[
            "dengue", "model", "transmission", "aedes", "vector",
            "reproduction", "parameters", "seir"
        ],
        exclude_keywords=["clinical", "serotype only", "meta-analysis"],
        max_results=150,
        who_priority=1,
    ),

    # ── EBOLA ─────────────────────────────────────────────────────────────────
    "Ebola": DiseaseQuery(
        disease_key="Ebola",
        name_fr="Maladie à virus Ebola",
        name_en="Ebola virus disease",
        icd10="A98.4",
        pubmed_query=(
            '("Hemorrhagic Fever, Ebola"[MeSH Terms] OR "Ebola"[tiab] '
            'OR "EVD"[tiab] OR "ebola virus"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "SEIR"[tiab] OR "epidemic model"[tiab] '
            'OR "compartmental"[tiab]) '
            'AND ("parameters"[tiab] OR "reproduction number"[tiab] '
            'OR "transmission rate"[tiab] OR "serial interval"[tiab])'
        ),
        required_keywords=[
            "ebola", "model", "transmission", "reproduction",
            "parameters", "outbreak", "seir"
        ],
        exclude_keywords=["clinical", "treatment", "meta-analysis"],
        max_results=100,
        who_priority=1,
    ),

    # ── VIH/SIDA ──────────────────────────────────────────────────────────────
    "HIV": DiseaseQuery(
        disease_key="HIV",
        name_fr="VIH/SIDA",
        name_en="HIV/AIDS",
        icd10="B20",
        pubmed_query=(
            '("HIV"[MeSH Terms] OR "HIV"[tiab] OR "human immunodeficiency virus"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "epidemic model"[tiab] OR "dynamic model"[tiab] '
            'OR "compartmental"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "basic reproduction number"[tiab] OR "calibrated"[tiab]) '
            'AND ("Sub-Saharan Africa"[tiab] OR "Africa"[tiab] '
            'OR "prevalence"[tiab] OR "incidence"[tiab])'
        ),
        required_keywords=[
            "hiv", "aids", "model", "transmission", "reproduction",
            "parameters", "epidemic", "africa"
        ],
        exclude_keywords=["drug trial", "clinical", "meta-analysis"],
        max_results=200,
        who_priority=1,
    ),

    # ── CHOLÉRA ───────────────────────────────────────────────────────────────
    "Cholera": DiseaseQuery(
        disease_key="Cholera",
        name_fr="Choléra",
        name_en="Cholera",
        icd10="A00",
        pubmed_query=(
            '("Cholera"[MeSH Terms] OR "cholera"[tiab] '
            'OR "Vibrio cholerae"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "SIRB"[tiab] OR "waterborne"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "reproduction number"[tiab] '
            'OR "transmission rate"[tiab] OR "calibrated"[tiab])'
        ),
        required_keywords=[
            "cholera", "model", "transmission", "waterborne",
            "reproduction", "parameters", "outbreak"
        ],
        exclude_keywords=["clinical", "treatment only", "meta-analysis"],
        max_results=100,
        who_priority=1,
    ),

    # ── MPOX ─────────────────────────────────────────────────────────────────
    "Mpox": DiseaseQuery(
        disease_key="Mpox",
        name_fr="Mpox (variole du singe)",
        name_en="Mpox (Monkeypox)",
        icd10="B04",
        pubmed_query=(
            '("Monkeypox"[MeSH Terms] OR "mpox"[tiab] OR "monkeypox"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "SEIR"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "reproduction number"[tiab] '
            'OR "transmission rate"[tiab]) '
            'AND ("2022"[pdat] : "2025"[pdat])'
        ),
        required_keywords=[
            "mpox", "monkeypox", "model", "transmission",
            "reproduction", "parameters", "outbreak"
        ],
        exclude_keywords=["clinical", "meta-analysis"],
        max_results=80,
        who_priority=1,
    ),

    # ── CHIKUNGUNYA ───────────────────────────────────────────────────────────
    "Chikungunya": DiseaseQuery(
        disease_key="Chikungunya",
        name_fr="Chikungunya",
        name_en="Chikungunya",
        icd10="A92.0",
        pubmed_query=(
            '("Chikungunya Fever"[MeSH Terms] OR "chikungunya"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "SEIR"[tiab] OR "vector-borne"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "reproduction number"[tiab] '
            'OR "Aedes"[tiab] OR "transmission rate"[tiab])'
        ),
        required_keywords=[
            "chikungunya", "model", "transmission", "aedes",
            "vector", "reproduction", "parameters"
        ],
        exclude_keywords=["clinical", "meta-analysis"],
        max_results=80,
        who_priority=2,
    ),

    # ── MÉNINGITE À MÉNINGOCOQUE ─────────────────────────────────────────────
    "MeningococcalMeningitis": DiseaseQuery(
        disease_key="MeningococcalMeningitis",
        name_fr="Méningite à méningocoque",
        name_en="Meningococcal meningitis",
        icd10="A39",
        pubmed_query=(
            '("Meningococcal Infections"[MeSH Terms] '
            'OR "meningococcal meningitis"[tiab] '
            'OR "Neisseria meningitidis"[tiab] OR "meningitis belt"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "epidemic model"[tiab] OR "SEIR"[tiab]) '
            'AND ("parameters"[tiab] OR "reproduction number"[tiab] '
            'OR "carriage"[tiab] OR "transmission rate"[tiab])'
        ),
        required_keywords=[
            "meningococcal", "meningitis", "model", "transmission",
            "reproduction", "parameters", "africa"
        ],
        exclude_keywords=["clinical", "vaccine only", "meta-analysis"],
        max_results=80,
        who_priority=2,
    ),

    # ── PALUDISME — AFRIQUE SUBSAHARIENNE (requête spécialisée Sénégal) ───────
    "MalariaWestAfrica": DiseaseQuery(
        disease_key="MalariaWestAfrica",
        name_fr="Paludisme — Afrique de l'Ouest",
        name_en="Malaria West Africa",
        icd10="B50",
        pubmed_query=(
            '("Malaria"[MeSH Terms] OR "malaria"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "compartmental"[tiab]) '
            'AND ("West Africa"[tiab] OR "Senegal"[tiab] OR "Mali"[tiab] '
            'OR "Burkina Faso"[tiab] OR "Guinea"[tiab] '
            'OR "sub-Saharan"[tiab] OR "seasonal"[tiab]) '
            'AND ("parameters"[tiab] OR "calibrated"[tiab] '
            'OR "reproduction number"[tiab])'
        ),
        required_keywords=[
            "malaria", "model", "west africa", "senegal", "seasonal",
            "transmission", "parameters", "sub-saharan"
        ],
        exclude_keywords=["drug treatment only", "clinical", "meta-analysis"],
        max_results=120,
        who_priority=1,
    ),

    # ── HÉPATITE B ────────────────────────────────────────────────────────────
    "HepatitisBVirus": DiseaseQuery(
        disease_key="HepatitisBVirus",
        name_fr="Hépatite B",
        name_en="Hepatitis B",
        icd10="B16",
        pubmed_query=(
            '("Hepatitis B"[MeSH Terms] OR "hepatitis B virus"[tiab] OR "HBV"[tiab]) '
            'AND ("mathematical model"[tiab] OR "transmission model"[tiab] '
            'OR "dynamic model"[tiab] OR "epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab] '
            'OR "basic reproduction number"[tiab] OR "calibrated"[tiab])'
        ),
        required_keywords=[
            "hepatitis b", "hbv", "model", "transmission",
            "reproduction", "parameters", "chronic", "epidemic"
        ],
        exclude_keywords=["clinical trial", "treatment only", "meta-analysis"],
        max_results=100,
        who_priority=2,
    ),
}


# ── Requêtes génériques transversales (tous modèles) ─────────────────────────
GENERIC_QUERIES: dict[str, DiseaseQuery] = {

    "GenericSEIR": DiseaseQuery(
        disease_key="Generic",
        name_fr="Modèles génériques SEIR/SIR",
        name_en="Generic SEIR/SIR models",
        icd10="",
        pubmed_query=(
            '("SEIR model"[tiab] OR "SIR model"[tiab] OR "SEIRS model"[tiab] '
            'OR "compartmental epidemic model"[tiab]) '
            'AND ("parameters"[tiab] OR "basic reproduction number"[tiab] '
            'OR "transmission rate"[tiab]) '
            'AND ("infectious disease"[tiab] OR "epidemic"[tiab]) '
            'AND NOT ("COVID-19"[tiab] OR "influenza"[tiab] '
            'OR "malaria"[tiab] OR "dengue"[tiab])'
        ),
        required_keywords=[
            "seir", "sir", "model", "transmission", "reproduction",
            "parameters", "infectious", "epidemic"
        ],
        exclude_keywords=["meta-analysis", "review only"],
        max_results=150,
        who_priority=2,
    ),

    "StochasticModels": DiseaseQuery(
        disease_key="StochasticGeneric",
        name_fr="Modèles stochastiques",
        name_en="Stochastic epidemic models",
        icd10="",
        pubmed_query=(
            '("stochastic model"[tiab] OR "stochastic epidemic"[tiab] '
            'OR "branching process"[tiab] OR "Gillespie"[tiab] '
            'OR "Monte Carlo"[tiab]) '
            'AND ("infectious disease"[tiab] OR "epidemic"[tiab]) '
            'AND ("parameters"[tiab] OR "transmission rate"[tiab])'
        ),
        required_keywords=[
            "stochastic", "model", "epidemic", "transmission", "parameters"
        ],
        exclude_keywords=["meta-analysis"],
        max_results=100,
        who_priority=2,
    ),

    "ABMModels": DiseaseQuery(
        disease_key="ABMGeneric",
        name_fr="Modèles à base d'agents",
        name_en="Agent-based epidemic models",
        icd10="",
        pubmed_query=(
            '("agent-based model"[tiab] OR "individual-based model"[tiab] '
            'OR "ABM"[tiab] OR "IBM"[tiab] OR "NetLogo"[tiab] '
            'OR "Covasim"[tiab] OR "GAMA"[tiab]) '
            'AND ("infectious disease"[tiab] OR "epidemic"[tiab] '
            'OR "transmission"[tiab]) '
            'AND ("parameters"[tiab] OR "calibrated"[tiab])'
        ),
        required_keywords=[
            "agent-based", "individual-based", "abm", "model",
            "epidemic", "transmission", "parameters"
        ],
        exclude_keywords=["meta-analysis"],
        max_results=100,
        who_priority=2,
    ),

    "SpatialMetapopulation": DiseaseQuery(
        disease_key="SpatialGeneric",
        name_fr="Modèles spatiaux / métapopulation",
        name_en="Spatial/metapopulation models",
        icd10="",
        pubmed_query=(
            '("metapopulation model"[tiab] OR "spatial model"[tiab] '
            'OR "network model"[tiab] OR "patch model"[tiab] '
            'OR "mobility model"[tiab]) '
            'AND ("infectious disease"[tiab] OR "epidemic"[tiab]) '
            'AND ("parameters"[tiab] OR "calibrated"[tiab] '
            'OR "transmission rate"[tiab])'
        ),
        required_keywords=[
            "metapopulation", "spatial", "network", "model",
            "epidemic", "transmission", "parameters"
        ],
        exclude_keywords=["meta-analysis"],
        max_results=100,
        who_priority=2,
    ),
}

# Toutes les requêtes combinées
ALL_QUERIES: dict[str, DiseaseQuery] = {
    **DISEASE_QUERIES,
    **GENERIC_QUERIES,
}

# Résumé
def print_summary():
    total_max = sum(q.max_results for q in ALL_QUERIES.values())
    print(f"\n{'═'*60}")
    print(f"  Requêtes PubMed PIPOnto — Résumé")
    print(f"{'═'*60}")
    print(f"  Maladies spécifiques   : {len(DISEASE_QUERIES)}")
    print(f"  Requêtes génériques    : {len(GENERIC_QUERIES)}")
    print(f"  Total requêtes         : {len(ALL_QUERIES)}")
    print(f"  Articles max attendus  : ~{total_max}")
    print(f"  Articles retenus (est) : ~{int(total_max * 0.35)}")
    print(f"{'─'*60}")
    for key, q in ALL_QUERIES.items():
        print(f"  [{q.who_priority}⭐] {q.name_en:<35} max={q.max_results}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    print_summary()
