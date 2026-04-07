"""
nlp/nlp_v2.py
==============
Moteur NLP v2 — chargement des dictionnaires depuis PostgreSQL.

Contrairement au NLP v1 (hardcodé), cette version :
  - Charge les maladies et leurs synonymes depuis piponto.diseases
  - Charge les pays depuis piponto.geographic_scopes
  - Charge les keywords de modèles depuis piponto.keywords
  - Met en cache les dictionnaires (TTL 5 min)
  - Analyse une phrase en langage naturel et retourne un intent structuré

Usage :
    from nlp.nlp_v2 import NLPParser
    parser = NLPParser()
    intent = parser.parse("Simule COVID-19 en France 68 millions 365 jours SEIR")
    # → SimulationIntent(disease="COVID-19", country_code="FR", N=68000000, ...)
"""

import re
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / "piponto" / ".env")

logger = logging.getLogger("piponto.nlp_v2")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}

CACHE_TTL = 300   # 5 minutes


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURES DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SimulationIntent:
    """Résultat de l'analyse NLP d'une phrase de simulation."""
    # Maladie détectée
    disease_name:   Optional[str]  = None
    disease_id:     Optional[int]  = None
    disease_fr:     Optional[str]  = None
    disease_conf:   float          = 0.0

    # Géographie
    country_code:   Optional[str]  = None
    country_name:   Optional[str]  = None
    country_conf:   float          = 0.0

    # Paramètres numériques
    N:              Optional[int]  = None
    days:           Optional[int]  = None
    I0:             Optional[int]  = None

    # Modèle
    formalism:      Optional[str]  = None
    population_type:Optional[str]  = None

    # ── Données ontologiques M8/M2 (enrichies par onto_client) ──
    disease_uri:     Optional[str]  = None    # URI M8 canonique
    disease_uri_short:Optional[str] = None    # Fragment local ex: "COVID19"
    onto_disease:    Optional[dict] = None    # Données complètes M8
    typical_params:  Optional[dict] = None    # Paramètres typiques (beta, gamma...)
    disease_note:    Optional[str]  = None    # Note ontologique sur la maladie
    r0_range:        Optional[list] = None    # [R0_min, R0_max]

    # Métadonnées
    raw_text:       str            = ""
    tokens_matched: list           = field(default_factory=list)
    confidence:     float          = 0.0

    def to_api_params(self) -> dict:
        """Convertit l'intent en paramètres pour GET /models/search."""
        params = {"limit": 5}
        if self.disease_name:
            params["disease"] = self.disease_name
        if self.country_code:
            params["country"] = self.country_code
        if self.formalism:
            params["formalism"] = self.formalism
        if self.population_type:
            params["population"] = self.population_type
        return params

    def summary(self) -> str:
        parts = []
        if self.disease_name:
            parts.append(f"Maladie : {self.disease_name}")
        if self.country_name:
            parts.append(f"Pays : {self.country_name}")
        if self.N:
            parts.append(f"Population : {self.N:,}")
        if self.days:
            parts.append(f"Durée : {self.days} jours")
        if self.formalism:
            parts.append(f"Formalisme : {self.formalism}")
        return "  |  ".join(parts) if parts else "Aucun paramètre détecté"


# ══════════════════════════════════════════════════════════════════════════════
# CACHE
# ══════════════════════════════════════════════════════════════════════════════

class _Cache:
    def __init__(self):
        self._data = {}
        self._ts   = {}

    def get(self, key):
        if key in self._data and (time.time() - self._ts[key]) < CACHE_TTL:
            return self._data[key]
        return None

    def set(self, key, value):
        self._data[key] = value
        self._ts[key]   = time.time()

    def invalidate(self):
        self._data.clear()
        self._ts.clear()


_cache = _Cache()


# ══════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DEPUIS POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════

def _get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def load_disease_dict() -> list[dict]:
    """
    Charge le dictionnaire des maladies depuis PostgreSQL.
    Fallback sur la base de connaissance ontologique si BD indisponible.
    """
    cached = _cache.get("diseases")
    if cached:
        return cached

    try:
        diseases = _load_diseases_from_pg()
        _cache.set("diseases", diseases)
        return diseases
    except Exception as e:
        logger.warning(f"Impossible de charger les maladies depuis BD : {e}")
        # ── FALLBACK : base de connaissance ontologique ───────────────────────
        logger.info("Fallback → dictionnaire ontologique M8")
        return _build_disease_dict_from_onto()


