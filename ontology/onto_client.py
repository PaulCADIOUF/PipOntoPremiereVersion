"""
ontology/onto_client.py
========================
Client ontologique PIPOnto — sans dépendance externe (ElementTree intégré).

Charge les 9 modules RDF OWL et expose des méthodes de requête structurées.
Remplace rdflib pour ne pas ajouter de dépendance.

Modules chargés :
  M0  foundations_corrected.rdf     — Fondations (classes de base)
  M1  complex_system_corrected.rdf  — Systèmes Complexes
  M2  models.rdf                    — Ontologie des Modèles
  M4  simulation_corrected.rdf      — Simulation
  M5  experimentation.rdf           — Expérimentation
  M6  results.rdf                   — Résultats
  M7  interoperability.rdf          — Interopérabilité
  M8  epidemiological_domain.rdf    — Domaine Épidémiologique (TBox)
  M8b epidemiological_domain_abox   — Maladies individuelles (ABox)

Usage :
    from ontology.onto_client import OntologyClient

    onto = OntologyClient("/chemin/vers/rdf/")
    onto.load()

    # Obtenir info sur une maladie
    d = onto.get_disease("COVID-19")
    # → { uri, label_fr, recommended_formalism, r0_range, notes, ... }

    # Hiérarchie de classes d'un modèle
    h = onto.get_model_class_hierarchy("SEIRModel")
    # → ["Model", "CompartmentalModel", "SEIRModel"]

    # Propriétés attendues d'un formalisme
    p = onto.get_formalism_properties("SEIR")
    # → { compartments: [S,E,I,R], params: [β,γ,σ], ... }

    # URI canonique d'un modèle extrait
    uri = onto.get_model_uri("SEIR_COVID19_Ferguson_2020")
    # → "http://www.pacadi.org/these/piponto/module2#SEIR_COVID19_Ferguson_2020"
"""

import os
import re
import logging
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Optional

logger = logging.getLogger("piponto.ontology")

# ── Namespaces OWL/RDF ────────────────────────────────────────────────────────
NS = {
    "rdf":  "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl":  "http://www.w3.org/2002/07/owl#",
    "xsd":  "http://www.w3.org/2001/XMLSchema#",
    "pip":  "http://www.pacadi.org/these/piponto/",
}
BASE = "http://www.pacadi.org/these/piponto/"
M2   = BASE + "module2#"
M4   = BASE + "module4#"
M8   = BASE + "module8#"


# ══════════════════════════════════════════════════════════════════════════════
# DONNÉES ENCODÉES — extraites des RDF comments (knowledge compact)
# ══════════════════════════════════════════════════════════════════════════════

