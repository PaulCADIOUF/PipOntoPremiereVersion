"""
api/schemas.py
===============
Schémas Pydantic pour la validation des données de l'API PIPOnto.
"""

from typing import Optional, List, Any
from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# SYSTÈME
# ══════════════════════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str
    version: str
    db_connected: bool
    validated_models: int
    total_models: int
    diseases_covered: int
    timestamp: str

    class Config:
        json_schema_extra = {
            "example": {
                "status": "ok",
                "version": "1.0.0",
                "db_connected": True,
                "validated_models": 229,
                "total_models": 249,
                "diseases_covered": 12,
                "timestamp": "2026-03-11T02:00:00Z"
            }
        }


class StatsResponse(BaseModel):
    validated_models: int
    pending_models: int
    models_with_code: int
    empirically_validated: int
    avg_confidence: float
    by_formalism: dict[str, int]
    by_disease: List[dict]
    by_decade: List[dict]
    top_countries: List[dict]


# ══════════════════════════════════════════════════════════════════════════════
# MALADIES
# ══════════════════════════════════════════════════════════════════════════════

class DiseaseOut(BaseModel):
    disease_id: int
    name_fr: str
    name_en: str
    icd10_code: Optional[str]
    pathogen_type: Optional[str]
    pathogen_name: Optional[str]
    transmission_route: Optional[str]
    is_zoonotic: bool = False
    has_vector: bool = False
    vaccine_available: bool = False
    who_priority: Optional[bool]
    endemic_regions: List[str] = []
    uri_m8: Optional[str]
    model_count: int = 0

    class Config:
        json_schema_extra = {
            "example": {
                "disease_id": 1,
                "name_fr": "COVID-19",
                "name_en": "COVID-19",
                "icd10_code": "U07.1",
                "pathogen_type": "Virus",
                "pathogen_name": "SARS-CoV-2",
                "transmission_route": "AIRBORNE",
                "is_zoonotic": False,
                "has_vector": False,
                "vaccine_available": True,
                "who_priority": True,
                "endemic_regions": ["Global"],
                "uri_m8": "http://www.pacadi.org/these/piponto/module8#COVID19",
                "model_count": 45
            }
        }


class DiseaseListOut(BaseModel):
    diseases: List[DiseaseOut]
    total: int


# ══════════════════════════════════════════════════════════════════════════════
# MODÈLES — RÉSUMÉ (POUR LA LISTE/RECHERCHE)
# ══════════════════════════════════════════════════════════════════════════════

class ModelSummary(BaseModel):
    model_id: str
    name: str
    formalism: str
    model_type: str
    spatial_structure: Optional[str]
    is_age_structured: bool = False
    disease_name: str
    disease_name_fr: Optional[str]
    has_code: bool = False
    implementation_url: Optional[str]
    platform: Optional[str]
    countries: List[str] = []
    param_count: int = 0
    extraction_confidence: float
    is_empirically_validated: bool = False
    primary_population: Optional[str]
    doi: Optional[str]
    year: Optional[int]
    uri_m2: Optional[str]

    class Config:
        json_schema_extra = {
            "example": {
                "model_id": "SEIR_COVID19_Ferguson_2020",
                "name": "SEIR Ferguson 2020 — COVID-19 — Royaume-Uni",
                "formalism": "SEIR",
                "model_type": "DETERMINISTIC",
                "disease_name": "COVID-19",
                "has_code": True,
                "countries": ["GB"],
                "param_count": 4,
                "extraction_confidence": 0.85,
                "uri_m2": "http://www.pacadi.org/these/piponto/module2#SEIR_COVID19_Ferguson_2020"
            }
        }


class ModelSearchOut(BaseModel):
    models: List[ModelSummary]
    total: int
    limit: int
    offset: int
    query: dict


# ══════════════════════════════════════════════════════════════════════════════
# PARAMÈTRES
# ══════════════════════════════════════════════════════════════════════════════

class ParameterOut(BaseModel):
    param_id: Optional[int] = None
    param_type: str
    symbol: str
    name_fr: Optional[str]
    name_en: Optional[str]
    default_value: float
    min_value: Optional[float]
    max_value: Optional[float]
    confidence_interval_low: Optional[float]
    confidence_interval_high: Optional[float]
    unit: Optional[str]
    time_unit: Optional[str]
    is_estimated: bool = True
    estimation_method: Optional[str]
    notes: Optional[str]

    class Config:
        json_schema_extra = {
            "example": {
                "param_type": "TRANSMISSION_RATE",
                "symbol": "β",
                "default_value": 0.31,
                "min_value": 0.25,
                "max_value": 0.38,
                "unit": "day^-1",
                "time_unit": "day",
                "is_estimated": True,
                "estimation_method": "MLE"
            }
        }