def _build_disease_dict_from_onto() -> list[dict]:
    """Construit le dictionnaire maladies depuis la KB ontologique (sans BD)."""
    try:
        import sys
        for base in [Path(__file__).parent.parent, Path.home() / "piponto"]:
            if (base / "ontology" / "onto_client.py").exists():
                if str(base) not in sys.path:
                    sys.path.insert(0, str(base))
                break
        from ontology.onto_client import get_ontology, DISEASE_KB
        diseases = []
        EXTRA_SYNONYMS = {
            "COVID-19": ["covid", "covid19", "sars-cov-2", "coronavirus", "corona", "ncov"],
            "Seasonal Influenza": ["flu", "grippe", "influenza", "gripe"],
            "Malaria": ["paludisme", "malaria", "palu", "plasmodium"],
            "Tuberculosis": ["tuberculose", "tb", "mycobacterium"],
            "Measles": ["rougeole", "measles"],
            "Dengue fever": ["dengue", "denv"],
            "Cholera": ["choléra", "cholera", "vibrio"],
            "Ebola virus disease": ["ebola", "evd", "fièvre ebola"],
            "HIV/AIDS": ["vih", "hiv", "sida", "aids"],
            "Mpox": ["mpox", "monkeypox", "variole du singe"],
        }
        for i, (name_en, data) in enumerate(DISEASE_KB.items()):
            synonyms = {name_en.lower(), data.get("label_fr","").lower()}
            for syn in EXTRA_SYNONYMS.get(name_en, []):
                synonyms.add(syn)
            synonyms.discard("")
            diseases.append({
                "disease_id": i+1,
                "name_en":    name_en,
                "name_fr":    data.get("label_fr", name_en),
                "icd10":      data.get("icd10"),
                "synonyms":   sorted(synonyms, key=len, reverse=True),
            })
        _cache.set("diseases", diseases)
        return diseases
    except Exception as e2:
        logger.error(f"Fallback ontologique aussi échoué : {e2}")
        return []


def _load_diseases_from_pg() -> list[dict]:
    """Charge les maladies depuis PostgreSQL."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT disease_id, name_en, name_fr, icd10_code,
                   pathogen_name, pathogen_type, transmission_route
            FROM piponto.diseases
            ORDER BY disease_id
        """)
        rows = cur.fetchall()
    conn.close()

    diseases = []
    for row in rows:
        did, name_en, name_fr, icd10, pathogen, ptype, route = row
        synonyms = set()
        for term in [name_en, name_fr, pathogen]:
            if term:
                synonyms.add(term.lower())
                synonyms.add(term.lower().replace("-", " "))
                synonyms.add(term.lower().replace(" ", ""))
        EXTRA = {
            "COVID-19": ["covid", "covid19", "sars-cov-2", "sars cov 2",
                         "coronavirus", "corona", "ncov"],
            "Seasonal Influenza": ["flu", "grippe", "influenza", "gripe"],
            "Pandemic Influenza": ["h1n1", "h5n1", "grippe pandémique"],
            "Malaria": ["paludisme", "malaria", "palu", "plasmodium"],
            "Tuberculosis": ["tuberculose", "tb", "mycobacterium"],
            "Measles": ["rougeole", "measles", "morbillivirus"],
            "Dengue fever": ["dengue", "denv"],
            "Cholera": ["choléra", "cholera", "vibrio"],
            "Ebola virus disease": ["ebola", "evd", "fièvre ebola"],
            "HIV/AIDS": ["vih", "hiv", "sida", "aids"],
            "Mpox": ["mpox", "monkeypox", "variole du singe"],
            "Chikungunya": ["chikungunya", "chik"],
            "Zika virus disease": ["zika"],
        }
        for key, extras in EXTRA.items():
            if name_en and key.lower() in name_en.lower():
                synonyms.update(extras)
        diseases.append({
            "disease_id": did,
            "name_en":    name_en,
            "name_fr":    name_fr,
            "icd10":      icd10,
            "synonyms":   sorted(synonyms, key=len, reverse=True),
        })

    logger.debug(f"Chargé {len(diseases)} maladies depuis PostgreSQL")
    return diseases