# Données structurées extraites de M8 (épidémiologique) + littérature
DISEASE_KB = {
    "COVID-19": {
        "uri":           M8 + "COVID19",
        "label_fr":      "COVID-19",
        "label_en":      "COVID-19",
        "icd10":         "U07.1",
        "pathogen":      M8 + "SARS_CoV_2",
        "best_formalism":["SEIR", "SEIRD", "SEIRS"],
        "r0_range":      [2.0, 6.0],
        "incubation_d":  5.1,
        "infectious_d":  7.0,
        "ifr":           0.01,
        "transmission":  "respiratory_droplets",
        "note": "Incubation ~5j → compartiment E requis. SEIRD recommandé pour modéliser la mortalité. SEIRS pour Omicron (réinfection).",
        "key_params": {"sigma": 0.196, "gamma": 0.143, "mu": 0.005},
    },
    "Seasonal Influenza": {
        "uri":           M8 + "SeasonalInfluenza",
        "label_fr":      "Grippe saisonnière",
        "label_en":      "Seasonal Influenza",
        "icd10":         "J11",
        "pathogen":      M8 + "InfluenzaVirus",
        "best_formalism":["SIR", "SEIRS", "SIS"],
        "r0_range":      [1.2, 2.5],
        "incubation_d":  2.0,
        "infectious_d":  4.0,
        "ifr":           0.001,
        "transmission":  "respiratory_droplets",
        "note": "Immunité saisonnière (~6 mois) → SEIRS. Incubation courte (1-4j) souvent intégrée dans β → SIR acceptable.",
        "key_params": {"gamma": 0.25, "omega": 0.005},
    },
    "Malaria": {
        "uri":           M8 + "Malaria",
        "label_fr":      "Paludisme",
        "label_en":      "Malaria",
        "icd10":         "B54",
        "pathogen":      M8 + "PlasmodiumFalciparum",
        "best_formalism":["SEIRS", "METAPOPULATION"],
        "r0_range":      [5.0, 100.0],
        "incubation_d":  12.0,
        "infectious_d":  30.0,
        "ifr":           0.004,
        "transmission":  "vector_borne",
        "note": "Endémie majeure Sénégal/Afrique subsaharienne. Immunité partielle → SEIRS. Modèle Ross-Macdonald ou SEIRS deux populations.",
        "key_params": {"sigma": 0.083, "gamma": 0.033},
    },
    "Ebola virus disease": {
        "uri":           M8 + "EbolaDiseaseInstance",
        "label_fr":      "Maladie à virus Ebola",
        "label_en":      "Ebola virus disease",
        "icd10":         "A98.4",
        "pathogen":      M8 + "EbolaVirus",
        "best_formalism":["SEIRD", "ABM"],
        "r0_range":      [1.4, 2.5],
        "incubation_d":  8.0,
        "infectious_d":  10.0,
        "ifr":           0.60,
        "transmission":  "direct_contact",
        "note": "CFR très élevé (~50-90%) → compartiment D requis. ABM pertinent pour les chaînes de transmission familiales.",
        "key_params": {"sigma": 0.125, "gamma": 0.1, "mu": 0.05},
    },
    "Dengue fever": {
        "uri":           M8 + "Dengue",
        "label_fr":      "Dengue",
        "label_en":      "Dengue fever",
        "icd10":         "A90",
        "best_formalism":["SEIRS", "METAPOPULATION"],
        "r0_range":      [2.0, 6.0],
        "incubation_d":  6.0,
        "infectious_d":  7.0,
        "ifr":           0.001,
        "transmission":  "vector_borne",
        "note": "Vecteur Aedes aegypti. 4 sérotypes → immunité croisée partielle → SEIRS. Pertinent zones tropicales.",
        "key_params": {"sigma": 0.167, "gamma": 0.143},
    },
    "Measles": {
        "uri":           M8 + "Measles",
        "label_fr":      "Rougeole",
        "label_en":      "Measles",
        "icd10":         "B05",
        "best_formalism":["SIR", "SEIR"],
        "r0_range":      [12.0, 18.0],
        "incubation_d":  10.0,
        "infectious_d":  8.0,
        "ifr":           0.002,
        "transmission":  "airborne",
        "note": "Immunité à vie → compartiment R permanent → SIR. R0 = 12-18 (le plus élevé des maladies communes).",
        "key_params": {"sigma": 0.1, "gamma": 0.125},
    },
    "HIV/AIDS": {
        "uri":           M8 + "HIVAIDS",
        "label_fr":      "VIH/SIDA",
        "label_en":      "HIV/AIDS",
        "icd10":         "B24",
        "best_formalism":["SIS", "ABM", "NETWORK"],
        "r0_range":      [2.0, 5.0],
        "incubation_d":  3650.0,
        "infectious_d":  3650.0,
        "ifr":           0.90,
        "transmission":  "sexual_contact_blood",
        "note": "Infection chronique → SIS ou ABM réseau. Les comportements individuels sont déterminants → ABM/NETWORK.",
        "key_params": {},
    },
    "Tuberculosis": {
        "uri":           M8 + "Tuberculosis",
        "label_fr":      "Tuberculose",
        "label_en":      "Tuberculosis",
        "icd10":         "A15",
        "best_formalism":["SEIR", "SEIRS"],
        "r0_range":      [1.0, 4.0],
        "incubation_d":  730.0,
        "infectious_d":  180.0,
        "ifr":           0.15,
        "transmission":  "airborne",
        "note": "Latence longue (mois-années) → compartiment E prolongé. Co-infection VIH importante.",
        "key_params": {"sigma": 0.0014},
    },
    "Cholera": {
        "uri":           M8 + "Cholera",
        "label_fr":      "Choléra",
        "label_en":      "Cholera",
        "icd10":         "A00",
        "best_formalism":["SIR", "SEIR", "METAPOPULATION"],
        "r0_range":      [1.5, 4.0],
        "incubation_d":  2.0,
        "infectious_d":  5.0,
        "ifr":           0.25,
        "transmission":  "waterborne",
        "note": "Transmission hydrique → modèle SIWR (Water compartment). Métapopulation pour les épidémies régionales.",
        "key_params": {"sigma": 0.5, "gamma": 0.2},
    },
    "Mpox": {
        "uri":           M8 + "Mpox",
        "label_fr":      "Mpox",
        "label_en":      "Mpox",
        "icd10":         "B04",
        "best_formalism":["SEIRD", "ABM", "NETWORK"],
        "r0_range":      [1.0, 2.5],
        "incubation_d":  10.0,
        "infectious_d":  20.0,
        "ifr":           0.04,
        "transmission":  "direct_contact",
        "note": "Transmission par contact direct → réseau de contacts. ABM pour les clusters.",
        "key_params": {"sigma": 0.1, "gamma": 0.05},
    },
}

