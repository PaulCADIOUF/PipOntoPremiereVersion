"""
pipeline/pdf_patterns.py
=========================
Bibliothèque de patterns regex pour l'extraction automatique
de paramètres épidémiologiques depuis des PDFs scientifiques.

Organisé en 6 sections :
    1. Formalisme du modèle (SIR, SEIR, ABM...)
    2. Type de modèle (déterministe, stochastique)
    3. Paramètres épidémiologiques (β, γ, σ, R0...)
    4. Géographies (pays, villes, régions)
    5. Populations (scolaire, personnes âgées, urbain...)
    6. Interventions (confinement, vaccination...)
    7. Code source (GitHub, Zenodo, plateforme)
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURES DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtractedParam:
    """Paramètre épidémiologique extrait."""
    symbol:       str
    param_type:   str         # transmission_rate, recovery_rate, R0...
    value:        float
    value_min:    Optional[float] = None
    value_max:    Optional[float] = None
    ci_low:       Optional[float] = None
    ci_high:      Optional[float] = None
    unit:         str = "day^-1"
    time_unit:    str = "day"
    is_estimated: bool = True
    context:      str = ""    # phrase source pour traçabilité
    confidence:   float = 0.8


@dataclass
class ExtractedModel:
    """Modèle extrait d'un PDF."""
    # Identité
    formalism:       str = "SEIR"           # SIR, SEIR, SEIRS, SEIRD, ABM...
    model_type:      str = "DETERMINISTIC"  # DETERMINISTIC, STOCHASTIC, HYBRID
    spatial_struct:  str = "NONE"           # NONE, METAPOPULATION, NETWORK, GRID
    is_age_struct:   bool = False
    is_multi_strain: bool = False
    has_interventions: bool = False
    platform:        str = "PYTHON"

    # Paramètres
    params:          list[ExtractedParam] = field(default_factory=list)

    # Compartiments détectés
    compartments:    list[str] = field(default_factory=list)   # ["S","E","I","R"]

    # Géographies
    countries:       list[str] = field(default_factory=list)   # ["FR","GB"]
    country_names:   list[str] = field(default_factory=list)
    cities:          list[str] = field(default_factory=list)
    population_size: Optional[int] = None

    # Population
    population_type: str = "GENERAL"

    # Code
    github_url:      Optional[str] = None
    zenodo_url:      Optional[str] = None
    has_code:        bool = False
    code_license:    Optional[str] = None

    # Qualité extraction
    extraction_confidence: float = 0.0
    extraction_notes: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 1. DÉTECTION DU FORMALISME
# ══════════════════════════════════════════════════════════════════════════════

# Ordre important : les plus spécifiques en premier
FORMALISM_PATTERNS = [
    # ── ABM / Individual-based ────────────────────────────────────────────────
    ("ABM",           r'\b(?:agent[- ]based|individual[- ]based|IBM|ABM)\b'),
    ("ABM",           r'\bCovasim\b'),
    ("ABM",           r'\bNetLogo\b'),
    ("ABM",           r'\bGAMA\b'),
    ("ABM",           r'\bMesa\b(?:\s+framework)?'),

    # ── Réseau ───────────────────────────────────────────────────────────────
    ("NETWORK",       r'\b(?:network[- ]based|contact network|network model)\b'),

    # ── Métapopulation ────────────────────────────────────────────────────────
    ("METAPOPULATION",r'\b(?:metapopulation|meta-population|patch model|multi[- ]patch)\b'),

    # ── Compartimentaux spécifiques (ordre : plus complexe → plus simple) ────
    ("SEIRD",         r'\bSEIR[DM]\b'),
    ("SEIRS",         r'\bSEIRS\b'),
    ("SEIS",          r'\bSEIS\b'),
    ("SEIR",          r'\bSEIR\b'),
    ("SEIRD",          r'\bSIRD\b'),
    ("SIRS",          r'\bSIRS\b'),
    ("SIS",           r'\bSIS\b(?!\w)'),
    ("SIR",           r'\bSIR\b(?!\w)'),

    # ── Ross-Macdonald (paludisme/dengue) ────────────────────────────────────
    ("NETWORK",       r'\bRoss[- ]Macdonald\b'),

    # ── Stochastique générique ────────────────────────────────────────────────
    ("STOCHASTIC_SIR",r'\bstochastic\s+(?:SIR|SEIR|compartmental)\b'),

    # ── Renouvellement ────────────────────────────────────────────────────────
    ("RENEWAL_EQUATION", r'\brenewal\s+equation\b'),

    # ── Bayésien ─────────────────────────────────────────────────────────────
    ("BAYESIAN",      r'\bBayesian\s+(?:model|inference|framework)\b'),
]

