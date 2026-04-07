"""
piponto_nlp/nlp_extractor.py
============================
Module NLP de PIPOnto — Extraction d'entités et résolution vers URIs ontologiques.

Approche : règles + dictionnaires (rule-based NER).
Justification scientifique : pour un domaine aussi contrôlé que l'épidémiologie,
les systèmes basés sur des dictionnaires terminologiques surpassent les modèles
génériques (cf. MedCAT, QuickUMLS, MetaMap). Cf. Lample et al. 2016.

Pipeline :
    Texte brut → Normalisation → Détection d'entités → Résolution URIs → Score confiance

Entités extraites :
    - Disease       : maladie infectieuse (COVID-19, grippe, paludisme...)
    - Geography     : lieu géographique (Paris, Sénégal, Thiès...)
    - Population    : groupe de population (écoliers, personnes âgées...)
    - Intervention  : intervention de santé publique (confinement, vaccination...)
    - TimeFrame     : horizon temporel (6 mois, 1 an...)

Usage :
    from nlp_extractor import PIPOntoNLPExtractor
    extractor = PIPOntoNLPExtractor()
    result = extractor.extract("simule COVID-19 à Paris chez les écoliers")
    print(result)

Auteur : PIPOnto Project
Version : 1.0
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# NAMESPACES PIPONTO
# ══════════════════════════════════════════════════════════════════════════════

NS = {
    "m2": "http://www.pacadi.org/these/piponto/module2#",
    "m4": "http://www.pacadi.org/these/piponto/module4#",
    "m5": "http://www.pacadi.org/these/piponto/module5#",
    "m8": "http://www.pacadi.org/these/piponto/module8#",
}


def uri(module: str, local: str) -> str:
    """Construit une URI PIPOnto complète."""
    return NS[module] + local


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURES DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtractedEntity:
    """Une entité extraite depuis le texte."""
    label: str          # Texte original dans la requête
    category: str       # Disease | Geography | Population | Intervention | TimeFrame
    uri_m8: Optional[str] = None    # URI M8 (domaine épidémio)
    uri_m2: Optional[str] = None    # URI M2 (contexte modèle)
    confidence: float = 1.0         # Score de confiance 0-1
    span_start: int = 0             # Position dans le texte
    span_end: int = 0


@dataclass
class NLPExtractionResult:
    """Résultat complet d'une extraction NLP."""
    query_raw: str                              # Requête originale
    query_normalized: str                       # Requête normalisée
    entities: list[ExtractedEntity] = field(default_factory=list)

    # Entités principales résolues
    disease: Optional[ExtractedEntity] = None
    geography: Optional[ExtractedEntity] = None
    population: Optional[ExtractedEntity] = None
    intervention: Optional[ExtractedEntity] = None
    timeframe: Optional[ExtractedEntity] = None

    # URIs candidats M2 classés par pertinence
    candidate_model_uris: list[dict] = field(default_factory=list)

    # Score de confiance global 0-1
    global_confidence: float = 0.0

    # Message d'ambiguïté ou d'avertissement
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Sérialise le résultat pour l'API REST."""
        return {
            "query_raw": self.query_raw,
            "query_normalized": self.query_normalized,
            "entities": [
                {
                    "label": e.label,
                    "category": e.category,
                    "uri_m8": e.uri_m8,
                    "uri_m2": e.uri_m2,
                    "confidence": round(e.confidence, 3),
                }
                for e in self.entities
            ],
            "resolved": {
                "disease": {
                    "label": self.disease.label,
                    "uri_m8": self.disease.uri_m8,
                } if self.disease else None,
                "geography": {
                    "label": self.geography.label,
                    "uri_m8": self.geography.uri_m8,
                } if self.geography else None,
                "population": {
                    "label": self.population.label,
                    "uri_m8": self.population.uri_m8,
                    "uri_m2": self.population.uri_m2,
                } if self.population else None,
                "intervention": {
                    "label": self.intervention.label,
                    "uri_m8": self.intervention.uri_m8,
                } if self.intervention else None,
            },
            "candidate_models": self.candidate_model_uris,
            "global_confidence": round(self.global_confidence, 3),
            "warnings": self.warnings,
        }

    def to_sparql_params(self) -> dict:
        """Extrait les paramètres pour la génération de requête SPARQL."""
        return {
            "disease_uri": self.disease.uri_m8 if self.disease else None,
            "geography_uri": self.geography.uri_m8 if self.geography else None,
            "population_uri_m2": self.population.uri_m2 if self.population else None,
            "population_uri_m8": self.population.uri_m8 if self.population else None,
            "intervention_uri": self.intervention.uri_m8 if self.intervention else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DICTIONNAIRES TERMINOLOGIQUES
# ══════════════════════════════════════════════════════════════════════════════

class DiseaseOntology:
    """
    Dictionnaire terminologique des maladies infectieuses.
    Construit à partir de M8 + MeSH + ICD-10.
    Format : { terme_normalisé : (uri_m8, label_officiel, mots_clés_m2) }
    """
    TERMS = {
        # COVID-19 et variants
        "covid": (uri("m8", "COVID19"), "COVID-19",
                  ["covid", "coronavirus", "sars-cov-2", "sars cov 2"]),
        "covid-19": (uri("m8", "COVID19"), "COVID-19",
                     ["covid", "coronavirus", "sars-cov-2"]),
        "covid 19": (uri("m8", "COVID19"), "COVID-19",
                     ["covid", "coronavirus"]),
        "coronavirus": (uri("m8", "COVID19"), "COVID-19",
                        ["covid", "coronavirus"]),
        "sars-cov-2": (uri("m8", "COVID19"), "COVID-19",
                       ["covid", "coronavirus", "sars"]),
        "sars cov 2": (uri("m8", "COVID19"), "COVID-19",
                       ["covid", "coronavirus"]),
        "sarscov2": (uri("m8", "COVID19"), "COVID-19",
                     ["covid", "coronavirus"]),

        # Grippe / Influenza
        "grippe": (uri("m8", "SeasonalInfluenza"), "Grippe saisonnière",
                   ["grippe", "influenza", "flu"]),
        "influenza": (uri("m8", "SeasonalInfluenza"), "Grippe saisonnière",
                      ["influenza", "grippe"]),
        "flu": (uri("m8", "SeasonalInfluenza"), "Grippe saisonnière",
                ["influenza", "grippe"]),

        # Paludisme / Malaria
        "paludisme": (uri("m8", "Malaria"), "Paludisme",
                      ["paludisme", "malaria", "plasmodium"]),
        "malaria": (uri("m8", "Malaria"), "Paludisme",
                    ["malaria", "paludisme"]),
        "palu": (uri("m8", "Malaria"), "Paludisme",
                 ["paludisme", "malaria"]),

        # Rougeole / Measles
        "rougeole": (uri("m8", "Measles"), "Rougeole",
                     ["rougeole", "measles", "morbilli"]),
        "measles": (uri("m8", "Measles"), "Rougeole",
                    ["measles", "rougeole"]),

        # Ebola
        "ebola": (uri("m8", "EbolaDiseaseInstance"), "Maladie à virus Ebola",
                  ["ebola", "mvb"]),
        "mvb": (uri("m8", "EbolaDiseaseInstance"), "Maladie à virus Ebola",
                ["ebola"]),

        # Dengue
        "dengue": (uri("m8", "Dengue"), "Dengue",
                   ["dengue", "arbovirose"]),
    }

    @classmethod
    def lookup(cls, token: str):
        key = token.lower().strip()
        if key in cls.TERMS:
            return cls.TERMS[key]
        import unicodedata
        def strip_acc(s):
            return "".join(c for c in unicodedata.normalize("NFD", s)
                           if unicodedata.category(c) != "Mn")
        key_na = strip_acc(key)
        for term, value in cls.TERMS.items():
            if strip_acc(term) == key_na:
                return value
        return None


class GeographyOntology:
    """
    Dictionnaire terminologique des entités géographiques.
    Format : { terme_normalisé : (uri_m8, label_officiel, pays_code) }
    """
    TERMS = {
        # France et villes
        "paris": (uri("m8", "Paris"), "Paris", "FR"),
        "france": (uri("m8", "France"), "France", "FR"),
        "france metropolitaine": (uri("m8", "France"), "France métropolitaine", "FR"),

        # Sénégal et villes
        "senegal": (uri("m8", "Senegal"), "Sénégal", "SN"),
        "sénégal": (uri("m8", "Senegal"), "Sénégal", "SN"),
        "thies": (uri("m8", "Thies"), "Thiès", "SN"),
        "thiès": (uri("m8", "Thies"), "Thiès", "SN"),
        "thies region": (uri("m8", "Thies"), "Région de Thiès", "SN"),
        "region de thies": (uri("m8", "Thies"), "Région de Thiès", "SN"),
        "region de thiès": (uri("m8", "Thies"), "Région de Thiès", "SN"),

        # Canada
        "montreal": (uri("m8", "Montreal"), "Montréal", "CA"),
        "montréal": (uri("m8", "Montreal"), "Montréal", "CA"),
        "canada": (uri("m8", "Montreal"), "Canada", "CA"),  # fallback

        # Afrique
        "afrique subsaharienne": (uri("m8", "AfriqueSubsaharienne"),
                                   "Afrique subsaharienne", "AF"),
        "afrique": (uri("m8", "AfriqueSubsaharienne"),
                    "Afrique subsaharienne", "AF"),
        "afrique de l ouest": (uri("m8", "AfriqueSubsaharienne"),
                                "Afrique de l'Ouest", "AF"),
        "afrique de louest": (uri("m8", "AfriqueSubsaharienne"),
                               "Afrique de l'Ouest", "AF"),

        # Global
        "mondial": (None, "Mondial", "WW"),
        "monde": (None, "Mondial", "WW"),
        "global": (None, "Mondial", "WW"),
        "international": (None, "Mondial", "WW"),
    }

    @classmethod
    def lookup(cls, token: str):
        key = token.lower().strip()
        if key in cls.TERMS:
            return cls.TERMS[key]
        import unicodedata
        def strip_acc(s):
            return "".join(c for c in unicodedata.normalize("NFD", s)
                           if unicodedata.category(c) != "Mn")
        key_na = strip_acc(key)
        for term, value in cls.TERMS.items():
            if strip_acc(term) == key_na:
                return value
        return None


class PopulationOntology:
    """
    Dictionnaire terminologique des groupes de population.
    Format : { terme_normalisé : (uri_m8, uri_m2, label_officiel) }
    """
    TERMS = {
        # Population scolaire
        "ecoliers": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                     "Enfants scolarisés"),
        "élèves": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                   "Enfants scolarisés"),
        "eleves": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                   "Enfants scolarisés"),
        "enfants": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                    "Enfants scolarisés"),
        "scolaire": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                     "Enfants scolarisés"),
        "population scolaire": (uri("m8", "SchoolChildren"),
                                 uri("m2", "Pop_School"), "Enfants scolarisés"),
        "collegiens": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                       "Collégiens"),
        "lycéens": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                    "Lycéens"),
        "lyceens": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                    "Lycéens"),
        "ecole": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                  "Milieu scolaire"),
        "école": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                  "Milieu scolaire"),

        # Personnes âgées
        "personnes agees": (uri("m8", "ElderlyPop_France"),
                             uri("m2", "Pop_Elderly"), "Personnes âgées (65+)"),
        "personnes âgées": (uri("m8", "ElderlyPop_France"),
                             uri("m2", "Pop_Elderly"), "Personnes âgées (65+)"),
        "seniors": (uri("m8", "ElderlyPop_France"),
                    uri("m2", "Pop_Elderly"), "Seniors"),
        "ages": (uri("m8", "ElderlyPop_France"),
                 uri("m2", "Pop_Elderly"), "Personnes âgées"),
        "âgés": (uri("m8", "ElderlyPop_France"),
                 uri("m2", "Pop_Elderly"), "Personnes âgées"),
        "agees": (uri("m8", "ElderlyPop_France"),
                  uri("m2", "Pop_Elderly"), "Personnes âgées"),
        "ehpad": (uri("m8", "ElderlyPop_France"),
                  uri("m2", "Pop_Elderly"), "Résidents EHPAD"),
        "retraites": (uri("m8", "ElderlyPop_France"),
                      uri("m2", "Pop_Elderly"), "Retraités"),
        "retraités": (uri("m8", "ElderlyPop_France"),
                      uri("m2", "Pop_Elderly"), "Retraités"),

        # Soignants
        "soignants": (uri("m8", "HealthcareWorkers"),
                      uri("m2", "Pop_Healthcare"), "Personnel soignant"),
        "medecins": (uri("m8", "HealthcareWorkers"),
                     uri("m2", "Pop_Healthcare"), "Médecins"),
        "médecins": (uri("m8", "HealthcareWorkers"),
                     uri("m2", "Pop_Healthcare"), "Médecins"),
        "infirmiers": (uri("m8", "HealthcareWorkers"),
                       uri("m2", "Pop_Healthcare"), "Infirmiers"),
        "personnel medical": (uri("m8", "HealthcareWorkers"),
                               uri("m2", "Pop_Healthcare"), "Personnel médical"),
        "personnel médical": (uri("m8", "HealthcareWorkers"),
                               uri("m2", "Pop_Healthcare"), "Personnel médical"),
        "hopital": (uri("m8", "HealthcareWorkers"),
                    uri("m2", "Pop_Healthcare"), "Milieu hospitalier"),
        "hôpital": (uri("m8", "HealthcareWorkers"),
                    uri("m2", "Pop_Healthcare"), "Milieu hospitalier"),

        # Population urbaine
        "urbaine": (uri("m8", "UrbanPopulationGroup"),
                    uri("m2", "Pop_Urban"), "Population urbaine"),
        "urban": (uri("m8", "UrbanPopulationGroup"),
                  uri("m2", "Pop_Urban"), "Population urbaine"),
        "ville": (uri("m8", "UrbanPopulationGroup"),
                  uri("m2", "Pop_Urban"), "Population urbaine"),
        "metropole": (uri("m8", "UrbanPopulationGroup"),
                      uri("m2", "Pop_Urban"), "Métropole"),
        "métropole": (uri("m8", "UrbanPopulationGroup"),
                      uri("m2", "Pop_Urban"), "Métropole"),

        # Population rurale
        "rurale": (uri("m8", "RuralPop_Thies"),
                   uri("m2", "Pop_Rural_Africa"), "Population rurale"),
        "rural": (uri("m8", "RuralPop_Thies"),
                  uri("m2", "Pop_Rural_Africa"), "Population rurale"),
        "campagne": (uri("m8", "RuralPop_Thies"),
                     uri("m2", "Pop_Rural_Africa"), "Zone rurale"),
        "village": (uri("m8", "RuralPop_Thies"),
                    uri("m2", "Pop_Rural_Africa"), "Village"),

        # English terms
        "school": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                   "School children"),
        "schools": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                    "School children"),
        "schoolchildren": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                           "School children"),
        "students": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                     "Students"),
        "children": (uri("m8", "SchoolChildren"), uri("m2", "Pop_School"),
                     "Children"),
        "elderly": (uri("m8", "ElderlyPop_France"), uri("m2", "Pop_Elderly"),
                    "Elderly population"),
        "older adults": (uri("m8", "ElderlyPop_France"), uri("m2", "Pop_Elderly"),
                         "Older adults"),
        "healthcare workers": (uri("m8", "HealthcareWorkers"),
                                uri("m2", "Pop_Healthcare"),
                                "Healthcare workers"),
        "urban": (uri("m8", "UrbanPopulationGroup"), uri("m2", "Pop_Urban"),
                  "Urban population"),
        "rural": (uri("m8", "RuralPop_Thies"), uri("m2", "Pop_Rural_Africa"),
                  "Rural population"),
    }

    @classmethod
    def lookup(cls, token: str):
        key = token.lower().strip()
        if key in cls.TERMS:
            return cls.TERMS[key]
        import unicodedata
        def strip_acc(s):
            return "".join(c for c in unicodedata.normalize("NFD", s)
                           if unicodedata.category(c) != "Mn")
        key_na = strip_acc(key)
        for term, value in cls.TERMS.items():
            if strip_acc(term) == key_na:
                return value
        return None