# Hiérarchie de classes M2 (extraite du RDF)
MODEL_CLASSES = {
    "Model":                    {"parent": None,                     "uri": M2+"Model"},
    "CompartmentalModel":       {"parent": "Model",                  "uri": M2+"CompartmentalModel"},
    "SIRModel":                 {"parent": "CompartmentalModel",     "uri": M2+"SIRModel",
                                 "compartments": ["S","I","R"], "formalism": "SIR"},
    "SEIRModel":                {"parent": "CompartmentalModel",     "uri": M2+"SEIRModel",
                                 "compartments": ["S","E","I","R"], "formalism": "SEIR"},
    "SEIRSModel":               {"parent": "SEIRModel",              "uri": M2+"SEIRSModel",
                                 "compartments": ["S","E","I","R"], "formalism": "SEIRS"},
    "SEIRDModel":               {"parent": "SEIRModel",              "uri": M2+"SEIRDModel",
                                 "compartments": ["S","E","I","R","D"], "formalism": "SEIRD"},
    "SIRSModel":                {"parent": "SIRModel",               "uri": M2+"SIRSModel",
                                 "compartments": ["S","I","R"], "formalism": "SIRS"},
    "SISModel":                 {"parent": "CompartmentalModel",     "uri": M2+"SISModel",
                                 "compartments": ["S","I"], "formalism": "SIS"},
    "StochasticModel":          {"parent": "Model",                  "uri": M2+"StochasticModel"},
    "StochasticCompartmental":  {"parent": "StochasticModel",        "uri": M2+"StochasticCompartmentalModel"},
    "ABMModel":                 {"parent": "Model",                  "uri": M2+"ABMModel",
                                 "formalism": "ABM"},
    "NetworkModel":             {"parent": "Model",                  "uri": M2+"NetworkModel",
                                 "formalism": "NETWORK"},
    "MetapopulationModel":      {"parent": "Model",                  "uri": M2+"MetapopulationModel",
                                 "formalism": "METAPOPULATION"},
}