def detect_formalism(text: str) -> tuple[str, float]:
    """Détecte le formalisme dominant. Retourne (formalism, confidence)."""
    text_lower = text.lower()

    # Compter les occurrences de chaque formalisme
    counts = {}
    for formalism, pattern in FORMALISM_PATTERNS:
        matches = len(re.findall(pattern, text, re.IGNORECASE))
        if matches > 0:
            counts[formalism] = counts.get(formalism, 0) + matches

    if not counts:
        return "OTHER", 0.3

    # Prendre le formalisme le plus fréquent
    best = max(counts, key=counts.get)
    total = sum(counts.values())
    confidence = min(counts[best] / max(total, 1) + 0.4, 1.0)
    return best, round(confidence, 2)


# ══════════════════════════════════════════════════════════════════════════════
# 2. DÉTECTION DU TYPE (déterministe / stochastique)
# ══════════════════════════════════════════════════════════════════════════════

STOCHASTIC_PATTERNS = [
    r'\bstochastic\b',
    r'\bMarkov\b',
    r'\bGillespie\b',
    r'\bMonte Carlo\b',
    r'\bbranching process\b',
    r'\btau[- ]leaping\b',
    r'\brandom\s+(?:walk|process|variable)\b',
]

def detect_model_type(text: str) -> str:
    stoch_hits = sum(
        len(re.findall(p, text, re.IGNORECASE))
        for p in STOCHASTIC_PATTERNS
    )
    determ_hits = len(re.findall(
        r'\b(?:deterministic|ODE|ordinary differential|system of equations)\b',
        text, re.IGNORECASE
    ))
    if stoch_hits > 0 and determ_hits > 0:
        return "HYBRID"
    if stoch_hits > 0:
        return "STOCHASTIC"
    return "DETERMINISTIC"


# ══════════════════════════════════════════════════════════════════════════════
# 3. EXTRACTION DES PARAMÈTRES ÉPIDÉMIOLOGIQUES
# ══════════════════════════════════════════════════════════════════════════════

# Format général des valeurs numériques dans les articles :
#   0.31, .31, 1.23e-2, 2.5×10^-3
NUM = r'(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'

# Intervalle de confiance : (0.25, 0.35) ou [0.25, 0.35] ou 0.25–0.35
CI = r'(?:\(|\[)?{num}\s*[,;–\-]\s*{num}(?:\)|\])?'.format(num=NUM)