class InterventionOntology:
    """Dictionnaire des interventions de santé publique."""
    TERMS = {
        "confinement": (uri("m8", "Lockdown"), "Confinement"),
        "lockdown": (uri("m8", "Lockdown"), "Confinement"),
        "vaccination": (uri("m8", "VaccinationCampaign"), "Campagne de vaccination"),
        "vaccin": (uri("m8", "VaccinationCampaign"), "Vaccination"),
        "vaccine": (uri("m8", "VaccinationCampaign"), "Vaccination"),
        "quarantaine": (uri("m8", "Quarantine"), "Quarantaine"),
        "quarantine": (uri("m8", "Quarantine"), "Quarantaine"),
        "isolement": (uri("m8", "Quarantine"), "Isolement"),
        "distanciation": (uri("m8", "SocialDistancing"), "Distanciation sociale"),
        "distanciation sociale": (uri("m8", "SocialDistancing"),
                                   "Distanciation sociale"),
        "masque": (uri("m8", "MaskWearing"), "Port du masque"),
        "masques": (uri("m8", "MaskWearing"), "Port du masque"),
        "fermeture": (uri("m8", "Lockdown"), "Fermeture"),
        "fermeture ecoles": (uri("m8", "Lockdown"), "Fermeture des écoles"),
        "fermeture des ecoles": (uri("m8", "Lockdown"), "Fermeture des écoles"),
    }

    @classmethod
    def lookup(cls, token: str):
        key = token.lower().strip()
        if key in cls.TERMS:
            return cls.TERMS[key]
        import unicodedata
        def strip_acc(s):
            return "".join(c for c in unicodedata.normalize("NFD", s)
                           if unicodedata.category(c) != "Mn")
        key_na = strip_acc(key)
        for term, value in cls.TERMS.items():
            if strip_acc(term) == key_na:
                return value
        return None