def load_country_dict() -> list[dict]:
    """
    Charge le dictionnaire des pays depuis geographic_scopes.
    Retourne une liste de {code, name, aliases}.
    """
    cached = _cache.get("countries")
    if cached:
        return cached

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT country_code, country_name
                FROM piponto.geographic_scopes
                WHERE country_code IS NOT NULL AND country_name IS NOT NULL
                ORDER BY country_name
            """)
            rows = cur.fetchall()
        conn.close()

        # Dictionnaire de base enrichi
        BASE_ALIASES = {
            "FR": ["france", "français", "française", "french", "fr"],
            "SN": ["sénégal", "senegal", "dakar", "sn"],
            "GB": ["royaume-uni", "uk", "united kingdom", "england", "angleterre",
                   "grande-bretagne", "britain", "gb"],
            "US": ["états-unis", "usa", "united states", "america", "amérique", "us"],
            "CN": ["chine", "china", "chinois", "cn"],
            "IT": ["italie", "italy", "italian", "it"],
            "DE": ["allemagne", "germany", "deutsch", "de"],
            "ES": ["espagne", "spain", "español", "es"],
            "BR": ["brésil", "brazil", "br"],
            "IN": ["inde", "india", "indien", "in"],
            "NG": ["nigeria", "nigéria", "ng"],
            "CD": ["congo", "rdc", "rd congo", "drc"],
            "ZA": ["afrique du sud", "south africa", "za"],
            "GN": ["guinée", "guinea", "gn"],
            "ML": ["mali", "ml"],
            "CI": ["côte d'ivoire", "ivory coast", "ci"],
        }

        countries = []
        seen_codes = set()
        for code, name in rows:
            if code in seen_codes:
                continue
            seen_codes.add(code)

            aliases = set(BASE_ALIASES.get(code, []))
            aliases.add(code.lower())
            if name:
                aliases.add(name.lower())
                aliases.add(name.lower().replace("-", " "))

            countries.append({
                "code":    code,
                "name":    name,
                "aliases": sorted(aliases, key=len, reverse=True),
            })

        # Ajouter les pays connus même sans modèles
        for code, aliases in BASE_ALIASES.items():
            if code not in seen_codes:
                countries.append({
                    "code":    code,
                    "name":    aliases[0].title(),
                    "aliases": aliases,
                })

        _cache.set("countries", countries)
        logger.debug(f"Chargé {len(countries)} pays depuis PostgreSQL")
        return countries

    except Exception as e:
        logger.warning(f"Impossible de charger les pays : {e}")
        return []


def load_formalism_dict() -> dict[str, list[str]]:
    """Dictionnaire des formalismes et leurs alias textuels."""
    return {
        "SEIR":   ["seir", "seir model", "modèle seir", "exposé infectieux rétabli"],
        "SIR":    ["sir", "sir model", "modèle sir", "susceptible infectieux rétabli"],
        "SEIRD":  ["seird", "seir avec décès", "seir mortalité"],
        "SEIRS":  ["seirs", "seir réinfection", "perte immunité"],
        "SIS":    ["sis", "réinfection directe"],
        "ABM":    ["abm", "agent-based", "agent based", "individu", "individuel",
                   "multi-agent", "multi agent", "ibm"],
        "NETWORK":["network", "réseau", "réseau de contacts", "contact network"],
        "METAPOPULATION": ["metapopulation", "métapopulation", "patches", "spatial",
                           "spatial patches", "régions"],
        "STOCHASTIC_SEIR": ["stochastique", "stochastic", "aléatoire"],
    }


def load_population_dict() -> dict[str, list[str]]:
    """Dictionnaire des types de population."""
    return {
        "GENERAL":           ["général", "generale", "générale", "population générale",
                               "tout le monde", "general", "overall"],
        "SCHOOL":            ["école", "scolaire", "enfants", "élèves", "school",
                               "children", "schoolchildren", "enfants scolarisés"],
        "ELDERLY":           ["personnes âgées", "seniors", "âgés", "elderly",
                               "vieux", "retraités", "+65", "65 ans"],
        "HEALTHCARE_WORKERS":["soignants", "médecins", "infirmiers", "personnel soignant",
                               "healthcare", "health workers", "professionnels de santé"],
        "URBAN":             ["urbain", "ville", "métropole", "urban", "city"],
        "RURAL":             ["rural", "campagne", "village", "rural area"],
        "CHILDREN_UNDER5":   ["enfants moins de 5", "moins de 5 ans", "nourrissons",
                               "under 5", "children under 5"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# MOTEUR NLP
# ══════════════════════════════════════════════════════════════════════════════

class NLPParser:
    """
    Analyseur NLP pour les phrases de simulation épidémique.
    Charge ses dictionnaires depuis PostgreSQL au premier appel.
    """

    def __init__(self, rdf_dir: str = None):
        self._diseases    = None
        self._countries   = None
        self._formalisms  = None
        self._populations = None
        self._onto        = None
        self._rdf_dir     = rdf_dir

    def _ensure_loaded(self):
        if self._diseases is None:
            self._diseases    = load_disease_dict()
            self._countries   = load_country_dict()
            self._formalisms  = load_formalism_dict()
            self._populations = load_population_dict()

        if self._onto is None:
            try:
                import sys
                for base in [Path(__file__).parent.parent,
                             Path.home() / "piponto"]:
                    if (base / "ontology" / "onto_client.py").exists():
                        if str(base) not in sys.path:
                            sys.path.insert(0, str(base))
                        break
                from ontology.onto_client import get_ontology
                rdf_dir = self._rdf_dir or str(Path.home() / "piponto" / "ontology")
                self._onto = get_ontology(rdf_dir)
                logger.info(f"Ontologie chargée : {self._onto.get_stats()['modules_loaded']} modules")
            except Exception as e:
                logger.warning(f"Ontologie non disponible : {e}")
                self._onto = None

    def parse(self, text: str) -> SimulationIntent:
        """
        Analyse une phrase en langage naturel et retourne un SimulationIntent.

        Exemples :
            "Simule COVID-19 France 68 millions 365 jours"
            "Épidémie de paludisme au Sénégal population rurale"
            "SEIR grippe 500000 personnes 180 jours"
        """
        self._ensure_loaded()
        intent = SimulationIntent(raw_text=text)
        text_lower = text.lower().strip()
        matched = []

        # ── Détection maladie ─────────────────────────────────────────────────
        best_disease = None
        best_conf    = 0.0
        for d in self._diseases:
            for syn in d["synonyms"]:
                if syn and syn in text_lower:
                    # Score basé sur la longueur du match (plus long = plus précis)
                    conf = min(1.0, len(syn) / 15 + 0.5)
                    if conf > best_conf:
                        best_conf    = conf
                        best_disease = d
                        matched.append(f"maladie:{syn}")
                        break

        if best_disease:
            intent.disease_name  = best_disease["name_en"]
            intent.disease_id    = best_disease["disease_id"]
            intent.disease_fr    = best_disease["name_fr"]
            intent.disease_conf  = best_conf

        # ── Détection pays ────────────────────────────────────────────────────
        best_country = None
        best_cc      = 0.0
        for c in self._countries:
            for alias in c["aliases"]:
                if alias and alias in text_lower:
                    conf = min(1.0, len(alias) / 10 + 0.4)
                    if conf > best_cc:
                        best_cc      = conf
                        best_country = c
                        matched.append(f"pays:{alias}")
                        break

        if best_country:
            intent.country_code = best_country["code"]
            intent.country_name = best_country["name"]
            intent.country_conf = best_cc

        # ── Détection formalisme ──────────────────────────────────────────────
        for form, aliases in self._formalisms.items():
            for alias in aliases:
                if alias in text_lower:
                    intent.formalism = form
                    matched.append(f"formalisme:{alias}")
                    break
            if intent.formalism:
                break

        # ── Détection population type ─────────────────────────────────────────
        for pop_type, aliases in self._populations.items():
            for alias in aliases:
                if alias in text_lower:
                    intent.population_type = pop_type
                    matched.append(f"population:{alias}")
                    break
            if intent.population_type:
                break

        # ── Extraction des nombres ─────────────────────────────────────────────
        # Chercher "X millions", "X milliards", nombres bruts, "X jours/ans"
        intent.N    = self._extract_population(text_lower)
        intent.days = self._extract_days(text_lower)
        intent.I0   = self._extract_i0(text_lower)

        if intent.N:    matched.append(f"N:{intent.N:,}")
        if intent.days: matched.append(f"jours:{intent.days}")
        if intent.I0:   matched.append(f"I0:{intent.I0}")

        # ── Score de confiance global ─────────────────────────────────────────
        scores = []
        if intent.disease_name:  scores.append(intent.disease_conf)
        if intent.country_code:  scores.append(intent.country_conf)
        if intent.N:             scores.append(0.7)
        if intent.days:          scores.append(0.6)
        if intent.formalism:     scores.append(0.8)

        intent.confidence     = round(sum(scores) / max(len(scores), 1), 2)
        intent.tokens_matched = matched

        # ── Enrichissement ontologique (M8 + M2) ─────────────────────────────
        if self._onto and intent.disease_name:
            onto_data = self._onto.get_disease_by_search(intent.disease_name)
            if onto_data:
                # URI canonique M8
                intent.disease_uri      = onto_data.get("uri")
                intent.disease_uri_short = onto_data.get("uri","").split("#")[-1]
                # Formalisme recommandé par l'ontologie
                rec = onto_data.get("best_formalism", [])
                if rec and not intent.formalism:
                    intent.formalism = rec[0]
                    matched.append(f"formalisme_onto:{rec[0]}")
                intent.onto_disease     = onto_data
                # Paramètres typiques de la maladie
                if onto_data.get("key_params"):
                    intent.typical_params = onto_data["key_params"]
                # Note ontologique sur la maladie
                intent.disease_note     = onto_data.get("note", "")
                # R₀ range
                intent.r0_range         = onto_data.get("r0_range")

        return intent

    # ── Helpers extraction numérique ─────────────────────────────────────────

    def _extract_population(self, text: str) -> Optional[int]:
        """Extrait la taille de population depuis le texte."""
        # "68 millions", "68M", "68 000 000", "1.4 milliard"
        patterns = [
            (r'(\d+(?:[.,]\d+)?)\s*milliard', 1e9),
            (r'(\d+(?:[.,]\d+)?)\s*billion',  1e9),
            (r'(\d+(?:[.,]\d+)?)\s*million',  1e6),
            (r'(\d+(?:[.,]\d+)?)\s*M\b',      1e6),
            (r'(\d+(?:[.,]\d+)?)\s*k\b',      1e3),
            (r'(\d+(?:[.,]\d+)?)\s*mille\b',  1e3),
        ]
        for pattern, multiplier in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                val = float(m.group(1).replace(',', '.'))
                return int(val * multiplier)

        # Nombre brut ≥ 1000 (mais pas une année)
        for m in re.finditer(r'\b(\d{4,})\b', text):
            val = int(m.group(1))
            if 1000 <= val <= 10_000_000_000 and not (1900 <= val <= 2100):
                return val

        return None

    def _extract_days(self, text: str) -> Optional[int]:
        """Extrait la durée en jours."""
        # "365 jours", "2 ans", "6 mois", "1 year", "3 months"
        patterns = [
            (r'(\d+)\s*ans?\b',        365),
            (r'(\d+)\s*year',          365),
            (r'(\d+)\s*mois\b',        30),
            (r'(\d+)\s*month',         30),
            (r'(\d+)\s*semaine',       7),
            (r'(\d+)\s*week',          7),
            (r'(\d+)\s*jour',          1),
            (r'(\d+)\s*day',           1),
        ]
        for pattern, multiplier in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return int(m.group(1)) * multiplier
        return None

    def _extract_i0(self, text: str) -> Optional[int]:
        """Extrait le nombre initial d'infectieux."""
        m = re.search(
            r'(?:i0|infectieux\s+initiaux?|cas\s+initiaux?|départ)\s*[=:]\s*(\d+)',
            text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        # "commence avec 10 cas", "10 cas au départ"
        m = re.search(r'(\d+)\s*cas\s+(?:initiaux?|au\s+départ|de\s+départ)',
                      text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def get_disease_list(self) -> list[dict]:
        """Retourne la liste des maladies disponibles (pour l'UI)."""
        self._ensure_loaded()
        return [{"id": d["disease_id"], "en": d["name_en"], "fr": d["name_fr"]}
                for d in self._diseases]

    def get_country_list(self) -> list[dict]:
        """Retourne la liste des pays disponibles (pour l'UI)."""
        self._ensure_loaded()
        return [{"code": c["code"], "name": c["name"]} for c in self._countries]

    def refresh(self):
        """Vide le cache et recharge depuis PostgreSQL."""
        _cache.invalidate()
        self._diseases = self._countries = self._formalisms = self._populations = None
        self._ensure_loaded()


# ── Test standalone ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = NLPParser()

    tests = [
        "Simule une épidémie de COVID-19 en France avec 68 millions de personnes sur 365 jours",
        "Épidémie de paludisme au Sénégal, population rurale, 2 millions, 6 mois, modèle SEIR",
        "grippe saisonnière UK 60M 180 jours stochastique",
        "ABM Ebola Congo 5 millions 120 jours soignants",
        "SIR influenza 500000 personnes",
        "COVID France",
    ]

    print("\n" + "═"*65)
    print("  NLP v2 — Tests de parsing")
    print("═"*65)

    for t in tests:
        intent = parser.parse(t)
        print(f"\n  Texte : \"{t}\"")
        print(f"  → {intent.summary()}")
        print(f"  → Confiance : {intent.confidence:.2f}")
        print(f"  → Tokens    : {intent.tokens_matched}")
        print(f"  → API params: {intent.to_api_params()}")