PARAM_PATTERNS = [
    # ── R0 / Nombre de reproduction de base ──────────────────────────────────
    {
        "param_type": "R0",
        "symbol":     "R₀",
        "name_en":    "Basic reproduction number",
        "name_fr":    "Nombre de reproduction de base",
        "unit":       "dimensionless",
        "time_unit":  "none",
        "patterns": [
            # R0 = 2.5 ou R0 ≈ 2.5 ou R_0 = 2.5
            rf'R[_0₀]?\s*[=≈~≃]\s*{NUM}',
            # basic reproduction number of 2.5
            rf'basic\s+reproduction\s+number\s+(?:of\s+|was\s+|is\s+)?(?:approximately\s+)?{NUM}',
            rf'R0\s+(?:was\s+|is\s+|estimated\s+(?:at|to be)\s+){NUM}',
            # (R0=2.5) or [R0=2.5]
            rf'[\(\[]\s*R[_0₀]\s*=\s*{NUM}\s*[\)\]]',
        ]
    },

    # ── Rt / Nombre de reproduction effectif ─────────────────────────────────
    {
        "param_type": "R0",
        "symbol":     "Rₜ",
        "name_en":    "Effective reproduction number",
        "name_fr":    "Nombre de reproduction effectif",
        "unit":       "dimensionless",
        "time_unit":  "none",
        "patterns": [
            rf'R[_e]?t?\s*[=≈]\s*{NUM}',
            rf'effective\s+reproduction\s+number\s+(?:of\s+|was\s+|is\s+)?{NUM}',
        ]
    },

    # ── Taux de transmission β ────────────────────────────────────────────────
    {
        "param_type": "TRANSMISSION_RATE",
        "symbol":     "β",
        "name_en":    "Transmission rate",
        "name_fr":    "Taux de transmission",
        "unit":       "day^-1",
        "time_unit":  "day",
        "patterns": [
            rf'β\s*[=≈]\s*{NUM}',
            rf'beta\s*[=≈]\s*{NUM}',
            rf'transmission\s+rate\s*[=≈:]\s*{NUM}',
            rf'contact\s+rate\s*[=≈:]\s*{NUM}',
            rf'infection\s+rate\s*[=≈:]\s*{NUM}',
        ]
    },

    # ── Taux de guérison γ ────────────────────────────────────────────────────
    {
        "param_type": "RECOVERY_RATE",
        "symbol":     "γ",
        "name_en":    "Recovery rate",
        "name_fr":    "Taux de guérison",
        "unit":       "day^-1",
        "time_unit":  "day",
        "patterns": [
            rf'γ\s*[=≈]\s*{NUM}',
            rf'gamma\s*[=≈]\s*{NUM}',
            rf'recovery\s+rate\s*[=≈:]\s*{NUM}',
            rf'infectious\s+period\s*[=≈:]\s*{NUM}\s*days?',  # 1/γ
        ]
    },

    # ── Taux d'incubation σ ───────────────────────────────────────────────────
    {
        "param_type": "INCUBATION_RATE",
        "symbol":     "σ",
        "name_en":    "Incubation rate",
        "name_fr":    "Taux d'incubation",
        "unit":       "day^-1",
        "time_unit":  "day",
        "patterns": [
            rf'σ\s*[=≈]\s*{NUM}',
            rf'sigma\s*[=≈]\s*{NUM}',
            rf'incubation\s+rate\s*[=≈:]\s*{NUM}',
            rf'incubation\s+period\s*(?:of\s+)?{NUM}\s*days?',
            rf'latent\s+period\s*(?:of\s+)?{NUM}\s*days?',
        ]
    },

    # ── Intervalle sériel ─────────────────────────────────────────────────────
    {
        "param_type": "SERIAL_INTERVAL",
        "symbol":     "SI",
        "name_en":    "Serial interval",
        "name_fr":    "Intervalle sériel",
        "unit":       "day",
        "time_unit":  "day",
        "patterns": [
            rf'serial\s+interval\s*(?:of\s+|was\s+|=\s*)?{NUM}\s*days?',
            rf'generation\s+time\s*(?:of\s+|=\s*)?{NUM}\s*days?',
            rf'generation\s+interval\s*(?:of\s+|=\s*)?{NUM}\s*days?',
        ]
    },

    # ── CFR / IFR ─────────────────────────────────────────────────────────────
    {
        "param_type": "CASE_FATALITY_RATE",
        "symbol":     "CFR",
        "name_en":    "Case fatality rate",
        "name_fr":    "Taux de létalité",
        "unit":       "%",
        "time_unit":  "none",
        "patterns": [
            rf'(?:CFR|IFR|case\s+fatality\s+rate|infection\s+fatality\s+rate)'
            rf'\s*(?:of\s+|=\s*|was\s+)?{NUM}\s*%?',
        ]
    },

    # ── Taux de mortalité μ ───────────────────────────────────────────────────
    {
        "param_type": "MORTALITY_RATE",
        "symbol":     "μ",
        "name_en":    "Mortality rate",
        "name_fr":    "Taux de mortalité",
        "unit":       "day^-1",
        "time_unit":  "day",
        "patterns": [
            rf'(?:μ|mu|mortality\s+rate|disease[- ]induced\s+mortality)\s*[=≈:]\s*{NUM}',
            rf'death\s+rate\s*[=≈:]\s*{NUM}',
        ]
    },

    # ── Taux de vaccination ρ ─────────────────────────────────────────────────
    {
        "param_type": "VACCINATION_RATE",
        "symbol":     "ρ",
        "name_en":    "Vaccination rate",
        "name_fr":    "Taux de vaccination",
        "unit":       "day^-1",
        "time_unit":  "day",
        "patterns": [
            rf'vaccination\s+rate\s*[=≈:]\s*{NUM}',
            rf'vaccine\s+coverage\s*[=≈:]\s*{NUM}',
        ]
    },

    # ── Taux de perte d'immunité ω ────────────────────────────────────────────
    {
        "param_type": "WANING_IMMUNITY_RATE",
        "symbol":     "ω",
        "name_en":    "Waning immunity rate",
        "name_fr":    "Taux de perte d'immunité",
        "unit":       "day^-1",
        "time_unit":  "day",
        "patterns": [
            rf'(?:ω|omega|waning\s+immunity|loss\s+of\s+immunity)\s*[=≈:]\s*{NUM}',
            rf'immunity\s+duration\s*(?:of\s+)?{NUM}\s*(?:days?|months?)',
        ]
    },
]