# ══════════════════════════════════════════════════════════════════════════════
# MODÈLE — DÉTAIL COMPLET
# ══════════════════════════════════════════════════════════════════════════════

class ModelDetail(BaseModel):
    # Identité
    model_id: str
    name: str
    description: Optional[str]
    formalism: str
    model_type: str
    spatial_structure: Optional[str]
    is_age_structured: bool
    is_multi_strain: bool
    has_interventions: bool
    # Implémentation
    platform: Optional[str]
    has_code: bool
    implementation_url: Optional[str]
    code_license: Optional[str]
    # Population
    primary_population: Optional[str]
    is_empirically_validated: bool
    extraction_confidence: float
    # Ontologie
    uri_m2: Optional[str]
    # Maladie
    disease_name_fr: str
    disease_name_en: str
    pathogen_name: Optional[str]
    transmission_route: Optional[str]
    disease_uri_m8: Optional[str]
    # Référence
    ref_title: Optional[str]
    ref_authors: Optional[str]
    ref_journal: Optional[str]
    ref_year: Optional[int]
    ref_doi: Optional[str]
    ref_pmid: Optional[str]
    ref_open_access: Optional[bool]
    # Données liées
    parameters: List[ParameterOut]
    compartments: List[dict]
    geographic_scopes: List[dict]


# ══════════════════════════════════════════════════════════════════════════════
# SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

class SimulateRequest(BaseModel):
    # Source des paramètres
    model_id: Optional[str] = Field(
        None,
        description="ID d'un modèle de la bibliothèque (récupère β, γ, σ automatiquement)"
    )
    formalism: Optional[str] = Field(
        "SEIR",
        description="SIR, SEIR, SEIRS, SEIRD (ignoré si model_id fourni)"
    )

    # Population
    N: int = Field(
        1_000_000,
        description="Taille de la population", ge=100, le=10_000_000_000
    )
    I0: int = Field(10, description="Nombre initial d'infectieux", ge=1)
    R0_init: int = Field(0, description="Nombre initial de guéris", ge=0)

    # Paramètres épidémiologiques (optionnels si model_id fourni)
    beta:  Optional[float] = Field(None, description="Taux de transmission (day⁻¹)", ge=0, le=10)
    gamma: Optional[float] = Field(None, description="Taux de guérison (day⁻¹)", ge=0, le=1)
    sigma: Optional[float] = Field(None, description="Taux d'incubation (day⁻¹)", ge=0, le=1)
    mu:    Optional[float] = Field(None, description="Taux de mortalité (day⁻¹)", ge=0, le=1)
    omega: Optional[float] = Field(None, description="Taux de perte d'immunité SEIRS (day⁻¹)", ge=0, le=1)

    # Paramètres temporels
    days: int = Field(365, description="Durée de la simulation en jours", ge=1, le=3650)
    dt:   float = Field(1.0, description="Pas de temps en jours", ge=0.01, le=1.0)

    class Config:
        json_schema_extra = {
            "example": {
                "model_id": "SEIR_COVID19_Ferguson_2020",
                "N": 68000000,
                "I0": 100,
                "days": 365
            }
        }


class SimulateResponse(BaseModel):
    model_id: Optional[str]
    formalism: str
    parameters_used: dict
    source_params: dict
    # Résultats
    peak_infected: int
    peak_day: int
    total_infected: int
    attack_rate: float          # % de la population infectée
    epidemic_duration_days: int
    R0_effective: float
    # Séries temporelles (une valeur par jour)
    time_series: dict           # {"t": [...], "S": [...], "E": [...], "I": [...], "R": [...]}
    summary: str                # Phrase de résumé lisible

    class Config:
        json_schema_extra = {
            "example": {
                "formalism": "SEIR",
                "peak_infected": 142000,
                "peak_day": 87,
                "total_infected": 680000,
                "attack_rate": 0.68,
                "epidemic_duration_days": 210,
                "R0_effective": 2.5,
                "summary": "Pic épidémique au jour 87 avec 142 000 infectieux simultanés. 680 000 cas au total (68% de la population)."
            }
        }