# Propriétés M2
FORMALISM_PROPS = {
    "SIR":   {"compartments": ["S","I","R"],           "required_params": ["beta","gamma"],
              "owl_class": M2+"SIRModel",
              "equations": ["dS/dt = -β·S·I/N", "dI/dt = β·S·I/N - γ·I", "dR/dt = γ·I"]},
    "SEIR":  {"compartments": ["S","E","I","R"],        "required_params": ["beta","gamma","sigma"],
              "owl_class": M2+"SEIRModel",
              "equations": ["dS/dt = -β·S·I/N", "dE/dt = β·S·I/N - σ·E",
                            "dI/dt = σ·E - γ·I", "dR/dt = γ·I"]},
    "SEIRS": {"compartments": ["S","E","I","R"],        "required_params": ["beta","gamma","sigma","omega"],
              "owl_class": M2+"SEIRSModel",
              "equations": ["... + ω·R → S (réinfection)"]},
    "SEIRD": {"compartments": ["S","E","I","R","D"],    "required_params": ["beta","gamma","sigma","mu"],
              "owl_class": M2+"SEIRDModel",
              "equations": ["dD/dt = μ·I"]},
    "SIS":   {"compartments": ["S","I"],                "required_params": ["beta","gamma"],
              "owl_class": M2+"SISModel",
              "equations": ["dS/dt = -β·S·I/N + γ·I", "dI/dt = β·S·I/N - γ·I"]},
    "ABM":   {"compartments": [],                       "required_params": [],
              "owl_class": M2+"ABMModel",
              "note": "Simulation individu par individu — paramètres définis localement"},
    "NETWORK": {"compartments": [],                     "required_params": [],
                "owl_class": M2+"NetworkModel",
                "note": "Transmission sur graphe de contacts"},
    "METAPOPULATION": {"compartments": [],              "required_params": [],
                       "owl_class": M2+"MetapopulationModel",
                       "note": "Patches géographiques couplés"},
}

# Propriétés M4 (simulation)
SIMULATION_PROPS = {
    "Simulation": M4 + "Simulation",
    "instanciates": M4 + "instanciates",
    "instanciatesModel": M4 + "instanciatesModel",
    "hasConfiguration": M4 + "hasConfiguration",
    "hasRun": M4 + "hasRun",
    "setsParameterValue": M4 + "setsParameterValue",
    "hasInitialCondition": M4 + "hasInitialCondition",
}


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT ONTOLOGIQUE
# ══════════════════════════════════════════════════════════════════════════════