def extract_param_value(text_around: str, pattern: str) -> Optional[float]:
    """Extrait une valeur numérique depuis un contexte textuel."""
    m = re.search(pattern, text_around, re.IGNORECASE)
    if m:
        try:
            val = float(m.group(1).replace(',', '.'))
            # Filtre de cohérence : les paramètres épidémio sont dans [0, 100]
            if 0 < val < 100:
                return val
        except (ValueError, IndexError):
            pass
    return None


def extract_parameters(text: str) -> list[ExtractedParam]:
    """
    Extrait tous les paramètres épidémiologiques du texte.
    Retourne une liste de ExtractedParam dédupliqués.
    """
    extracted = []
    seen_types = set()

    # Travailler sur des fenêtres de 200 chars autour des positions clés
    for param_def in PARAM_PATTERNS:
        for pattern in param_def["patterns"]:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                try:
                    val = float(m.group(1))
                except (IndexError, ValueError):
                    continue

                # Filtre cohérence
                if not (0 < val < 1000):
                    continue

                # Filtre cohérence spécifique au type
                ptype = param_def["param_type"]
                if ptype == "R0" and not (0.1 < val < 50):
                    continue
                if ptype in ("TRANSMISSION_RATE", "RECOVERY_RATE",
                             "INCUBATION_RATE") and val > 10:
                    continue

                # Contexte (50 chars avant + 50 chars après)
                start = max(0, m.start() - 50)
                end   = min(len(text), m.end() + 50)
                context = text[start:end].replace('\n', ' ').strip()

                # Dédupliquer par type (garder la première occurrence)
                if ptype not in seen_types:
                    seen_types.add(ptype)
                    extracted.append(ExtractedParam(
                        symbol=param_def["symbol"],
                        param_type=ptype,
                        value=val,
                        unit=param_def["unit"],
                        time_unit=param_def["time_unit"],
                        is_estimated=True,
                        context=context[:200],
                        confidence=0.75,
                    ))
                break  # un seul match par pattern

    return extracted


