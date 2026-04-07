"""
api/main.py
============
API REST PIPOnto — FastAPI

7 endpoints :
    GET  /                          → santé + stats globales
    GET  /diseases                  → liste des maladies
    GET  /models/search             → recherche multicritère
    GET  /models/{model_id}         → fiche complète d'un modèle
    GET  /models/{model_id}/params  → paramètres épidémiologiques
    POST /simulate                  → lancer une simulation SEIR/SIR
    GET  /stats                     → statistiques de la bibliothèque

Usage :
    cd ~/piponto
    source venv/bin/activate
    pip install fastapi uvicorn psycopg2-binary python-dotenv scipy

    # Développement (rechargement auto)
    uvicorn api.main:app --reload --port 8000

    # Production
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

Documentation interactive :
    http://localhost:8000/docs      ← Swagger UI
    http://localhost:8000/redoc     ← ReDoc
"""

import os
import math
import logging
from typing import Optional, List
from pathlib import Path
from datetime import datetime

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ── Modèles Pydantic ──────────────────────────────────────────────────────────
from api.schemas import (
    HealthResponse, StatsResponse,
    DiseaseOut, DiseaseListOut,
    ModelSummary, ModelSearchOut,
    ModelDetail,
    SimulateRequest, SimulateResponse,
    ParameterOut,
)
from api.simulator import run_simulation

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv(Path.home() / "piponto" / ".env")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}

API_VERSION = "1.0.0"
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("piponto.api")

# ══════════════════════════════════════════════════════════════════════════════
# APPLICATION FASTAPI
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="PIPOnto API",
    description="""
## Bibliothèque de Modèles Épidémiologiques — API REST

API REST pour accéder à la bibliothèque PIPOnto de modèles épidémiologiques
validés, leurs paramètres calibrés, et lancer des simulations SEIR/SIR.

### Endpoints principaux
- **`GET /models/search`** — rechercher des modèles par maladie, pays, type
- **`GET /models/{id}`** — fiche complète d'un modèle avec ses paramètres
- **`POST /simulate`** — lancer une simulation épidémique avec les paramètres du modèle
- **`GET /diseases`** — liste des maladies avec leurs caractéristiques

### Namespace ontologique
`http://www.pacadi.org/these/piponto/`
""",
    version=API_VERSION,
    contact={"name": "PIPOnto Research", "url": "http://www.pacadi.org/these/piponto/"},
    license_info={"name": "CC-BY 4.0"},
)

# CORS — autoriser les appels depuis le frontend local (validation_app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000", "http://localhost:3000", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