class OntologyClient:
    """
    Client d'accès à l'ontologie PIPOnto.

    Charge les modules RDF et expose des méthodes d'interrogation.
    Fonctionne avec xml.etree.ElementTree (aucune dépendance externe).
    """

    # Modules RDF à charger
    MODULES = {
        "M0": "foundations_corrected.rdf",
        "M1": "complex_system_corrected.rdf",
        "M2": "models.rdf",
        "M4": "simulation_corrected.rdf",
        "M5": "experimentation.rdf",
        "M6": "results.rdf",
        "M7": "interoperability.rdf",
        "M8": "epidemiological_domain.rdf",
        "M8b":"epidemiological_domain_abox.rdf",
    }

    def __init__(self, rdf_dir: str = None):
        if rdf_dir is None:
            # Chercher dans les emplacements habituels
            candidates = [
                Path.home() / "piponto" / "ontology",
                Path.home() / "piponto" / "rdf",
                Path("/mnt/user-data/outputs"),
            ]
            for c in candidates:
                if c.exists():
                    rdf_dir = str(c)
                    break
            else:
                rdf_dir = str(Path.home() / "piponto" / "ontology")

        self.rdf_dir   = Path(rdf_dir)
        self._loaded   = False
        self._modules  = {}     # nom → ET root
        self._classes  = {}     # uri → {label, parent, comment}
        self._individuals = {}  # uri → {label, type, comment}
        self._properties  = {}  # uri → {domain, range, label}

        # Utilise la base de connaissance intégrée (toujours disponible)
        self._disease_kb = DISEASE_KB
        self._model_cls  = MODEL_CLASSES
        self._form_props = FORMALISM_PROPS

    def load(self):
        """Charge tous les modules RDF disponibles."""
        loaded_count = 0
        for module_id, filename in self.MODULES.items():
            path = self.rdf_dir / filename
            if path.exists():
                try:
                    tree = ET.parse(str(path))
                    self._modules[module_id] = tree.getroot()
                    self._index_module(module_id, tree.getroot())
                    loaded_count += 1
                    logger.debug(f"  ✅ {module_id} chargé ({filename})")
                except Exception as e:
                    logger.warning(f"  ⚠️  {module_id} — erreur parse : {e}")
            else:
                logger.debug(f"  — {module_id} non trouvé ({path})")

        self._loaded = True
        logger.info(f"Ontologie PIPOnto : {loaded_count}/{len(self.MODULES)} modules chargés")
        return self

    def _index_module(self, module_id: str, root: ET.Element):
        """Indexe les classes, individus et propriétés d'un module."""
        def tag(ns, local): return f"{{{NS[ns]}}}{local}"

        for elem in root.iter():
            about = elem.get(f"{{{NS['rdf']}}}about", "")
            if not about:
                continue

            label_en = label_fr = comment = ""
            for child in elem:
                local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if local == "label":
                    lang = child.get(f"{{{NS['xml']}}}lang", "") if "xml" in str(child.attrib) else ""
                    if "fr" in lang or not label_fr:
                        label_fr = child.text or ""
                    if "en" in lang or not label_en:
                        label_en = child.text or ""
                elif local == "comment":
                    comment = (child.text or "").strip()

            # Classes OWL
            if elem.tag == f"{{{NS['owl']}}}Class":
                parent = None
                for sub in elem.findall(f"{{{NS['rdfs']}}}subClassOf"):
                    parent = sub.get(f"{{{NS['rdf']}}}resource")
                self._classes[about] = {
                    "uri": about, "label_fr": label_fr, "label_en": label_en,
                    "comment": comment, "parent": parent, "module": module_id,
                }
            # Individus OWL
            elif elem.tag == f"{{{NS['owl']}}}NamedIndividual":
                rdf_type = None
                for t in elem.findall(f"{{{NS['rdf']}}}type"):
                    rdf_type = t.get(f"{{{NS['rdf']}}}resource")
                self._individuals[about] = {
                    "uri": about, "label_fr": label_fr, "label_en": label_en,
                    "comment": comment, "type": rdf_type, "module": module_id,
                }

    # ── API publique ────────────────────────────────────────────────────────

    def get_disease(self, name: str) -> Optional[dict]:
        """
        Retourne les données ontologiques d'une maladie.

        Cherche par nom anglais, français, ou synonyme.
        Retourne toujours quelque chose si la maladie est connue.
        """
        name_lower = name.lower().strip()

        # Recherche directe dans la KB
        for disease_name, data in self._disease_kb.items():
            if (name_lower in disease_name.lower() or
                disease_name.lower() in name_lower or
                name_lower in data.get("label_fr", "").lower() or
                name_lower in [s.lower() for s in [
                    "covid", "coronavirus", "grippe", "flu", "malaria",
                    "paludisme", "ebola", "dengue", "rougeole", "measles",
                    "tuberculose", "tb", "vih", "hiv", "sida", "aids",
                    "choléra", "cholera", "mpox", "monkeypox",
                ]]):
                # Enrichir avec les données de l'ontologie chargée si disponible
                onto_data = self._get_individual_from_rdf(data["uri"])
                return {**data, **(onto_data or {})}

        return None

    def _get_individual_from_rdf(self, uri: str) -> Optional[dict]:
        """Récupère les données d'un individu dans les modules RDF chargés."""
        ind = self._individuals.get(uri)
        if ind:
            return {"rdf_comment": ind.get("comment"), "rdf_type": ind.get("type")}
        return None

    def get_disease_by_search(self, query: str) -> Optional[dict]:
        """Recherche floue d'une maladie dans la base de connaissance."""
        q = query.lower()

        # Mapping synonymes → nom canonique
        SYNONYMS = {
            "covid": "COVID-19", "covid19": "COVID-19", "coronavirus": "COVID-19",
            "sars-cov-2": "COVID-19", "corona": "COVID-19",
            "grippe": "Seasonal Influenza", "flu": "Seasonal Influenza",
            "influenza": "Seasonal Influenza",
            "malaria": "Malaria", "paludisme": "Malaria", "plasmodium": "Malaria",
            "ebola": "Ebola virus disease", "evd": "Ebola virus disease",
            "dengue": "Dengue fever", "denv": "Dengue fever",
            "rougeole": "Measles", "measles": "Measles",
            "tuberculose": "Tuberculosis", "tb": "Tuberculosis",
            "vih": "HIV/AIDS", "hiv": "HIV/AIDS", "sida": "HIV/AIDS", "aids": "HIV/AIDS",
            "choléra": "Cholera", "cholera": "Cholera",
            "mpox": "Mpox", "monkeypox": "Mpox",
        }

        # Chercher le synonyme le plus long qui correspond
        best_match = None
        best_len   = 0
        for syn, canonical in SYNONYMS.items():
            if syn in q and len(syn) > best_len:
                best_match = canonical
                best_len   = len(syn)

        if best_match:
            return self._disease_kb.get(best_match)

        return None

    def get_recommended_formalism(self, disease_name: str) -> list[str]:
        """
        Retourne les formalismes recommandés pour une maladie.
        Basé sur les annotations M8 de l'ontologie.
        """
        d = self.get_disease(disease_name)
        if d:
            return d.get("best_formalism", ["SEIR"])
        return ["SEIR"]

    def get_formalism_class(self, formalism: str) -> dict:
        """
        Retourne les données ontologiques d'un formalisme de modélisation.
        Inclut URI M2, compartiments, paramètres requis, équations.
        """
        return self._form_props.get(formalism.upper(), {
            "compartments": [],
            "required_params": [],
            "owl_class": M2 + formalism + "Model",
            "note": "Formalisme non standard"
        })

    def get_model_class_hierarchy(self, formalism: str) -> list[str]:
        """
        Retourne la hiérarchie de classes OWL pour un formalisme.
        Ex: "SEIR" → ["Model", "CompartmentalModel", "SEIRModel"]
        """
        # Mapping formalisme → nom de classe M2
        fmap = {
            "SIR":   "SIRModel",
            "SEIR":  "SEIRModel",
            "SEIRS": "SEIRSModel",
            "SEIRD": "SEIRDModel",
            "SIRS":  "SIRSModel",
            "SIS":   "SISModel",
            "ABM":   "ABMModel",
            "NETWORK": "NetworkModel",
            "METAPOPULATION": "MetapopulationModel",
        }
        class_name = fmap.get(formalism.upper(), "Model")

        # Construire la hiérarchie en remontant les parents
        hierarchy = []
        current = class_name
        visited  = set()
        while current and current not in visited:
            hierarchy.insert(0, current)
            visited.add(current)
            info = self._model_cls.get(current, {})
            current = info.get("parent")

        return hierarchy

    def get_model_uri(self, model_id: str) -> str:
        """Génère l'URI ontologique M2 d'un modèle extrait."""
        return M2 + model_id

    def get_simulation_uri(self, model_id: str, run_id: str = None) -> str:
        """Génère l'URI M4 d'une simulation."""
        suffix = f"Sim_{model_id}" + (f"_{run_id}" if run_id else "")
        return M4 + suffix

    def get_disease_uri(self, disease_name: str) -> Optional[str]:
        """Retourne l'URI M8 d'une maladie."""
        d = self.get_disease(disease_name)
        return d.get("uri") if d else None

    def get_all_diseases(self) -> list[dict]:
        """Retourne toutes les maladies de la base de connaissance."""
        return [
            {"name_en": name, **data}
            for name, data in self._disease_kb.items()
        ]

    def validate_model_formalism(self, model_id: str, formalism: str,
                                  disease_name: str) -> dict:
        """
        Valide qu'un formalisme est cohérent avec la maladie.
        Retourne un rapport de validation ontologique.
        """
        recommended = self.get_recommended_formalism(disease_name)
        is_valid    = formalism.upper() in recommended

        report = {
            "model_uri":        self.get_model_uri(model_id),
            "formalism":        formalism,
            "disease_uri":      self.get_disease_uri(disease_name),
            "recommended":      recommended,
            "ontologically_consistent": is_valid,
            "model_class":      self.get_formalism_class(formalism).get("owl_class"),
            "hierarchy":        self.get_model_class_hierarchy(formalism),
        }

        if not is_valid:
            report["warning"] = (
                f"L'ontologie M8 recommande {recommended} pour {disease_name}. "
                f"{formalism} est utilisable mais peut manquer de précision."
            )
        return report

    def enrich_intent(self, intent_dict: dict) -> dict:
        """
        Enrichit un intent NLP avec les données ontologiques.
        Ajoute : URI canonique, formalisme recommandé, paramètres typiques.

        Entrée  : { disease_name, country_code, N, days, formalism, ... }
        Sortie  : entrée + { onto_disease, recommended_formalism, default_params, ... }
        """
        enriched = dict(intent_dict)

        disease_name = intent_dict.get("disease_name")
        if disease_name:
            d = self.get_disease_by_search(disease_name) or self.get_disease(disease_name)
            if d:
                enriched["onto_disease"]            = d
                enriched["disease_uri"]             = d.get("uri")
                enriched["disease_uri_short"]       = d.get("uri","").split("#")[-1]
                enriched["recommended_formalism"]   = d.get("best_formalism", ["SEIR"])
                enriched["r0_range"]                = d.get("r0_range")
                enriched["transmission_route"]      = d.get("transmission")
                enriched["disease_note"]            = d.get("note")
                enriched["incubation_days"]         = d.get("incubation_d")

                # Si pas de formalisme saisi, suggérer le premier recommandé
                if not intent_dict.get("formalism"):
                    enriched["suggested_formalism"] = d.get("best_formalism", ["SEIR"])[0]

                # Paramètres typiques de la maladie
                if d.get("key_params"):
                    enriched["typical_params"] = d["key_params"]

        # URI ontologique du modèle si model_id connu
        if intent_dict.get("model_id"):
            enriched["model_uri"] = self.get_model_uri(intent_dict["model_id"])

        return enriched

    def get_stats(self) -> dict:
        """Statistiques sur l'ontologie chargée."""
        return {
            "modules_loaded":    len(self._modules),
            "modules_available": list(self._modules.keys()),
            "classes_indexed":   len(self._classes),
            "individuals_indexed":len(self._individuals),
            "diseases_in_kb":    len(self._disease_kb),
            "formalisms_in_kb":  len(self._form_props),
            "rdf_dir":           str(self.rdf_dir),
        }