# ══════════════════════════════════════════════════════════════════════════════
# 4. DÉTECTION GÉOGRAPHIQUE
# ══════════════════════════════════════════════════════════════════════════════

# (country_name, iso_code, population_size, density, uri_m8_hint)
GEOGRAPHY_PATTERNS = [
    # ── Afrique de l'Ouest ────────────────────────────────────────────────────
    (r'\bSenegal\b',                   "SN", "Sénégal",      17763163,  79.0),
    (r'\bThiès?\b',                    "SN", "Thiès",         1163974, None),
    (r'\bDakar\b',                     "SN", "Dakar",         3732284, 6284),
    (r'\bMali\b',                      "ML", "Mali",         22414599,  18.0),
    (r'\bBurkina\s+Faso\b',            "BF", "Burkina Faso",  22100683,  80.7),
    (r'\bGuinea\b',                    "GN", "Guinée",       13532783,  55.0),
    (r'\bNigeria\b',                   "NG", "Nigeria",     218541212, 236.0),
    (r'\bGhana\b',                     "GH", "Ghana",        32395450, 135.0),
    (r'\bCôte\s+d.Ivoire\b|Ivory Coast',"CI","Côte d'Ivoire",27478249,  85.0),
    (r'\bCameroon\b',                  "CM", "Cameroun",     27914536,  58.0),
    # ── Afrique de l'Est / Sud ────────────────────────────────────────────────
    (r'\bKenya\b',                     "KE", "Kenya",        54985698,  94.0),
    (r'\bEthiopia\b',                  "ET", "Éthiopie",    123379924, 115.0),
    (r'\bTanzania\b',                  "TZ", "Tanzanie",     63298550,  67.0),
    (r'\bSouth\s+Africa\b',            "ZA", "Afrique du Sud",59308690,  48.0),
    # ── Asie du Sud-Est ───────────────────────────────────────────────────────
    (r'\bIndonesia\b',                 "ID", "Indonésie",   275501339, 145.0),
    (r'\bPhilippines?\b',              "PH", "Philippines",  114163719, 380.0),
    (r'\bVietnam\b',                   "VN", "Vietnam",      97338583, 313.0),
    (r'\bThailand\b',                  "TH", "Thaïlande",    71601103, 139.0),
    (r'\bIndia\b',                     "IN", "Inde",        1428627663, 475.0),
    (r'\bChina\b',                     "CN", "Chine",       1425671352, 153.0),
    # ── Europe ────────────────────────────────────────────────────────────────
    (r'\bFrance\b',                    "FR", "France",       68042591, 122.0),
    (r'\bParis\b',                     "FR", "Paris",         2161000,20755.0),
    (r'\bUnited\s+Kingdom\b|UK\b|England\b',
                                       "GB", "Royaume-Uni",  67736802, 272.0),
    (r'\bGermany\b|Deutschland\b',     "DE", "Allemagne",    84307700, 235.0),
    (r'\bItaly\b|Italia\b',            "IT", "Italie",       60461826, 200.0),
    (r'\bSpain\b|España\b',            "ES", "Espagne",      47422613,  94.0),
    (r'\bNetherlands?\b',              "NL", "Pays-Bas",     17618299, 423.0),
    (r'\bBelgium\b',                   "BE", "Belgique",     11632326, 376.0),
    (r'\bSweden\b',                    "SE", "Suède",        10549347,  25.0),
    # ── Amérique du Nord ─────────────────────────────────────────────────────
    (r'\bUnited\s+States?\b|USA\b|U\.S\.\b',
                                       "US", "États-Unis",  335893238,  36.0),
    (r'\bCanada\b',                    "CA", "Canada",       38654738,   4.0),
    (r'\bMontreal\b|Montréal\b',       "CA", "Montréal",      2096468, 976.0),
    # ── Amérique du Sud ────────────────────────────────────────────────────────
    (r'\bBrazil\b|Brasil\b',           "BR", "Brésil",      215313498,  25.0),
    (r'\bMexico\b|México\b',           "MX", "Mexique",     127575529,  65.0),
    # ── Océanie ────────────────────────────────────────────────────────────────
    (r'\bAustralia\b',                 "AU", "Australie",    26117000,   3.0),
    # ── Termes généraux ───────────────────────────────────────────────────────
    (r'\bSub[-\s]Saharan\s+Africa\b',  "SS", "Afrique subsaharienne", None, None),
    (r'\bWest\s+Africa\b',             "WA", "Afrique de l'Ouest", None, None),
    (r'\bSoutheast\s+Asia\b',          "SE", "Asie du Sud-Est", None, None),
]