def get_db():
    """Connexion PostgreSQL avec curseur dict."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def db_fetchall(query: str, params=None) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            return [dict(r) for r in cur.fetchall()]


def db_fetchone(query: str, params=None) -> Optional[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            row = cur.fetchone()
            return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 1 : SANTÉ / RACINE
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/",
    response_model=HealthResponse,
    summary="Santé de l'API",
    tags=["Système"],
)
def health():
    """
    Vérifie que l'API est opérationnelle et retourne les statistiques globales.
    """
    try:
        stats = db_fetchone("""
            SELECT
                COUNT(*) FILTER (WHERE validation_status='VALIDATED') AS validated_models,
                COUNT(*) AS total_models,
                COUNT(DISTINCT disease_id) AS diseases_covered
            FROM piponto.models
        """)
        db_ok = True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        stats = {"validated_models": 0, "total_models": 0, "diseases_covered": 0}
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=API_VERSION,
        db_connected=db_ok,
        validated_models=stats["validated_models"],
        total_models=stats["total_models"],
        diseases_covered=stats["diseases_covered"],
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 2 : STATISTIQUES
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/stats",
    response_model=StatsResponse,
    summary="Statistiques de la bibliothèque",
    tags=["Système"],
)
def get_stats():
    """
    Statistiques détaillées de la bibliothèque PIPOnto :
    distribution par formalisme, maladie, période, présence de code.
    """
    # Stats globales
    global_stats = db_fetchone("""
        SELECT
            COUNT(*) FILTER (WHERE validation_status='VALIDATED') AS validated,
            COUNT(*) FILTER (WHERE validation_status='PENDING')   AS pending,
            COUNT(*) FILTER (WHERE has_code=TRUE)                 AS with_code,
            COUNT(*) FILTER (WHERE is_empirically_validated=TRUE) AS empirical,
            ROUND(AVG(extraction_confidence)::numeric, 3)         AS avg_confidence
        FROM piponto.models
        WHERE validation_status = 'VALIDATED'
    """)

    # Par formalisme
    by_formalism = db_fetchall("""
        SELECT formalism, COUNT(*) AS count
        FROM piponto.models WHERE validation_status='VALIDATED'
        GROUP BY formalism ORDER BY count DESC
    """)

    # Par maladie (top 10)
    by_disease = db_fetchall("""
        SELECT d.name_en AS disease, COUNT(*) AS count
        FROM piponto.models m
        JOIN piponto.diseases d ON d.disease_id = m.disease_id
        WHERE m.validation_status='VALIDATED'
        GROUP BY d.name_en ORDER BY count DESC LIMIT 10
    """)

    # Par décennie
    by_decade = db_fetchall("""
        SELECT
            CONCAT(FLOOR(r.year/10)*10, 's') AS decade,
            COUNT(*) AS count
        FROM piponto.models m
        JOIN piponto.model_references r ON r.reference_id = m.reference_id
        WHERE m.validation_status='VALIDATED' AND r.year IS NOT NULL
        GROUP BY FLOOR(r.year/10) ORDER BY FLOOR(r.year/10)
    """)

    # Top pays
    top_countries = db_fetchall("""
        SELECT gs.country_name, gs.country_code, COUNT(*) AS count
        FROM piponto.geographic_scopes gs
        JOIN piponto.models m ON m.model_id = gs.model_id
        WHERE m.validation_status='VALIDATED' AND gs.country_name IS NOT NULL
        GROUP BY gs.country_name, gs.country_code
        ORDER BY count DESC LIMIT 10
    """)

    return StatsResponse(
        validated_models=global_stats["validated"] or 0,
        pending_models=global_stats["pending"] or 0,
        models_with_code=global_stats["with_code"] or 0,
        empirically_validated=global_stats["empirical"] or 0,
        avg_confidence=float(global_stats["avg_confidence"] or 0),
        by_formalism={r["formalism"]: r["count"] for r in by_formalism},
        by_disease=[{"disease": r["disease"], "count": r["count"]} for r in by_disease],
        by_decade=[{"decade": r["decade"], "count": r["count"]} for r in by_decade],
        top_countries=[{"country": r["country_name"], "code": r["country_code"],
                        "count": r["count"]} for r in top_countries],
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 3 : MALADIES
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/diseases",
    response_model=DiseaseListOut,
    summary="Liste des maladies",
    tags=["Maladies"],
)
def list_diseases(
    with_models_only: bool = Query(False, description="Uniquement les maladies avec modèles validés"),
):
    """
    Liste toutes les maladies du référentiel PIPOnto avec leurs caractéristiques
    (pathogène, voie de transmission, priorité OMS) et le nombre de modèles disponibles.
    """
    query = """
        SELECT
            d.disease_id, d.name_fr, d.name_en, d.icd10_code,
            d.pathogen_type, d.pathogen_name, d.transmission_route,
            d.is_zoonotic, d.has_vector, d.vaccine_available,
            d.who_priority, d.endemic_regions, d.uri_m8,
            COUNT(m.model_id) FILTER (WHERE m.validation_status='VALIDATED') AS model_count
        FROM piponto.diseases d
        LEFT JOIN piponto.models m ON m.disease_id = d.disease_id
        GROUP BY d.disease_id
        {}
        ORDER BY d.who_priority DESC NULLS LAST, d.name_en
    """.format("HAVING COUNT(m.model_id) FILTER (WHERE m.validation_status='VALIDATED') > 0"
               if with_models_only else "")

    rows = db_fetchall(query)
    diseases = [DiseaseOut(
        disease_id=r["disease_id"],
        name_fr=r["name_fr"],
        name_en=r["name_en"],
        icd10_code=r["icd10_code"],
        pathogen_type=r["pathogen_type"],
        pathogen_name=r["pathogen_name"],
        transmission_route=r["transmission_route"],
        is_zoonotic=r["is_zoonotic"],
        has_vector=r["has_vector"],
        vaccine_available=r["vaccine_available"],
        who_priority=r["who_priority"],
        endemic_regions=r["endemic_regions"] or [],
        uri_m8=r["uri_m8"],
        model_count=r["model_count"] or 0,
    ) for r in rows]

    return DiseaseListOut(diseases=diseases, total=len(diseases))


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 4 : RECHERCHE DE MODÈLES
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/models/search",
    response_model=ModelSearchOut,
    summary="Rechercher des modèles",
    tags=["Modèles"],
)
def search_models(
    disease: Optional[str] = Query(None,
        description="Nom de la maladie (ex: COVID-19, Malaria, Influenza)"),
    formalism: Optional[str] = Query(None,
        description="Formalisme (SIR, SEIR, ABM, NETWORK, METAPOPULATION...)"),
    country: Optional[str] = Query(None,
        description="Code pays ISO-2 (ex: FR, SN, GB, US)"),
    population: Optional[str] = Query(None,
        description="Population cible (GENERAL, SCHOOL, ELDERLY, URBAN, RURAL...)"),
    has_code: Optional[bool] = Query(None,
        description="Uniquement les modèles avec code disponible"),
    empirical: Optional[bool] = Query(None,
        description="Uniquement les modèles validés empiriquement"),
    model_type: Optional[str] = Query(None,
        description="DETERMINISTIC, STOCHASTIC ou HYBRID"),
    min_confidence: float = Query(0.0,
        description="Score de confiance minimum (0-1)", ge=0, le=1),
    limit: int = Query(20, description="Nombre max de résultats", ge=1, le=100),
    offset: int = Query(0, description="Décalage pour pagination", ge=0),
):
    """
    Recherche multicritère dans la bibliothèque de modèles validés.

    Exemples d'utilisation :
    - `/models/search?disease=COVID-19&formalism=SEIR&country=FR`
    - `/models/search?disease=Malaria&has_code=true&country=SN`
    - `/models/search?formalism=ABM&population=SCHOOL`
    """
    conditions = ["m.validation_status = 'VALIDATED'",
                  "m.extraction_confidence >= %(min_confidence)s"]
    params = {"min_confidence": min_confidence, "limit": limit, "offset": offset}

    if disease:
        conditions.append("""(
            d.name_en ILIKE %(disease_pattern)s
            OR d.name_fr ILIKE %(disease_pattern)s
            OR EXISTS (SELECT 1 FROM piponto.keywords k
                       WHERE k.model_id = m.model_id
                         AND k.keyword ILIKE %(disease_pattern)s)
        )""")
        params["disease_pattern"] = f"%{disease}%"

    if formalism:
        conditions.append("m.formalism = %(formalism)s")
        params["formalism"] = formalism.upper()

    if model_type:
        conditions.append("m.model_type = %(model_type)s")
        params["model_type"] = model_type.upper()

    if country:
        conditions.append("""EXISTS (
            SELECT 1 FROM piponto.geographic_scopes gs
            WHERE gs.model_id = m.model_id
              AND gs.country_code = %(country)s
        )""")
        params["country"] = country.upper()

    if population:
        conditions.append("m.primary_population = %(population)s")
        params["population"] = population.upper()

    if has_code is not None:
        conditions.append("m.has_code = %(has_code)s")
        params["has_code"] = has_code

    if empirical is not None:
        conditions.append("m.is_empirically_validated = %(empirical)s")
        params["empirical"] = empirical

    where = " AND ".join(conditions)

    # Compter le total
    count_row = db_fetchone(f"""
        SELECT COUNT(*) AS total
        FROM piponto.models m
        JOIN piponto.diseases d ON d.disease_id = m.disease_id
        WHERE {where}
    """, params)
    total = count_row["total"] if count_row else 0

    # Récupérer les résultats
    rows = db_fetchall(f"""
        SELECT
            m.model_id, m.name, m.formalism, m.model_type,
            m.spatial_structure, m.is_age_structured,
            m.has_code, m.implementation_url, m.platform,
            m.extraction_confidence, m.is_empirically_validated,
            m.primary_population,
            d.name_en AS disease_name, d.name_fr AS disease_name_fr,
            r.doi, r.year,
            ARRAY_AGG(DISTINCT gs.country_code)
                FILTER (WHERE gs.country_code IS NOT NULL) AS countries,
            COUNT(DISTINCT p.param_id) AS param_count,
            m.uri_m2
        FROM piponto.models m
        JOIN piponto.diseases d ON d.disease_id = m.disease_id
        LEFT JOIN piponto.model_references r ON r.reference_id = m.reference_id
        LEFT JOIN piponto.geographic_scopes gs ON gs.model_id = m.model_id
        LEFT JOIN piponto.parameters p ON p.model_id = m.model_id
        WHERE {where}
        GROUP BY m.model_id, m.name, m.formalism, m.model_type,
                 m.spatial_structure, m.is_age_structured, m.has_code,
                 m.implementation_url, m.platform, m.extraction_confidence,
                 m.is_empirically_validated, m.primary_population,
                 d.name_en, d.name_fr, r.doi, r.year, m.uri_m2
        ORDER BY m.extraction_confidence DESC, m.model_id
        LIMIT %(limit)s OFFSET %(offset)s
    """, params)

    models = [ModelSummary(
        model_id=r["model_id"],
        name=r["name"],
        formalism=r["formalism"],
        model_type=r["model_type"],
        spatial_structure=r["spatial_structure"],
        is_age_structured=r["is_age_structured"],
        disease_name=r["disease_name"],
        disease_name_fr=r["disease_name_fr"],
        has_code=r["has_code"],
        implementation_url=r["implementation_url"],
        platform=r["platform"],
        countries=r["countries"] or [],
        param_count=r["param_count"] or 0,
        extraction_confidence=float(r["extraction_confidence"] or 0),
        is_empirically_validated=r["is_empirically_validated"],
        primary_population=r["primary_population"],
        doi=r["doi"],
        year=r["year"],
        uri_m2=r["uri_m2"],
    ) for r in rows]

    return ModelSearchOut(
        models=models, total=total, limit=limit, offset=offset,
        query={"disease": disease, "formalism": formalism, "country": country,
               "population": population, "has_code": has_code},
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 5 : DÉTAIL D'UN MODÈLE
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/models/{model_id}",
    response_model=ModelDetail,
    summary="Fiche complète d'un modèle",
    tags=["Modèles"],
)
def get_model(model_id: str):
    """
    Retourne la fiche complète d'un modèle avec :
    - Métadonnées complètes
    - Paramètres épidémiologiques (β, γ, σ, R0...)
    - Compartiments (S, E, I, R, D...)
    - Géographies de validation
    - Référence bibliographique
    - URI ontologique PIPOnto M2
    """
    row = db_fetchone("""
        SELECT
            m.*,
            d.name_fr AS disease_name_fr, d.name_en AS disease_name_en,
            d.pathogen_name, d.transmission_route, d.uri_m8 AS disease_uri_m8,
            r.title AS ref_title, r.authors AS ref_authors,
            r.journal AS ref_journal, r.year AS ref_year,
            r.doi AS ref_doi, r.pubmed_id AS ref_pmid,
            r.open_access AS ref_open_access
        FROM piponto.models m
        JOIN piponto.diseases d ON d.disease_id = m.disease_id
        LEFT JOIN piponto.model_references r ON r.reference_id = m.reference_id
        WHERE m.model_id = %(model_id)s
          AND m.validation_status = 'VALIDATED'
    """, {"model_id": model_id})

    if not row:
        raise HTTPException(status_code=404,
                            detail=f"Modèle '{model_id}' non trouvé ou non validé")

    # Paramètres
    params = db_fetchall("""
        SELECT param_id, param_type, symbol, name_fr, name_en,
               default_value, min_value, max_value,
               confidence_interval_low, confidence_interval_high,
               unit, time_unit, is_estimated, estimation_method, notes
        FROM piponto.parameters
        WHERE model_id = %(model_id)s
        ORDER BY param_id
    """, {"model_id": model_id})

    # Compartiments
    compartments = db_fetchall("""
        SELECT symbol, name_en, name_fr, is_infectious,
               is_recovered, is_dead, ode_equation, ordering
        FROM piponto.compartments
        WHERE model_id = %(model_id)s
        ORDER BY ordering NULLS LAST
    """, {"model_id": model_id})

    # Géographies
    geos = db_fetchall("""
        SELECT scope_level, country_code, country_name, region_name,
               population_size, is_primary_scope,
               data_period_start, data_period_end, data_source
        FROM piponto.geographic_scopes
        WHERE model_id = %(model_id)s
        ORDER BY is_primary_scope DESC
    """, {"model_id": model_id})

    return ModelDetail(
        model_id=row["model_id"],
        name=row["name"],
        description=row["description"],
        formalism=row["formalism"],
        model_type=row["model_type"],
        spatial_structure=row["spatial_structure"],
        is_age_structured=row["is_age_structured"],
        is_multi_strain=row["is_multi_strain"],
        has_interventions=row["has_interventions"],
        platform=row["platform"],
        has_code=row["has_code"],
        implementation_url=row["implementation_url"],
        code_license=row["code_license"],
        primary_population=row["primary_population"],
        is_empirically_validated=row["is_empirically_validated"],
        extraction_confidence=float(row["extraction_confidence"] or 0),
        uri_m2=row["uri_m2"],
        # Maladie
        disease_name_fr=row["disease_name_fr"],
        disease_name_en=row["disease_name_en"],
        pathogen_name=row["pathogen_name"],
        transmission_route=row["transmission_route"],
        disease_uri_m8=row["disease_uri_m8"],
        # Référence
        ref_title=row["ref_title"],
        ref_authors=row["ref_authors"],
        ref_journal=row["ref_journal"],
        ref_year=row["ref_year"],
        ref_doi=row["ref_doi"],
        ref_pmid=row["ref_pmid"],
        ref_open_access=row["ref_open_access"],
        # Données liées
        parameters=[ParameterOut(**p) for p in params],
        compartments=[dict(c) for c in compartments],
        geographic_scopes=[dict(g) for g in geos],
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 6 : PARAMÈTRES D'UN MODÈLE
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/models/{model_id}/params",
    summary="Paramètres épidémiologiques d'un modèle",
    tags=["Modèles"],
)
def get_model_params(model_id: str):
    """
    Retourne uniquement les paramètres épidémiologiques d'un modèle,
    formatés pour être utilisés directement dans une simulation.

    Le champ `simulation_ready` indique si les paramètres sont complets
    pour lancer une simulation SEIR standard (β, γ, σ présents).
    """
    # Vérifier que le modèle existe
    model = db_fetchone("""
        SELECT model_id, formalism, model_type
        FROM piponto.models
        WHERE model_id = %(id)s AND validation_status = 'VALIDATED'
    """, {"id": model_id})
    if not model:
        raise HTTPException(404, f"Modèle '{model_id}' non trouvé")

    params = db_fetchall("""
        SELECT param_type, symbol, default_value, min_value, max_value,
               confidence_interval_low, confidence_interval_high,
               unit, time_unit, is_estimated, estimation_method, notes
        FROM piponto.parameters
        WHERE model_id = %(id)s
        ORDER BY param_id
    """, {"id": model_id})

    # Construire un dict ready-to-use pour la simulation
    param_dict = {}
    for p in params:
        param_dict[p["symbol"]] = {
            "value":  float(p["default_value"]),
            "unit":   p["unit"],
            "source": "calibrated" if p["is_estimated"] else "measured",
        }
        if p["min_value"] is not None:
            param_dict[p["symbol"]]["range"] = [
                float(p["min_value"]), float(p["max_value"])
            ]

    # Vérifier si les params nécessaires pour SEIR sont présents
    has_r0   = any(p["param_type"] == "R0" for p in params)
    has_beta = any(p["param_type"] == "TRANSMISSION_RATE" for p in params)
    has_gamma= any(p["param_type"] == "RECOVERY_RATE" for p in params)
    has_sigma= any(p["param_type"] == "INCUBATION_RATE" for p in params)
    formalism = model["formalism"]
    simulation_ready = has_gamma and (has_beta or has_r0)
    if formalism in ("SEIR", "SEIRD", "SEIRS"):
        simulation_ready = simulation_ready and has_sigma

    return {
        "model_id":          model_id,
        "formalism":         formalism,
        "parameters":        params,
        "simulation_dict":   param_dict,
        "simulation_ready":  simulation_ready,
        "missing_for_sim":   [
            sym for sym, present in [
                ("β (transmission rate)", has_beta),
                ("γ (recovery rate)",     has_gamma),
                ("σ (incubation rate)",   has_sigma and formalism in ("SEIR","SEIRD","SEIRS")),
            ] if not present
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 7 : SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/simulate",
    response_model=SimulateResponse,
    summary="Lancer une simulation épidémique",
    tags=["Simulation"],
)
def simulate(req: SimulateRequest):
    """
    Lance une simulation épidémique (SIR ou SEIR) avec les paramètres fournis
    ou récupérés automatiquement depuis un modèle de la bibliothèque.

    ### Modes d'utilisation

    **Mode 1 — Paramètres manuels :**
    ```json
    {
      "formalism": "SEIR",
      "N": 1000000,
      "beta": 0.3,
      "gamma": 0.143,
      "sigma": 0.196,
      "I0": 10,
      "days": 365
    }
    ```

    **Mode 2 — Depuis un modèle de la bibliothèque :**
    ```json
    {
      "model_id": "SEIR_COVID19_Ferguson_2020",
      "N": 68000000,
      "days": 365
    }
    ```
    Les paramètres β, γ, σ sont récupérés automatiquement depuis la base.
    Les valeurs manuelles ont priorité sur les valeurs de la base.
    """
    # Si model_id fourni, récupérer les paramètres depuis la base
    beta = req.beta
    gamma = req.gamma
    sigma = req.sigma
    formalism = req.formalism
    source_params = {}

    if req.model_id:
        model = db_fetchone("""
            SELECT formalism FROM piponto.models
            WHERE model_id = %(id)s AND validation_status = 'VALIDATED'
        """, {"id": req.model_id})
        if not model:
            raise HTTPException(404, f"Modèle '{req.model_id}' non trouvé")

        formalism = model["formalism"]
        params = db_fetchall("""
            SELECT param_type, symbol, default_value
            FROM piponto.parameters WHERE model_id = %(id)s
        """, {"id": req.model_id})

        # Première passe — collecter tous les paramètres disponibles
        r0_val = None
        for p in params:
            val = float(p["default_value"])
            source_params[p["symbol"]] = val
            if p["param_type"] == "TRANSMISSION_RATE" and beta is None:
                beta = val
            elif p["param_type"] == "RECOVERY_RATE" and gamma is None:
                gamma = val
            elif p["param_type"] == "INCUBATION_RATE" and sigma is None:
                sigma = val
            elif p["param_type"] == "R0":
                r0_val = val

        # Deuxième passe — dériver les paramètres manquants
        if r0_val is not None:
            if beta is not None and gamma is None and r0_val > 0:
                # γ = β / R₀  (ex: Ebola β=0.033, R₀=5 → γ=0.0066)
                gamma = round(beta / r0_val, 6)
                source_params["γ (dérivé)"] = gamma
            elif gamma is not None and beta is None:
                # β = R₀ × γ
                beta = round(r0_val * gamma, 6)
                source_params["β (dérivé)"] = beta

        # Pour les modèles ABM/NETWORK/METAPOPULATION sans compartiments SEIR,
        # forcer le formalisme de simulation vers SEIR (approximation valide)
        NON_COMPARTMENTAL = {"ABM", "NETWORK", "METAPOPULATION", "IBM",
                             "BAYESIAN", "BRANCHING_PROCESS", "RENEWAL_EQUATION"}
        if formalism in NON_COMPARTMENTAL:
            formalism = "SEIR"   # approximation compartimentale

    # Validation des paramètres requis
    if gamma is None:
        raise HTTPException(422,
            "Paramètre manquant : gamma (taux de guérison). "
            "Fournissez gamma ou spécifiez un model_id avec R₀ + β pour dérivation automatique.")
    if beta is None:
        raise HTTPException(422,
            "Paramètre manquant : beta (taux de transmission). "
            "Fournissez beta ou spécifiez un model_id avec ce paramètre.")
    if formalism in ("SEIR", "SEIRD", "SEIRS", "SEIS") and sigma is None:
        # Valeur par défaut raisonnable (période incubation 5 jours)
        sigma = 0.196

    # Lancer la simulation
    try:
        result = run_simulation(
            formalism=formalism or "SEIR",
            N=req.N,
            beta=beta,
            gamma=gamma,
            sigma=sigma,
            I0=req.I0,
            R0_init=req.R0_init,
            days=req.days,
            dt=req.dt,
        )
    except Exception as e:
        raise HTTPException(500, f"Erreur de simulation : {e}")

    return SimulateResponse(
        model_id=req.model_id,
        formalism=formalism or "SEIR",
        parameters_used={
            "beta":  beta,
            "gamma": gamma,
            "sigma": sigma,
            "N":     req.N,
            "I0":    req.I0,
            "days":  req.days,
        },
        source_params=source_params,
        **result,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS ONTOLOGIQUES (M0–M8)
# ══════════════════════════════════════════════════════════════════════════════

# Charger l'ontologie au démarrage
_onto = None

def _get_onto():
    global _onto
    if _onto is None:
        try:
            import sys
            for base in [Path(__file__).parent.parent,
                         Path.home() / "piponto"]:
                if (base / "ontology" / "onto_client.py").exists():
                    sys.path.insert(0, str(base))
                    break
            from ontology.onto_client import get_ontology
            rdf_path = Path.home() / "piponto" / "ontology"
            _onto = get_ontology(str(rdf_path))
        except Exception as e:
            logger.warning(f"Ontologie non chargée : {e}")
    return _onto


@app.get(
    "/ontology/disease/{disease_name}",
    summary="Données ontologiques d'une maladie (M8)",
    tags=["Ontologie"],
)
def ontology_disease(disease_name: str):
    """
    Retourne les données ontologiques complètes d'une maladie depuis le module M8.

    Inclut :
    - URI canonique OWL (`http://www.pacadi.org/these/piponto/module8#...`)
    - Formalismes de modélisation recommandés par l'ontologie
    - Plage de R₀ typique, durée d'incubation, IFR
    - Paramètres épidémiologiques par défaut (β, γ, σ)
    - Note ontologique justifiant les recommandations
    """
    onto = _get_onto()
    if not onto:
        raise HTTPException(503, "Ontologie non disponible")

    d = onto.get_disease_by_search(disease_name) or onto.get_disease(disease_name)
    if not d:
        raise HTTPException(404, f"Maladie '{disease_name}' non trouvée dans l'ontologie M8")

    formalism = d.get("best_formalism", ["SEIR"])[0]
    fp = onto.get_formalism_class(formalism)

    return {
        "disease_name":          d.get("label_en"),
        "disease_name_fr":       d.get("label_fr"),
        "uri_m8":                d.get("uri"),
        "icd10":                 d.get("icd10"),
        "pathogen_uri":          d.get("pathogen"),
        "transmission_route":    d.get("transmission"),
        "recommended_formalisms":d.get("best_formalism", []),
        "primary_formalism":     formalism,
        "primary_model_class":   fp.get("owl_class"),
        "model_hierarchy":       onto.get_model_class_hierarchy(formalism),
        "r0_range":              d.get("r0_range"),
        "incubation_days":       d.get("incubation_d"),
        "infectious_days":       d.get("infectious_d"),
        "ifr":                   d.get("ifr"),
        "compartments":          fp.get("compartments", []),
        "required_params":       fp.get("required_params", []),
        "ode_equations":         fp.get("equations", []),
        "typical_params":        d.get("key_params", {}),
        "ontological_note":      d.get("note"),
        "namespace":             "http://www.pacadi.org/these/piponto/module8#",
        "module":                "M8 — Domaine Épidémiologique",
    }


@app.get(
    "/ontology/model/{model_id}",
    summary="URI et classe ontologique d'un modèle (M2)",
    tags=["Ontologie"],
)
def ontology_model(model_id: str):
    """
    Retourne la représentation ontologique d'un modèle depuis M2.

    Inclut :
    - URI canonique M2 du modèle
    - Classe OWL (SEIRModel, ABMModel...)
    - Hiérarchie complète (Model → CompartmentalModel → SEIRModel)
    - Cohérence ontologique avec la maladie modélisée
    - URI M4 pour la simulation associée
    """
    onto = _get_onto()
    if not onto:
        raise HTTPException(503, "Ontologie non disponible")

    model = db_fetchone("""
        SELECT m.model_id, m.formalism, m.model_type, d.name_en AS disease_name
        FROM piponto.models m
        LEFT JOIN piponto.diseases d ON m.disease_id = d.disease_id
        WHERE m.model_id = %(id)s AND m.validation_status = 'VALIDATED'
    """, {"id": model_id})

    if not model:
        raise HTTPException(404, f"Modèle '{model_id}' non trouvé")

    formalism    = model["formalism"] or "SEIR"
    disease_name = model["disease_name"] or ""
    fp           = onto.get_formalism_class(formalism)
    hierarchy    = onto.get_model_class_hierarchy(formalism)
    validation   = onto.validate_model_formalism(model_id, formalism, disease_name)

    return {
        "model_id":              model_id,
        "uri_m2":                onto.get_model_uri(model_id),
        "uri_m4_simulation":     onto.get_simulation_uri(model_id),
        "owl_class":             fp.get("owl_class"),
        "class_name":            hierarchy[-1] if hierarchy else "Model",
        "class_hierarchy":       hierarchy,
        "formalism":             formalism,
        "model_type":            model["model_type"],
        "compartments":          fp.get("compartments", []),
        "ode_equations":         fp.get("equations", []),
        "disease_uri_m8":        onto.get_disease_uri(disease_name),
        "ontologically_consistent": validation["ontologically_consistent"],
        "validation_report":     validation,
        "namespace_m2":          "http://www.pacadi.org/these/piponto/module2#",
        "namespace_m4":          "http://www.pacadi.org/these/piponto/module4#",
        "modules_used":          ["M2 — Ontologie des Modèles",
                                  "M4 — Ontologie de la Simulation",
                                  "M8 — Domaine Épidémiologique"],
    }


@app.get(
    "/ontology/stats",
    summary="Statistiques de l'ontologie PIPOnto chargée",
    tags=["Ontologie"],
)
def ontology_stats():
    """
    Retourne les statistiques de l'ontologie PIPOnto chargée en mémoire.

    Indique combien de modules RDF sont actifs, le nombre de classes
    et d'individus OWL indexés.
    """
    onto = _get_onto()
    if not onto:
        return {
            "status": "unavailable",
            "message": "Ontologie non chargée. Vérifiez ~/piponto/ontology/",
            "modules_loaded": 0,
        }
    stats = onto.get_stats()
    return {
        "status":             "ok",
        "modules_loaded":     stats["modules_loaded"],
        "modules_available":  stats["modules_available"],
        "classes_indexed":    stats["classes_indexed"],
        "individuals_indexed":stats["individuals_indexed"],
        "diseases_in_kb":     stats["diseases_in_kb"],
        "formalisms_in_kb":   stats["formalisms_in_kb"],
        "base_namespace":     "http://www.pacadi.org/these/piponto/",
        "modules": {
            "M0": "Fondations Ontologiques",
            "M1": "Systèmes Complexes",
            "M2": "Ontologie des Modèles",
            "M4": "Simulation",
            "M5": "Expérimentation",
            "M6": "Résultats",
            "M7": "Interopérabilité",
            "M8": "Domaine Épidémiologique",
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS D'ERREUR
# ══════════════════════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Erreur non gérée : {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur", "type": type(exc).__name__},
    )