# ── Singleton partagé ─────────────────────────────────────────────────────────
_instance: Optional[OntologyClient] = None

def get_ontology(rdf_dir: str = None) -> OntologyClient:
    """Retourne l'instance singleton du client ontologique."""
    global _instance
    if _instance is None:
        _instance = OntologyClient(rdf_dir)
        _instance.load()
    return _instance


# ── Test standalone ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    onto = get_ontology("/mnt/user-data/outputs")

    print("\n" + "═"*60)
    print("  PIPOnto Ontology Client — Tests")
    print("═"*60)
    print(f"\n  Stats : {onto.get_stats()}")

    tests = [
        ("COVID-19",       "SEIR",  "COVID-19"),
        ("Malaria",        "SEIRS", "Malaria"),
        ("Ebola",          "ABM",   "Ebola virus disease"),
        ("Seasonal Influenza", "SIR", "Seasonal Influenza"),
    ]
    for disease, formalism, full_name in tests:
        print(f"\n  ── {disease} ──")
        d = onto.get_disease_by_search(disease)
        if d:
            print(f"  URI      : {d['uri']}")
            print(f"  Formalismes recommandés : {d['best_formalism']}")
            print(f"  R₀ range : {d.get('r0_range')}")
            print(f"  Note     : {d.get('note','')[:80]}...")

        v = onto.validate_model_formalism(f"{formalism}_{disease}_Test_2024",
                                          formalism, full_name)
        status = "✅" if v["ontologically_consistent"] else "⚠️ "
        print(f"  {status} Cohérence {formalism}/{full_name} : {v['ontologically_consistent']}")
        print(f"  Hiérarchie : {' → '.join(v['hierarchy'])}")

    print(f"\n  Formalism SEIR :")
    fp = onto.get_formalism_class("SEIR")
    print(f"  Compartiments : {fp['compartments']}")
    print(f"  Params requis : {fp['required_params']}")
    print(f"  Équations     : {fp.get('equations',[])[:2]}")
    print(f"  URI OWL       : {fp.get('owl_class')}")