def detect_geographies(text: str) -> list[dict]:
    """
    Détecte les géographies mentionnées dans le texte.
    Retourne une liste de dicts avec code, nom, population, densité.
    """
    found = []
    seen_codes = set()

    # Chercher dans le titre et l'abstract (premiers 3000 chars)
    search_text = text[:3000]

    for pattern, iso, name, pop, density in GEOGRAPHY_PATTERNS:
        if re.search(pattern, search_text, re.IGNORECASE):
            if iso not in seen_codes:
                seen_codes.add(iso)
                found.append({
                    "iso":      iso,
                    "name":     name,
                    "pop":      pop,
                    "density":  density,
                    "is_city":  pop is not None and pop < 10_000_000,
                })

    return found


# ══════════════════════════════════════════════════════════════════════════════
# 5. DÉTECTION POPULATION
# ══════════════════════════════════════════════════════════════════════════════

POPULATION_PATTERNS = [
    ("SCHOOL",           r'\b(?:school[- ]age|children|écoliers|schoolchildren|'
                          r'students?|pupils?|pediatric|paediatric|school\s+setting)\b'),
    ("ELDERLY",          r'\b(?:elderly|older\s+adults?|aged?\s+(?:population|people)|'
                          r'65\+|senior\s+citizens?|geriatric)\b'),
    ("HEALTHCARE_WORKERS",r'\b(?:healthcare\s+workers?|HCW|medical\s+staff|'
                          r'nurses?|physicians?|frontline\s+workers?)\b'),
    ("URBAN",            r'\b(?:urban\s+(?:population|setting|area)|city\s+dwellers?|'
                          r'metropolitan)\b'),
    ("RURAL",            r'\b(?:rural|village|periurban)\b'),
    ("CHILDREN_UNDER5",  r'\b(?:under[- ]5|children\s+under\s+5|under\s+five|'
                          r'infants?|toddlers?)\b'),
    ("IMMUNOCOMPROMISED", r'\b(?:immunocompromised|immunodeficient|HIV[-\s]positive|'
                          r'transplant\s+recipients?)\b'),
    ("GENERAL",          r'\b(?:general\s+population|entire\s+population|'
                          r'whole\s+population)\b'),
]

def detect_population(text: str) -> str:
    """Détecte le type de population principal. Retourne un population_type."""
    search_text = text[:2000]
    scores = {}
    for pop_type, pattern in POPULATION_PATTERNS:
        hits = len(re.findall(pattern, search_text, re.IGNORECASE))
        if hits > 0:
            scores[pop_type] = hits

    if not scores:
        return "GENERAL"
    return max(scores, key=scores.get)


# ══════════════════════════════════════════════════════════════════════════════
# 6. DÉTECTION DES COMPARTIMENTS
# ══════════════════════════════════════════════════════════════════════════════