# ══════════════════════════════════════════════════════════════════════════════
# MODÈLES CANDIDATS M2 — TABLE DE SCORING
# Format : { disease_key: [ {model_id, label, pop_keys, geo_keys, score_base} ] }
# ══════════════════════════════════════════════════════════════════════════════

CANDIDATE_MODELS = {
    uri("m8", "COVID19"): [
        {
            "model_id": "SEIR_School_Cauchemez_2011",
            "uri": uri("m2", "SEIR_School_Cauchemez_2011"),
            "label": "SEIR Cauchemez 2011 — Scolaire",
            "population_match": [uri("m8", "SchoolChildren"),
                                  uri("m2", "Pop_School")],
            "geography_match": [],   # validé hors France — score réduit
            "score_base": 0.88,
        },
        {
            "model_id": "SEIR_COVID_France_Roux_2020",
            "uri": uri("m2", "SEIR_COVID_France_Roux_2020"),
            "label": "SEIR Roux 2020 — France",
            "population_match": [],
            "geography_match": [uri("m8", "France"), uri("m8", "Paris")],
            "score_base": 0.85,
        },
        {
            "model_id": "SEIR_COVID_Ferguson_2020",
            "uri": uri("m2", "SEIR_COVID_Ferguson_2020"),
            "label": "SEIR Ferguson 2020 — UK/Global",
            "population_match": [],
            "geography_match": [],
            "score_base": 0.97,
        },
        {
            "model_id": "ABM_COVID_Urban_Kerr_2021",
            "uri": uri("m2", "ABM_COVID_Urban_Kerr_2021"),
            "label": "Covasim ABM Kerr 2021 — Urbain",
            "population_match": [uri("m8", "UrbanPopulationGroup"),
                                  uri("m8", "SchoolChildren"),
                                  uri("m2", "Pop_Urban")],
            "geography_match": [],
            "score_base": 0.93,
        },
    ],
    uri("m8", "SeasonalInfluenza"): [
        {
            "model_id": "SEIR_School_Cauchemez_2011",
            "uri": uri("m2", "SEIR_School_Cauchemez_2011"),
            "label": "SEIR Cauchemez 2011 — Scolaire (grippe)",
            "population_match": [uri("m8", "SchoolChildren")],
            "geography_match": [],
            "score_base": 0.88,
        },
        {
            "model_id": "SIR_General_KermackMcKendrick_1927",
            "uri": uri("m2", "SIR_General_KermackMcKendrick_1927"),
            "label": "SIR Kermack-McKendrick 1927",
            "population_match": [],
            "geography_match": [],
            "score_base": 1.0,
        },
    ],
    uri("m8", "Malaria"): [
        {
            "model_id": "SEIRS_Malaria_Senegal_Diallo_2022",
            "uri": uri("m2", "SEIRS_Malaria_Senegal_Diallo_2022"),
            "label": "SEIRS Diallo 2022 — Paludisme Sénégal",
            "population_match": [uri("m8", "RuralPop_Thies")],
            "geography_match": [uri("m8", "Senegal"), uri("m8", "Thies")],
            "score_base": 0.82,
        },
    ],
    uri("m8", "Measles"): [
        {
            "model_id": "SIR_General_KermackMcKendrick_1927",
            "uri": uri("m2", "SIR_General_KermackMcKendrick_1927"),
            "label": "SIR Kermack-McKendrick 1927",
            "population_match": [],
            "geography_match": [],
            "score_base": 1.0,
        },
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTEUR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class PIPOntoNLPExtractor:
    """
    Extracteur d'entités NLP pour PIPOnto.

    Méthode : reconnaissance d'entités nommées basée sur des règles et
    dictionnaires terminologiques du domaine (rule-based NER).

    Avantages par rapport à un modèle ML générique :
    - Précision maximale sur le vocabulaire contrôlé de l'épidémiologie
    - Transparence : chaque décision est traçable
    - Pas de données d'entraînement nécessaires
    - Robuste aux nouveaux termes ajoutés dans l'ontologie
    """

    # Patterns regex pour la détection contextuelle
    PATTERNS = {
        "population_trigger": re.compile(
            r"\b(?:chez\s+les?|dans\s+la\s+population\s+(?:des?)?|"
            r"pour\s+les?|population\s+(?:de|des?))\s+(\w[\w\s]*)",
            re.IGNORECASE | re.UNICODE,
        ),
        "geography_trigger": re.compile(
            r"\b(?:à|a|dans|en|au|sur|pour\s+la?\s+région\s+de?|"
            r"region\s+de?|province\s+de?)\s+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[a-zà-ü]+)*)",
            re.IGNORECASE | re.UNICODE,
        ),
        "timeframe": re.compile(
            r"\b(?:sur\s+)?(\d+)\s*(jours?|semaines?|mois|ans?|années?)\b",
            re.IGNORECASE,
        ),
        "simulation_verbs": re.compile(
            r"\b(?:simul[eo]|modélis[eo]|modelis[eo]|prédis|predis|"
            r"estim[eo]|project[eo]|analys[eo])\b",
            re.IGNORECASE,
        ),
    }

    def __init__(self):
        self._disease_ont = DiseaseOntology()
        self._geo_ont = GeographyOntology()
        self._pop_ont = PopulationOntology()
        self._int_ont = InterventionOntology()

    # ──────────────────────────────────────────────────────────────
    # API PUBLIQUE
    # ──────────────────────────────────────────────────────────────

    def extract(self, query: str) -> NLPExtractionResult:
        """
        Point d'entrée principal.

        Args:
            query: Requête en langage naturel (français ou anglais).

        Returns:
            NLPExtractionResult avec toutes les entités résolues.
        """
        normalized = self._normalize(query)
        result = NLPExtractionResult(
            query_raw=query,
            query_normalized=normalized,
        )

        # Étape 1 : détecter les entités
        self._extract_disease(normalized, result)
        self._extract_geography(normalized, result)
        self._extract_population(normalized, result)
        self._extract_intervention(normalized, result)
        self._extract_timeframe(normalized, result)

        # Étape 2 : résoudre les candidats M2
        self._resolve_model_candidates(result)

        # Étape 2b : inférence population depuis géographie si absente
        if result.population is None and result.geography is not None:
            self._infer_population_from_geography(result)

        # Étape 3 : calculer la confiance globale
        self._compute_confidence(result)

        # Étape 4 : détecter les ambiguïtés
        self._check_warnings(result)

        return result

    # ──────────────────────────────────────────────────────────────
    # NORMALISATION
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Normalise le texte : minuscules, suppression accents optionnelle,
        suppression ponctuation sauf tirets, espaces multiples.
        """
        # Minuscules
        text = text.lower().strip()
        # Supprimer ponctuation sauf tirets et apostrophes utiles
        text = re.sub(r"[^\w\s\-àâäéèêëîïôùûüç]", " ", text)
        # Normaliser espaces
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _remove_accents(text: str) -> str:
        """Supprime les accents pour comparaison floue."""
        return "".join(
            c for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )

    # ──────────────────────────────────────────────────────────────
    # EXTRACTION MALADIE
    # ──────────────────────────────────────────────────────────────

    def _extract_disease(self, text: str, result: NLPExtractionResult):
        """
        Stratégie :
        1. Recherche exacte dans le dictionnaire (termes simples et composés)
        2. Recherche par tokens individuels
        3. Recherche sans accents (robustesse orthographique)
        """
        # Chercher les bigrams et trigrams d'abord (termes composés prioritaires)
        tokens = text.split()
        for window in range(3, 0, -1):
            for i in range(len(tokens) - window + 1):
                candidate = " ".join(tokens[i:i + window])
                match = self._disease_ont.lookup(candidate)
                if match:
                    uri_m8, label, keywords = match
                    entity = ExtractedEntity(
                        label=candidate,
                        category="Disease",
                        uri_m8=uri_m8,
                        confidence=1.0 if window > 1 else 0.95,
                        span_start=i,
                        span_end=i + window,
                    )
                    result.entities.append(entity)
                    if result.disease is None:
                        result.disease = entity
                    return

        # Recherche sans accents (fallback)
        text_no_accent = self._remove_accents(text)
        for term, value in self._disease_ont.TERMS.items():
            term_no_accent = self._remove_accents(term)
            if term_no_accent in text_no_accent:
                uri_m8, label, keywords = value
                entity = ExtractedEntity(
                    label=term,
                    category="Disease",
                    uri_m8=uri_m8,
                    confidence=0.85,  # léger dégrading pour match sans accent
                )
                result.entities.append(entity)
                if result.disease is None:
                    result.disease = entity
                return

    # ──────────────────────────────────────────────────────────────
    # EXTRACTION GÉOGRAPHIE
    # ──────────────────────────────────────────────────────────────

    def _extract_geography(self, text: str, result: NLPExtractionResult):
        """
        Stratégie :
        1. Pattern regex pour capturer ce qui suit "à", "dans", "en", "au"...
        2. Recherche directe dans le dictionnaire
        3. Fallback sans accents
        """
        # Pattern contextuel d'abord
        for m in self.PATTERNS["geography_trigger"].finditer(text):
            candidate = m.group(1).strip().lower()
            # Essayer longueur décroissante
            words = candidate.split()
            for w in range(len(words), 0, -1):
                phrase = " ".join(words[:w])
                geo = self._geo_ont.lookup(phrase)
                if geo:
                    uri_m8, label, code = geo
                    entity = ExtractedEntity(
                        label=phrase,
                        category="Geography",
                        uri_m8=uri_m8,
                        confidence=0.98,
                        span_start=m.start(),
                        span_end=m.end(),
                    )
                    result.entities.append(entity)
                    if result.geography is None:
                        result.geography = entity
                    return

        # Recherche directe dictionnaire (bigrams, trigrams, puis tokens)
        tokens = text.split()
        for window in range(4, 0, -1):
            for i in range(len(tokens) - window + 1):
                candidate = " ".join(tokens[i:i + window])
                geo = self._geo_ont.lookup(candidate)
                if geo:
                    uri_m8, label, code = geo
                    entity = ExtractedEntity(
                        label=candidate,
                        category="Geography",
                        uri_m8=uri_m8,
                        confidence=0.95,
                        span_start=i,
                        span_end=i + window,
                    )
                    result.entities.append(entity)
                    if result.geography is None:
                        result.geography = entity
                    return

    # ──────────────────────────────────────────────────────────────
    # EXTRACTION POPULATION
    # ──────────────────────────────────────────────────────────────

    def _extract_population(self, text: str, result: NLPExtractionResult):
        """
        Stratégie :
        1. Pattern contextuel "chez les X", "dans la population des X"
        2. Recherche directe dictionnaire (bigrams avant tokens)
        """
        # Pattern contextuel
        for m in self.PATTERNS["population_trigger"].finditer(text):
            candidate = m.group(1).strip().lower()
            words = candidate.split()
            for w in range(len(words), 0, -1):
                phrase = " ".join(words[:w])
                pop = self._pop_ont.lookup(phrase)
                if pop:
                    uri_m8, uri_m2, label = pop
                    entity = ExtractedEntity(
                        label=phrase,
                        category="Population",
                        uri_m8=uri_m8,
                        uri_m2=uri_m2,
                        confidence=0.98,
                    )
                    result.entities.append(entity)
                    if result.population is None:
                        result.population = entity
                    return

        # Recherche directe
        tokens = text.split()
        for window in range(3, 0, -1):
            for i in range(len(tokens) - window + 1):
                candidate = " ".join(tokens[i:i + window])
                pop = self._pop_ont.lookup(candidate)
                if pop:
                    uri_m8, uri_m2, label = pop
                    entity = ExtractedEntity(
                        label=candidate,
                        category="Population",
                        uri_m8=uri_m8,
                        uri_m2=uri_m2,
                        confidence=0.90,
                    )
                    result.entities.append(entity)
                    if result.population is None:
                        result.population = entity
                    return

    def _infer_population_from_geography(self, result: NLPExtractionResult):
        """
        Inférence de population depuis le contexte géographique.
        Règle : si la géographie est en zone rurale africaine → population rurale.
        Règle : si la géographie est Paris/France → population générale urbaine.
        """
        RURAL_GEOS = {uri("m8", "Thies"), uri("m8", "Senegal"),
                      uri("m8", "AfriqueSubsaharienne")}
        URBAN_GEOS = {uri("m8", "Paris"), uri("m8", "Montreal")}
        GENERAL_GEOS = {uri("m8", "France")}

        geo_uri = result.geography.uri_m8
        if geo_uri in RURAL_GEOS:
            entity = ExtractedEntity(
                label=f"population rurale (inférée depuis {result.geography.label})",
                category="Population",
                uri_m8=uri("m8", "RuralPop_Thies"),
                uri_m2=uri("m2", "Pop_Rural_Africa"),
                confidence=0.70,  # confiance réduite = inférence
            )
            result.entities.append(entity)
            result.population = entity
            result.warnings.append(
                f"ℹ️  Population inférée depuis la géographie "
                f"'{result.geography.label}' → population rurale (confiance 0.70)."
            )
        elif geo_uri in URBAN_GEOS:
            entity = ExtractedEntity(
                label=f"population urbaine (inférée depuis {result.geography.label})",
                category="Population",
                uri_m8=uri("m8", "UrbanPopulationGroup"),
                uri_m2=uri("m2", "Pop_Urban"),
                confidence=0.65,
            )
            result.entities.append(entity)
            result.population = entity
            result.warnings.append(
                f"ℹ️  Population inférée depuis la géographie "
                f"'{result.geography.label}' → population urbaine (confiance 0.65)."
            )

    # ──────────────────────────────────────────────────────────────
    # EXTRACTION INTERVENTION
    # ──────────────────────────────────────────────────────────────

    def _extract_intervention(self, text: str, result: NLPExtractionResult):
        tokens = text.split()
        for window in range(3, 0, -1):
            for i in range(len(tokens) - window + 1):
                candidate = " ".join(tokens[i:i + window])
                intv = self._int_ont.lookup(candidate)
                if intv:
                    uri_m8, label = intv
                    entity = ExtractedEntity(
                        label=candidate,
                        category="Intervention",
                        uri_m8=uri_m8,
                        confidence=0.90,
                    )
                    result.entities.append(entity)
                    if result.intervention is None:
                        result.intervention = entity
                    return

    # ──────────────────────────────────────────────────────────────
    # EXTRACTION HORIZON TEMPOREL
    # ──────────────────────────────────────────────────────────────

    def _extract_timeframe(self, text: str, result: NLPExtractionResult):
        m = self.PATTERNS["timeframe"].search(text)
        if m:
            value = int(m.group(1))
            unit = m.group(2).rstrip("s")  # singulariser
            # Convertir en jours
            to_days = {"jour": 1, "semaine": 7, "moi": 30, "an": 365, "année": 365}
            days = value * to_days.get(unit, 1)
            entity = ExtractedEntity(
                label=m.group(0),
                category="TimeFrame",
                confidence=0.99,
            )
            entity.uri_m8 = f"timeframe:{days}days"  # pseudo-URI
            result.entities.append(entity)
            result.timeframe = entity

    # ──────────────────────────────────────────────────────────────
    # RÉSOLUTION DES CANDIDATS M2
    # ──────────────────────────────────────────────────────────────

    def _resolve_model_candidates(self, result: NLPExtractionResult):
        """
        Sélectionne et classe les modèles M2 candidats en fonction des entités
        extraites. Score composite = score_base × bonus_population × bonus_géo.
        """
        if result.disease is None:
            return

        candidates = CANDIDATE_MODELS.get(result.disease.uri_m8, [])
        if not candidates:
            result.warnings.append(
                f"Aucun modèle M2 trouvé pour la maladie : {result.disease.label}"
            )
            return

        scored = []
        for cand in candidates:
            score = cand["score_base"]

            # Bonus population : +0.10 si correspondance exacte
            if result.population:
                pop_uris = {result.population.uri_m8, result.population.uri_m2}
                if any(u in pop_uris for u in cand["population_match"]):
                    score = min(1.0, score + 0.10)

            # Bonus géographie : +0.05 si correspondance exacte
            if result.geography:
                if result.geography.uri_m8 in cand["geography_match"]:
                    score = min(1.0, score + 0.05)

            # Pénalité légère si ni population ni géo ne correspondent
            if (result.population and
                    not any(u in {result.population.uri_m8,
                                  result.population.uri_m2}
                            for u in cand["population_match"])
                    and cand["population_match"]):
                score = max(0.0, score - 0.05)

            scored.append({
                "model_id": cand["model_id"],
                "uri": cand["uri"],
                "label": cand["label"],
                "relevance_score": round(score, 3),
            })

        # Trier par score décroissant
        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        result.candidate_model_uris = scored

    # ──────────────────────────────────────────────────────────────
    # CONFIANCE GLOBALE
    # ──────────────────────────────────────────────────────────────

    def _compute_confidence(self, result: NLPExtractionResult):
        """
        Score global = moyenne pondérée des confidences des entités clés.
        Pénalité si entité critique manquante (maladie obligatoire).
        """
        if result.disease is None:
            result.global_confidence = 0.0
            return

        weights = {
            "disease": (result.disease, 0.40),
            "geography": (result.geography, 0.25),
            "population": (result.population, 0.25),
            "intervention": (result.intervention, 0.10),
        }

        score = 0.0
        total_weight = 0.0
        for _key, (entity, weight) in weights.items():
            if entity is not None:
                score += entity.confidence * weight
                total_weight += weight
            # si entité absente, on ne pénalise pas — elle est optionnelle

        result.global_confidence = round(
            score / total_weight if total_weight > 0 else 0.0, 3
        )

    # ──────────────────────────────────────────────────────────────
    # VÉRIFICATION AMBIGUÏTÉS
    # ──────────────────────────────────────────────────────────────

    def _check_warnings(self, result: NLPExtractionResult):
        if result.disease is None:
            result.warnings.append(
                "⚠️  Maladie non détectée. "
                "Vérifiez l'orthographe ou enrichissez le dictionnaire."
            )
        if result.geography is None:
            result.warnings.append(
                "ℹ️  Aucun territoire détecté. "
                "Les modèles globaux seront proposés par défaut."
            )
        if result.population is None:
            result.warnings.append(
                "ℹ️  Aucune population spécifique détectée. "
                "Population générale supposée."
            )
        if result.candidate_model_uris:
            top = result.candidate_model_uris[0]
            second = result.candidate_model_uris[1] if len(
                result.candidate_model_uris) > 1 else None
            if second and abs(top["relevance_score"] -
                               second["relevance_score"]) < 0.05:
                result.warnings.append(
                    f"⚠️  Ambiguïté : scores proches entre "
                    f"'{top['label']}' ({top['relevance_score']}) et "
                    f"'{second['label']}' ({second['relevance_score']}). "
                    "Une comparaison M5 est recommandée."
                )