COMPARTMENT_DEFINITIONS = {
    "S": ("Susceptible",    "Susceptibles",    False, False, False),
    "E": ("Exposed",        "Exposés",         False, False, False),
    "I": ("Infectious",     "Infectieux",      True,  False, False),
    "R": ("Recovered",      "Rétablis",        False, True,  False),
    "D": ("Dead",           "Décédés",         False, False, True),
    "V": ("Vaccinated",     "Vaccinés",        False, True,  False),
    "H": ("Hospitalized",   "Hospitalisés",    True,  False, False),
    "Q": ("Quarantined",    "Quarantaine",     False, False, False),
    "A": ("Asymptomatic",   "Asymptomatiques", True,  False, False),
    "C": ("Critical",       "Critiques",       True,  False, False),
    "M": ("Mortality",      "Mortalité",       False, False, True),
}

def detect_compartments(formalism: str, text: str) -> list[str]:
    """
    Détecte les compartiments du modèle.
    Combine la déduction depuis le formalisme + détection dans le texte.
    """
    # Compartiments déduits du formalisme
    base = list(formalism.replace("_", "").replace("STOCHASTIC", "")
                          .replace("MODEL","").replace("OTHER","").replace("RENEWAL","R")
                          .replace("EQUATION","")
                          .replace("BAYESIAN","SIR").replace("NETWORK","SIR")
                          .replace("METAPOPULATION","SEIR").replace("ABM","SEIR"))
    inferred = [c for c in base if c in COMPARTMENT_DEFINITIONS]

    # Compartiments supplémentaires détectés dans le texte
    extras = []
    for symbol in ["V", "H", "Q", "A", "C", "D"]:
        if symbol not in inferred:
            pattern = rf'\b{symbol}\s*[\(\[]?\s*(?:compartment|class|state)?\s*[\)\]]?'
            if re.search(pattern, text[:1000]):
                extras.append(symbol)

    return list(dict.fromkeys(inferred + extras))   # préserve l'ordre, déduplique


# ══════════════════════════════════════════════════════════════════════════════
# 7. DÉTECTION DU CODE SOURCE
# ══════════════════════════════════════════════════════════════════════════════

CODE_PATTERNS = [
    ("github",  r'https?://github\.com/[\w\-\.]+/[\w\-\.]+(?:/tree/[\w\-]+)?'),
    ("zenodo",  r'https?://(?:doi\.org/10\.5281/zenodo\.\d+|zenodo\.org/record/\d+)'),
    ("osf",     r'https?://osf\.io/[\w]+'),
    ("comses",  r'https?://(?:www\.)?comses\.net/codebases?/[\w\-]+'),
    ("figshare", r'https?://(?:figshare\.com/[\w/\-]+|doi\.org/10\.6084/[\w\.]+)'),
    ("covasim", r'\bCovasim\b'),
    ("netlogo",  r'\bNetLogo\b'),
    ("gama",     r'\bGAMA\s+platform\b'),
]

LICENSE_PATTERNS = [
    ("MIT",    r'\bMIT\s+[Ll]icense\b'),
    ("GPL",    r'\bGPL[v\-]?[23]?\b'),
    ("Apache", r'\bApache[-\s]2\.0\b'),
    ("CC-BY",  r'\bCC[- ]BY(?:[- ][\d\.]+)?\b'),
    ("BSD",    r'\bBSD[- ]\d+[- ][Cc]lause\b'),
]

def detect_code(text: str) -> tuple[Optional[str], Optional[str], Optional[str], bool]:
    """
    Détecte les URLs de code source.
    Retourne (github_url, zenodo_url, platform, has_code).
    """
    github_url = zenodo_url = platform = None
    has_code = False

    for source, pattern in CODE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            has_code = True
            url = m.group(0) if m.group(0).startswith('http') else None
            if source == "github" and url:
                github_url = url
            elif source == "zenodo" and url:
                zenodo_url = url
            elif source in ("covasim", "netlogo", "gama"):
                # Mapper vers les valeurs de l'enum platform_type
                platform_map = {"covasim": "PYTHON", "netlogo": "NETLOGO", "gama": "GAMA"}
                platform = platform_map.get(source, "OTHER")

    # Détection générique "code available"
    if not has_code:
        has_code = bool(re.search(
            r'(?:code|software|scripts?)\s+(?:is\s+)?(?:available|accessible|'
            r'provided|deposited|shared|released)',
            text, re.IGNORECASE
        ))

    # Licence
    license_found = None
    for license_name, pattern in LICENSE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            license_found = license_name
            break

    return github_url, zenodo_url, platform, has_code, license_found


# ══════════════════════════════════════════════════════════════════════════════
# 8. DÉTECTION DES INTERVENTIONS
# ══════════════════════════════════════════════════════════════════════════════

INTERVENTION_PATTERNS = [
    r'\b(?:lockdown|confinement|stay[-\s]at[-\s]home)\b',
    r'\b(?:social\s+distancing|physical\s+distancing)\b',
    r'\b(?:mask\s+wearing|face\s+mask|masking)\b',
    r'\b(?:vaccination\s+campaign|mass\s+vaccination|vaccine\s+rollout)\b',
    r'\b(?:quarantine|isolation\s+measures?)\b',
    r'\b(?:school\s+closure|closing\s+schools)\b',
    r'\b(?:travel\s+restrictions?|border\s+closure)\b',
    r'\b(?:contact\s+tracing|test(?:ing)?[-\s]and[-\s]trace)\b',
    r'\b(?:NPI|non[-\s]pharmaceutical\s+intervention)\b',
]

def detect_interventions(text: str) -> bool:
    """Détecte si le modèle inclut des interventions non-pharmaceutiques."""
    return any(
        re.search(p, text, re.IGNORECASE)
        for p in INTERVENTION_PATTERNS
    )


# ══════════════════════════════════════════════════════════════════════════════
# 9. GÉNÉRATION DU model_id
# ══════════════════════════════════════════════════════════════════════════════

def build_model_id(formalism: str, disease_key: str,
                   authors: str, year: int) -> str:
    """
    Construit un model_id unique au format : FORMALISM_DISEASE_Author_YEAR
    Ex: SEIR_COVID19_Ferguson_2020
    """
    # Premier auteur (nom de famille uniquement)
    first_author = "Unknown"
    if authors:
        parts = authors.split(",")[0].strip().split()
        if parts:
            first_author = re.sub(r'[^A-Za-z]', '', parts[0])[:15]

    # Nettoyage du formalisme
    clean_formalism = formalism.replace("_", "").replace("MODEL", "")[:10]

    # Nettoyage de la maladie
    clean_disease = re.sub(r'[^A-Za-z0-9]', '', disease_key)[:12]

    return f"{clean_formalism}_{clean_disease}_{first_author}_{year}"


def build_model_name(formalism: str, disease_name: str,
                     authors: str, year: int,
                     geo_names: list[str]) -> str:
    """Construit un nom lisible pour le modèle."""
    first_author = authors.split(",")[0].strip().split()[-1] if authors else "Unknown"
    geo = f" — {', '.join(geo_names[:2])}" if geo_names else ""
    return f"{formalism} {first_author} {year} — {disease_name}{geo}"


# ══════════════════════════════════════════════════════════════════════════════
# 10. CALCUL DU SCORE D'EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def compute_extraction_confidence(model: ExtractedModel) -> float:
    """
    Calcule un score de confiance 0-1 pour l'extraction.
    Indique à quel point la fiche est complète et fiable.
    """
    score = 0.0

    # Formalisme détecté (20%)
    if model.formalism not in ("OTHER", ""):
        score += 0.20

    # Au moins 1 paramètre extrait (30%)
    n_params = len(model.params)
    score += min(n_params / 3, 1.0) * 0.30

    # Géographie identifiée (20%)
    if model.countries:
        score += 0.20

    # Compartiments cohérents (15%)
    if len(model.compartments) >= 3:
        score += 0.15

    # Code détecté (15%)
    if model.has_code:
        score += 0.15

    return round(score, 3)
