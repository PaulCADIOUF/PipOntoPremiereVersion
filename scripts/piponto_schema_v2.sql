-- =============================================================================
-- PIPOnto — Bibliothèque de Modèles Épidémiologiques
-- Schéma PostgreSQL v1.0
-- =============================================================================
-- 12 tables pour 500+ modèles de maladies infectieuses
-- Couvre : compartimentaux, ABM, spatiaux, stochastiques, calibrés
--
-- Architecture :
--   models              ← table centrale (1 ligne = 1 modèle publié)
--   references          ← article source (DOI, auteurs, journal)
--   parameters          ← β, γ, σ, R0... avec intervalles
--   compartments        ← S, E, I, R, D... et équations
--   diseases            ← maladie, pathogène, voie de transmission
--   geographic_scopes   ← territoire(s) de validation
--   population_contexts ← population cible, âges, matrice contacts
--   keywords            ← mots-clés NLP pondérés
--   code_artifacts      ← fichiers code (GAMA, Python, R, NetLogo)
--   calibration_data    ← données utilisées pour calibration
--   validation_results  ← métriques de validation (RMSE, R², MAE)
--   extraction_log      ← traçabilité de l'extraction (auto/manuel)
-- =============================================================================

-- Extensions nécessaires
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- recherche textuelle fuzzy

-- Schéma dédié
CREATE SCHEMA IF NOT EXISTS piponto;
SET search_path TO piponto, public;


-- =============================================================================
-- TYPES ÉNUMÉRÉS
-- =============================================================================

CREATE TYPE model_formalism AS ENUM (
    'SIR', 'SIS', 'SIRS',
    'SEIR', 'SEIRS', 'SEIS',
    'SEIRD', 'SEIRHD',
    'MSIR', 'MSEIR',
    'ABM',                   -- Agent-Based Model
    'NETWORK',               -- Modèle en réseau
    'METAPOPULATION',        -- Métapopulation / patches
    'IBM',                   -- Individual-Based Model
    'STOCHASTIC_SIR',
    'STOCHASTIC_SEIR',
    'BRANCHING_PROCESS',
    'RENEWAL_EQUATION',
    'BAYESIAN',
    'MIXED',                 -- combinaison de plusieurs formalismes
    'OTHER'
);

CREATE TYPE model_type AS ENUM (
    'DETERMINISTIC',
    'STOCHASTIC',
    'HYBRID'
);

CREATE TYPE spatial_structure AS ENUM (
    'NONE',                  -- modèle non spatial (bien-mélangé)
    'METAPOPULATION',        -- patches/régions connectées
    'NETWORK',               -- réseau de contacts explicite
    'GRID',                  -- grille géographique (cellulaire)
    'CONTINUOUS'             -- espace continu
);

CREATE TYPE validation_status AS ENUM (
    'PENDING',               -- pas encore validé manuellement
    'VALIDATED',             -- validé et approuvé
    'REJECTED',              -- rejeté (données insuffisantes)
    'NEEDS_REVIEW'           -- extraction incertaine, révision nécessaire
);

CREATE TYPE extraction_method AS ENUM (
    'MANUAL',                -- saisie manuelle par le chercheur
    'AUTO_PDF',              -- extraction automatique depuis PDF
    'AUTO_NLP',              -- extraction NLP depuis texte article
    'AUTO_GITHUB',           -- extraction depuis dépôt GitHub
    'IMPORT_CSV'             -- import depuis fichier CSV externe
);

CREATE TYPE param_type AS ENUM (
    'TRANSMISSION_RATE',     -- β : taux de transmission
    'RECOVERY_RATE',         -- γ : taux de guérison
    'INCUBATION_RATE',       -- σ : taux sortie latence (1/période incubation)
    'WANING_IMMUNITY_RATE',  -- ω : taux de perte d'immunité
    'MORTALITY_RATE',        -- μ : taux de mortalité liée à la maladie
    'BIRTH_RATE',            -- λ : taux de naissance
    'NATURAL_DEATH_RATE',    -- δ : taux de mortalité naturelle
    'VACCINATION_RATE',      -- ν : taux de vaccination
    'HOSPITALIZATION_RATE',  -- η : taux d'hospitalisation
    'R0',                    -- nombre de reproduction de base
    'SERIAL_INTERVAL',       -- intervalle sériel (jours)
    'GENERATION_TIME',       -- temps de génération (jours)
    'CASE_FATALITY_RATE',    -- CFR
    'CONTACT_RATE',          -- taux de contact moyen
    'VECTOR_BITING_RATE',    -- (maladies vectorielles)
    'VECTOR_COMPETENCE',     -- (maladies vectorielles)
    'OTHER'
);

CREATE TYPE transmission_route AS ENUM (
    'AIRBORNE',              -- transmission aérienne (gouttelettes/aérosols)
    'DROPLET',               -- gouttelettes
    'CONTACT_DIRECT',        -- contact direct
    'CONTACT_INDIRECT',      -- contact indirect (surfaces)
    'FECAL_ORAL',            -- fécal-oral
    'VECTOR_BORNE',          -- transmission vectorielle (moustiques...)
    'BLOODBORNE',            -- transmission sanguine
    'SEXUAL',                -- transmission sexuelle
    'VERTICAL',              -- transmission mère-enfant
    'WATERBORNE',            -- transmission hydrique
    'FOODBORNE',             -- transmission alimentaire
    'ZOONOTIC',              -- zoonose
    'MIXED'
);

CREATE TYPE population_type AS ENUM (
    'GENERAL',               -- population générale
    'SCHOOL',                -- population scolaire (6-18 ans)
    'ELDERLY',               -- personnes âgées (65+)
    'HEALTHCARE_WORKERS',    -- soignants
    'URBAN',                 -- population urbaine dense
    'RURAL',                 -- population rurale
    'CHILDREN_UNDER5',       -- enfants < 5 ans (maladies infantiles)
    'IMMUNOCOMPROMISED',     -- immunodéprimés
    'PREGNANT',              -- femmes enceintes
    'LIVESTOCK',             -- bétail (maladies zoonotiques)
    'MIXED'
);

CREATE TYPE platform_type AS ENUM (
    'PYTHON',
    'R',
    'MATLAB',
    'GAMA',
    'NETLOGO',
    'REPAST',
    'MESA',
    'JULIA',
    'C_CPP',
    'JAVA',
    'MATHEMATICA',
    'OTHER'
);


-- =============================================================================
-- TABLE 1 : diseases
-- Référentiel des maladies infectieuses
-- =============================================================================

CREATE TABLE diseases (
    disease_id          SERIAL PRIMARY KEY,
    name_fr             VARCHAR(200) NOT NULL,
    name_en             VARCHAR(200) NOT NULL,
    icd10_code          VARCHAR(10),           -- code CIM-10 (ex: J09-J18 pour grippe)
    mesh_id             VARCHAR(20),           -- MeSH term ID (NCBI)
    pathogen_type       VARCHAR(50),           -- Virus, Bacteria, Parasite, Prion, Fungi
    pathogen_name       VARCHAR(200),          -- SARS-CoV-2, Plasmodium falciparum...
    transmission_route  transmission_route NOT NULL,
    is_zoonotic         BOOLEAN DEFAULT FALSE,
    has_vector          BOOLEAN DEFAULT FALSE,
    vector_name         VARCHAR(100),          -- Anopheles, Aedes aegypti...
    natural_immunity    BOOLEAN,               -- immunité naturelle durable ?
    vaccine_available   BOOLEAN DEFAULT FALSE,
    who_priority        BOOLEAN DEFAULT FALSE, -- maladie prioritaire OMS
    endemic_regions     TEXT[],                -- régions endémiques
    uri_m8              VARCHAR(300),          -- URI PIPOnto M8
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE diseases IS
    'Référentiel des maladies infectieuses. Une maladie peut être associée '
    'à plusieurs modèles dans la table models.';

-- Index texte pour recherche NLP
CREATE INDEX idx_diseases_name_fr_trgm ON diseases
    USING gin (name_fr gin_trgm_ops);
CREATE INDEX idx_diseases_name_en_trgm ON diseases
    USING gin (name_en gin_trgm_ops);
CREATE INDEX idx_diseases_icd10 ON diseases (icd10_code);


-- =============================================================================
-- TABLE 2 : references
-- Articles scientifiques sources des modèles
-- =============================================================================

CREATE TABLE model_references (
    reference_id        SERIAL PRIMARY KEY,
    doi                 VARCHAR(200) UNIQUE,
    pubmed_id           VARCHAR(20) UNIQUE,
    arxiv_id            VARCHAR(30),
    title               TEXT NOT NULL,
    authors             TEXT NOT NULL,           -- "Nom1 A, Nom2 B, ..."
    authors_list        JSONB,                   -- [{"name": "...", "affiliation": "..."}]
    journal             VARCHAR(300),
    year                SMALLINT NOT NULL,
    volume              VARCHAR(20),
    issue               VARCHAR(20),
    pages               VARCHAR(30),
    abstract            TEXT,
    citation_count      INTEGER DEFAULT 0,
    impact_factor       NUMERIC(6,3),
    open_access         BOOLEAN,
    pdf_url             VARCHAR(500),
    pdf_path            VARCHAR(500),            -- chemin local après téléchargement
    github_url          VARCHAR(500),            -- code source si disponible
    language            CHAR(2) DEFAULT 'en',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE model_references IS
    'Articles scientifiques sources. Un article peut décrire plusieurs modèles '
    '(ex: article comparatif avec 3 variantes SEIR).';

CREATE INDEX idx_references_doi ON model_references (doi);
CREATE INDEX idx_references_year ON model_references (year);
CREATE INDEX idx_references_citations ON model_references (citation_count DESC);
CREATE INDEX idx_references_title_trgm ON model_references
    USING gin (title gin_trgm_ops);


-- =============================================================================
-- TABLE 3 : models  ← TABLE CENTRALE
-- Un modèle = une instance publiée avec ses métadonnées complètes
-- =============================================================================

CREATE TABLE models (
    model_id            VARCHAR(100) PRIMARY KEY,
    -- Format recommandé : FORMALISM_DISEASE_AUTHOR_YEAR
    -- Ex : SEIR_COVID_Ferguson_2020, ABM_Influenza_Kerr_2021

    reference_id        INTEGER REFERENCES model_references(reference_id)
                            ON DELETE SET NULL,
    disease_id          INTEGER NOT NULL REFERENCES diseases(disease_id)
                            ON DELETE RESTRICT,

    -- Identité du modèle
    name                VARCHAR(300) NOT NULL,
    short_name          VARCHAR(100),           -- nom court pour affichage
    description         TEXT,
    version             VARCHAR(20),            -- v1.0, v2.3...

    -- Classification formelle
    formalism           model_formalism NOT NULL,
    model_type          model_type NOT NULL,
    spatial_structure   spatial_structure DEFAULT 'NONE',
    is_age_structured   BOOLEAN DEFAULT FALSE,  -- compartiments par tranche d'âge
    is_multi_strain     BOOLEAN DEFAULT FALSE,  -- plusieurs souches/variants
    is_multi_host       BOOLEAN DEFAULT FALSE,  -- humains + vecteur + réservoir
    has_interventions   BOOLEAN DEFAULT FALSE,  -- modélise des interventions NPIs

    -- Plateforme d'implémentation
    platform            platform_type,
    platform_version    VARCHAR(30),
    implementation_url  VARCHAR(500),           -- GitHub, Zenodo, CoMSES...
    has_code            BOOLEAN DEFAULT FALSE,
    code_license        VARCHAR(50),            -- MIT, GPL, CC-BY...

    -- Qualité et pertinence
    quality_score       NUMERIC(4,3)
                            CHECK (quality_score BETWEEN 0 AND 1),
    -- Score calculé : 0.4×citations_normalisées
    --               + 0.3×validation_empirique
    --               + 0.2×code_disponible
    --               + 0.1×reproductibilité

    is_empirically_validated BOOLEAN DEFAULT FALSE,
    -- A été comparé à des données réelles dans l'article

    -- Population cible principale
    primary_population  population_type DEFAULT 'GENERAL',

    -- Statut ontologie PIPOnto
    uri_m2              VARCHAR(300),           -- URI M2 PIPOnto
    validation_status   validation_status DEFAULT 'PENDING',
    validated_by        VARCHAR(100),           -- identifiant du validateur
    validated_at        TIMESTAMPTZ,
    rejection_reason    TEXT,

    -- Traçabilité extraction
    extracted_by        extraction_method DEFAULT 'MANUAL',
    extraction_confidence NUMERIC(4,3)
                            CHECK (extraction_confidence BETWEEN 0 AND 1),
    -- Confiance de l'extraction automatique (1.0 = manuel)
    extraction_notes    TEXT,                   -- remarques sur l'extraction

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE models IS
    'Table centrale. Chaque ligne est un modèle épidémiologique publié. '
    'Un article (model_references) peut générer plusieurs modèles (ex: modèle de base + '
    'variante avec vaccination). Le model_id est l URI locale PIPOnto M2.';

CREATE INDEX idx_models_disease ON models (disease_id);
CREATE INDEX idx_models_formalism ON models (formalism);
CREATE INDEX idx_models_type ON models (model_type);
CREATE INDEX idx_models_status ON models (validation_status);
CREATE INDEX idx_models_quality ON models (quality_score DESC);
CREATE INDEX idx_models_has_code ON models (has_code);
CREATE INDEX idx_models_population ON models (primary_population);
CREATE INDEX idx_models_name_trgm ON models
    USING gin (name gin_trgm_ops);


-- =============================================================================
-- TABLE 4 : parameters
-- Paramètres épidémiologiques publiés dans l'article
-- =============================================================================

CREATE TABLE parameters (
    param_id            SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    param_type          param_type NOT NULL,
    symbol              VARCHAR(30) NOT NULL,   -- β, γ, σ, R0, ω...
    name_fr             VARCHAR(200),
    name_en             VARCHAR(200),
    default_value       NUMERIC(18,8) NOT NULL, -- valeur centrale publiée
    min_value           NUMERIC(18,8),
    max_value           NUMERIC(18,8),
    confidence_interval_low  NUMERIC(18,8),     -- IC 95% borne inférieure
    confidence_interval_high NUMERIC(18,8),     -- IC 95% borne supérieure
    unit                VARCHAR(50),            -- jour⁻¹, semaine⁻¹, sans unité...
    time_unit           VARCHAR(20) DEFAULT 'day',  -- day, week, year
    formula             TEXT,
    -- ex: "β = R0 × γ / N" ou "σ = 1 / incubation_period"
    is_calibratable     BOOLEAN DEFAULT TRUE,
    is_estimated        BOOLEAN DEFAULT FALSE,  -- estimé (pas mesuré directement)
    estimation_method   VARCHAR(100),           -- MLE, MCMC, ABC...
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE parameters IS
    'Paramètres épidémiologiques tels que publiés dans l article source. '
    'Un modèle a typiquement 3-15 paramètres. Les valeurs calibrées localement '
    'sont stockées dans calibration_data.';

CREATE INDEX idx_parameters_model ON parameters (model_id);
CREATE INDEX idx_parameters_type ON parameters (param_type);
CREATE INDEX idx_parameters_symbol ON parameters (symbol);


-- =============================================================================
-- TABLE 5 : compartments
-- Compartiments du modèle et leurs équations différentielles
-- =============================================================================

CREATE TABLE compartments (
    compartment_id      SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    symbol              VARCHAR(10) NOT NULL,   -- S, E, I, R, D, V, H...
    name_fr             VARCHAR(100),
    name_en             VARCHAR(100),
    description         TEXT,
    ode_equation        TEXT,
    -- ex: "dI/dt = β*S*I/N - γ*I"
    initial_condition   TEXT,
    -- ex: "I(0) = 1, S(0) = N-1, R(0) = 0"
    initial_fraction    NUMERIC(6,5),           -- fraction initiale de la population
    is_infectious       BOOLEAN DEFAULT FALSE,
    is_recovered        BOOLEAN DEFAULT FALSE,
    is_dead             BOOLEAN DEFAULT FALSE,
    ordering            SMALLINT,               -- ordre d'affichage S=1,E=2,I=3...
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE compartments IS
    'Compartiments du modèle avec leurs équations différentielles. '
    'Permet la reconstruction complète du système d équations.';

CREATE INDEX idx_compartments_model ON compartments (model_id);
CREATE UNIQUE INDEX idx_compartments_model_symbol
    ON compartments (model_id, symbol);


-- =============================================================================
-- TABLE 6 : geographic_scopes
-- Territoires sur lesquels le modèle a été validé
-- =============================================================================

CREATE TABLE geographic_scopes (
    scope_id            SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    scope_level         VARCHAR(20) NOT NULL,
    -- 'city', 'region', 'country', 'continent', 'global'
    country_code        CHAR(2),                -- ISO 3166-1 alpha-2
    country_name        VARCHAR(100),
    region_name         VARCHAR(200),           -- région/province/état
    city_name           VARCHAR(100),
    population_size     BIGINT,                 -- taille de la population N
    population_density  NUMERIC(10,2),          -- habitants/km²
    is_primary_scope    BOOLEAN DEFAULT TRUE,   -- territoire principal de validation
    data_period_start   DATE,                   -- début de la période de données
    data_period_end     DATE,
    data_source         VARCHAR(300),           -- source des données (OMS, INSEE...)
    uri_m8              VARCHAR(300),           -- URI M8 PIPOnto
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE geographic_scopes IS
    'Territoire(s) sur lesquels le modèle a été validé empiriquement. '
    'Un modèle peut avoir plusieurs scopes (ex: validé UK + France).';

CREATE INDEX idx_geo_scopes_model ON geographic_scopes (model_id);
CREATE INDEX idx_geo_scopes_country ON geographic_scopes (country_code);
CREATE INDEX idx_geo_scopes_level ON geographic_scopes (scope_level);


-- =============================================================================
-- TABLE 7 : population_contexts
-- Contextes populationnels du modèle
-- =============================================================================

CREATE TABLE population_contexts (
    context_id          SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    population_type     population_type NOT NULL,
    age_min             SMALLINT,               -- âge minimum (années)
    age_max             SMALLINT,               -- âge maximum (années)
    age_groups          TEXT[],                 -- ["0-4", "5-14", "15-64", "65+"]
    contact_matrix_source VARCHAR(200),         -- POLYMOD, Prem2017, autre...
    contact_matrix      JSONB,
    -- {"home": [[...]], "school": [[...]], "work": [[...]], "other": [[...]]}
    specific_params     JSONB,
    -- paramètres spécifiques à cette population
    -- ex: {"beta_school": 0.6, "school_attendance": 0.85}
    description         TEXT,
    uri_m2              VARCHAR(300),           -- URI M2 PopulationContext
    uri_m8              VARCHAR(300),           -- URI M8 PopulationGroup
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE population_contexts IS
    'Contexte populationnel du modèle. Lie M2 PopulationContext et M8 PopulationGroup. '
    'La matrice de contacts (JSONB) permet la calibration par tranche d âge.';

CREATE INDEX idx_pop_contexts_model ON population_contexts (model_id);
CREATE INDEX idx_pop_contexts_type ON population_contexts (population_type);


-- =============================================================================
-- TABLE 8 : keywords
-- Mots-clés NLP pour la recherche sémantique
-- =============================================================================

CREATE TABLE keywords (
    keyword_id          SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    keyword             VARCHAR(100) NOT NULL,
    language            CHAR(2) DEFAULT 'en',  -- 'fr' ou 'en'
    weight              NUMERIC(4,3) DEFAULT 1.0
                            CHECK (weight BETWEEN 0 AND 1),
    -- 1.0 = terme central, 0.5 = terme secondaire
    source              VARCHAR(50) DEFAULT 'manual',
    -- 'manual', 'title', 'abstract', 'mesh', 'auto'
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE keywords IS
    'Mots-clés NLP pondérés pour la recherche sémantique. '
    'Source: saisie manuelle, extraction depuis titre/abstract, termes MeSH, '
    'ou génération automatique.';

CREATE INDEX idx_keywords_model ON keywords (model_id);
CREATE INDEX idx_keywords_keyword_trgm ON keywords
    USING gin (keyword gin_trgm_ops);
CREATE INDEX idx_keywords_language ON keywords (language);


-- =============================================================================
-- TABLE 9 : code_artifacts
-- Fichiers de code associés au modèle
-- =============================================================================

CREATE TABLE code_artifacts (
    artifact_id         SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    platform            platform_type NOT NULL,
    file_name           VARCHAR(300),
    file_path           VARCHAR(500),           -- chemin local (MinIO/S3)
    file_url            VARCHAR(500),           -- URL publique (GitHub/Zenodo)
    file_format         VARCHAR(20),            -- .py, .R, .gaml, .nlogo, .m...
    file_size_kb        INTEGER,
    description         TEXT,
    is_runnable         BOOLEAN DEFAULT FALSE,  -- testé et fonctionnel
    dependencies        TEXT[],                 -- ["numpy", "scipy", "pandas"]
    run_command         TEXT,
    -- ex: "python3 seir_model.py --N 1000000 --beta 0.3"
    docker_image        VARCHAR(200),           -- image Docker si disponible
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE code_artifacts IS
    'Fichiers de code associés au modèle. Stockage physique dans MinIO/S3, '
    'référencé ici par chemin et URL.';

CREATE INDEX idx_artifacts_model ON code_artifacts (model_id);
CREATE INDEX idx_artifacts_platform ON code_artifacts (platform);
CREATE INDEX idx_artifacts_runnable ON code_artifacts (is_runnable);


-- =============================================================================
-- TABLE 10 : calibration_data
-- Données utilisées pour calibrer le modèle (dans l'article source)
-- =============================================================================

CREATE TABLE calibration_data (
    calibration_id      SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    data_source_name    VARCHAR(300) NOT NULL,  -- OMS, INSEE, ECDC, SPF...
    data_source_url     VARCHAR(500),
    country_code        CHAR(2),
    data_type           VARCHAR(100),
    -- "confirmed_cases", "hospitalizations", "deaths", "seroprevalence"
    time_period_start   DATE,
    time_period_end     DATE,
    n_observations      INTEGER,               -- nombre de points de données
    calibration_method  VARCHAR(100),          -- MLE, MCMC, ABC, Least Squares...
    calibrated_params   TEXT[],               -- ["beta", "gamma", "sigma"]
    gof_metric          VARCHAR(20),           -- R2, RMSE, MAE, AIC, BIC
    gof_value           NUMERIC(12,6),
    is_cross_validated  BOOLEAN DEFAULT FALSE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE calibration_data IS
    'Données et méthode utilisées pour calibrer le modèle dans l article source. '
    'Distinct des calibrations locales PIPOnto (stockées séparément).';

CREATE INDEX idx_calibration_model ON calibration_data (model_id);
CREATE INDEX idx_calibration_country ON calibration_data (country_code);


-- =============================================================================
-- TABLE 11 : validation_results
-- Résultats de validation empirique publiés
-- =============================================================================

CREATE TABLE validation_results (
    validation_id       SERIAL PRIMARY KEY,
    model_id            VARCHAR(100) NOT NULL
                            REFERENCES models(model_id) ON DELETE CASCADE,
    validation_type     VARCHAR(50),
    -- "retrospective", "prospective", "cross_validation", "out_of_sample"
    metric_name         VARCHAR(30),           -- RMSE, R2, MAE, MAPE, loglik
    metric_value        NUMERIC(15,8),
    country_code        CHAR(2),
    validation_period   VARCHAR(50),           -- "2020-03 / 2020-06"
    outcome_variable    VARCHAR(100),
    -- "daily_cases", "peak_timing", "attack_rate", "Rt"
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_validation_model ON validation_results (model_id);
CREATE INDEX idx_validation_metric ON validation_results (metric_name);


-- =============================================================================
-- TABLE 12 : extraction_log
-- Traçabilité complète de chaque extraction (auto ou manuelle)
-- =============================================================================

CREATE TABLE extraction_log (
    log_id              SERIAL PRIMARY KEY,
    model_id            VARCHAR(100)
                            REFERENCES models(model_id) ON DELETE CASCADE,
    reference_id        INTEGER
                            REFERENCES model_references(reference_id) ON DELETE CASCADE,

    -- Source de l'extraction
    extraction_method   extraction_method NOT NULL,
    extracted_at        TIMESTAMPTZ DEFAULT NOW(),
    extractor_version   VARCHAR(20),           -- version du script d'extraction
    extractor_name      VARCHAR(100),          -- nom de l'extracteur ou de la personne

    -- Qualité de l'extraction automatique
    raw_extraction      JSONB,
    -- données brutes extraites avant normalisation
    confidence_scores   JSONB,
    -- {"formalism": 0.95, "parameters": 0.72, "population": 0.88}

    -- Validation manuelle
    manual_review_at    TIMESTAMPTZ,
    reviewed_by         VARCHAR(100),
    review_notes        TEXT,
    final_status        validation_status DEFAULT 'PENDING',

    -- Champs modifiés lors de la validation
    fields_corrected    TEXT[],
    -- ["formalism", "parameters.beta", "geographic_scope.country"]
    correction_summary  TEXT
);

COMMENT ON TABLE extraction_log IS
    'Journal complet de chaque extraction. Permet de suivre : '
    'quand le modèle a été extrait, par quelle méthode, '
    'quels champs ont été corrigés lors de la validation manuelle.';

CREATE INDEX idx_log_model ON extraction_log (model_id);
CREATE INDEX idx_log_method ON extraction_log (extraction_method);
CREATE INDEX idx_log_status ON extraction_log (final_status);
CREATE INDEX idx_log_extracted_at ON extraction_log (extracted_at DESC);


-- =============================================================================
-- VUES UTILITAIRES
-- =============================================================================

-- Vue principale : modèle complet avec toutes ses métadonnées
CREATE OR REPLACE VIEW v_models_full AS
SELECT
    m.model_id,
    m.name,
    m.formalism::TEXT,
    m.model_type::TEXT,
    m.spatial_structure::TEXT,
    m.is_age_structured,
    CASE WHEN m.model_type = 'STOCHASTIC' THEN TRUE ELSE FALSE END AS is_stochastic_flag,  -- alias pour model_type = 'STOCHASTIC'
    m.quality_score,
    m.validation_status::TEXT,
    m.has_code,
    m.platform::TEXT,
    m.implementation_url,
    -- Maladie
    d.name_fr                   AS disease_fr,
    d.name_en                   AS disease_en,
    d.icd10_code,
    d.transmission_route::TEXT,
    d.who_priority,
    -- Référence
    r.doi,
    r.authors,
    r.year,
    r.journal,
    r.citation_count,
    r.github_url,
    -- Agrégats
    (SELECT COUNT(*) FROM parameters p WHERE p.model_id = m.model_id)
        AS param_count,
    (SELECT COUNT(*) FROM compartments c WHERE c.model_id = m.model_id)
        AS compartment_count,
    (SELECT COUNT(*) FROM geographic_scopes gs WHERE gs.model_id = m.model_id)
        AS scope_count,
    (SELECT ARRAY_AGG(DISTINCT gs.country_code)
     FROM geographic_scopes gs WHERE gs.model_id = m.model_id)
        AS country_codes,
    (SELECT ARRAY_AGG(k.keyword ORDER BY k.weight DESC)
     FROM keywords k WHERE k.model_id = m.model_id LIMIT 10)
        AS top_keywords
FROM models m
LEFT JOIN diseases d ON m.disease_id = d.disease_id
LEFT JOIN model_references r ON m.reference_id = r.reference_id;


-- Vue : statistiques de la bibliothèque
CREATE OR REPLACE VIEW v_library_stats AS
SELECT
    COUNT(*)                        AS total_models,
    COUNT(*) FILTER (WHERE validation_status = 'VALIDATED')
                                    AS validated_models,
    COUNT(*) FILTER (WHERE validation_status = 'PENDING')
                                    AS pending_models,
    COUNT(*) FILTER (WHERE has_code = TRUE)
                                    AS models_with_code,
    COUNT(DISTINCT disease_id)      AS diseases_covered,
    ROUND(AVG(quality_score), 3)    AS avg_quality_score,
    COUNT(*) FILTER (WHERE model_type = 'STOCHASTIC')
                                    AS stochastic_models,
    COUNT(*) FILTER (WHERE spatial_structure != 'NONE')
                                    AS spatial_models,
    COUNT(*) FILTER (WHERE is_age_structured = TRUE)
                                    AS age_structured_models
FROM models;


-- Vue : recherche NLP — modèles par maladie + population + géo
CREATE OR REPLACE VIEW v_nlp_search AS
SELECT
    m.model_id,
    m.name,
    m.formalism::TEXT,
    m.quality_score,
    d.name_en                       AS disease_en,
    d.name_fr                       AS disease_fr,
    d.uri_m8                        AS disease_uri_m8,
    m.primary_population::TEXT,
    m.uri_m2,
    ARRAY_AGG(DISTINCT gs.country_code)
        FILTER (WHERE gs.country_code IS NOT NULL)
                                    AS countries,
    ARRAY_AGG(DISTINCT k.keyword)
        FILTER (WHERE k.keyword IS NOT NULL)
                                    AS keywords
FROM models m
JOIN diseases d ON m.disease_id = d.disease_id
LEFT JOIN geographic_scopes gs ON gs.model_id = m.model_id
LEFT JOIN keywords k ON k.model_id = m.model_id
WHERE m.validation_status = 'VALIDATED'
GROUP BY m.model_id, m.name, m.formalism, m.quality_score,
         d.name_en, d.name_fr, d.uri_m8,
         m.primary_population, m.uri_m2;


-- =============================================================================
-- FONCTIONS UTILITAIRES
-- =============================================================================

-- Calcule le quality_score d'un modèle selon les critères PIPOnto
CREATE OR REPLACE FUNCTION compute_quality_score(p_model_id VARCHAR)
RETURNS NUMERIC AS $$
DECLARE
    v_citations     INTEGER;
    v_max_citations INTEGER;
    v_has_code      BOOLEAN;
    v_is_validated  BOOLEAN;
    v_has_calib     BOOLEAN;
    v_score         NUMERIC;
BEGIN
    SELECT
        COALESCE(r.citation_count, 0),
        m.has_code,
        m.is_empirically_validated
    INTO v_citations, v_has_code, v_is_validated
    FROM models m
    LEFT JOIN model_references r ON m.reference_id = r.reference_id
    WHERE m.model_id = p_model_id;

    SELECT MAX(COALESCE(r.citation_count, 0))
    INTO v_max_citations
    FROM models m
    LEFT JOIN model_references r ON m.reference_id = r.reference_id;

    SELECT EXISTS (
        SELECT 1 FROM calibration_data WHERE model_id = p_model_id
    ) INTO v_has_calib;

    v_score :=
        0.40 * CASE WHEN v_max_citations > 0
                    THEN LEAST(v_citations::NUMERIC / v_max_citations, 1.0)
                    ELSE 0 END
      + 0.30 * CASE WHEN v_is_validated THEN 1.0 ELSE 0 END
      + 0.20 * CASE WHEN v_has_code THEN 1.0 ELSE 0 END
      + 0.10 * CASE WHEN v_has_calib THEN 1.0 ELSE 0 END;

    RETURN ROUND(v_score, 3);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION compute_quality_score IS
    'Calcule le quality_score PIPOnto d un modèle. '
    'Formule : 0.40×citations_normalisées + 0.30×validation_empirique '
    '         + 0.20×code_disponible + 0.10×données_calibration';


-- Recherche de modèles candidats depuis le NLP (disease + population + pays)
CREATE OR REPLACE FUNCTION search_models_nlp(
    p_disease_name  VARCHAR,      -- "COVID-19" ou "covid"
    p_country_code  CHAR(2),      -- "FR", "SN", NULL
    p_population    population_type,  -- 'SCHOOL', 'ELDERLY'... ou NULL
    p_limit         INTEGER DEFAULT 10
)
RETURNS TABLE (
    model_id        VARCHAR,
    name            VARCHAR,
    formalism       TEXT,
    quality_score   NUMERIC,
    relevance_score NUMERIC,
    disease_en      VARCHAR,
    country_codes   CHAR(2)[],
    has_code        BOOLEAN,
    doi             VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.model_id,
        m.name,
        m.formalism::TEXT,
        m.quality_score,
        (
            m.quality_score
            -- Bonus géographie : +0.05 si pays correspond
            + CASE WHEN p_country_code IS NOT NULL AND EXISTS (
                SELECT 1 FROM geographic_scopes gs
                WHERE gs.model_id = m.model_id
                  AND gs.country_code = p_country_code
              ) THEN 0.05 ELSE 0 END
            -- Bonus population : +0.10 si population correspond
            + CASE WHEN p_population IS NOT NULL
                   AND m.primary_population = p_population
                   THEN 0.10 ELSE 0 END
        )::NUMERIC(4,3)                 AS relevance_score,
        d.name_en,
        ARRAY_AGG(DISTINCT gs.country_code)
            FILTER (WHERE gs.country_code IS NOT NULL),
        m.has_code,
        r.doi
    FROM models m
    JOIN diseases d ON m.disease_id = d.disease_id
    LEFT JOIN geographic_scopes gs ON gs.model_id = m.model_id
    LEFT JOIN model_references r ON m.reference_id = r.reference_id
    WHERE
        m.validation_status = 'VALIDATED'
        AND (
            d.name_en ILIKE '%' || p_disease_name || '%'
            OR d.name_fr ILIKE '%' || p_disease_name || '%'
            OR EXISTS (
                SELECT 1 FROM keywords k
                WHERE k.model_id = m.model_id
                  AND k.keyword ILIKE '%' || p_disease_name || '%'
            )
        )
    GROUP BY m.model_id, m.name, m.formalism, m.quality_score,
             m.primary_population, m.has_code, d.name_en, r.doi
    ORDER BY relevance_score DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION search_models_nlp IS
    'Fonction de recherche NLP → modèles candidats. '
    'Appelée par l API REST endpoint POST /api/search. '
    'Paramètres : maladie (texte libre), code pays ISO, type de population.';


-- =============================================================================
-- DONNÉES INITIALES — Référentiel maladies OMS prioritaires
-- =============================================================================

INSERT INTO diseases
    (name_fr, name_en, icd10_code, pathogen_type, pathogen_name,
     transmission_route, is_zoonotic, has_vector, vaccine_available,
     who_priority, endemic_regions, uri_m8)
VALUES
-- Maladies respiratoires
('COVID-19', 'COVID-19',
 'U07.1', 'Virus', 'SARS-CoV-2',
 'AIRBORNE', FALSE, FALSE, TRUE, TRUE,
 ARRAY['Global'], 'http://www.pacadi.org/these/piponto/module8#COVID19'),

('Grippe saisonnière', 'Seasonal Influenza',
 'J11', 'Virus', 'Influenza A/B',
 'AIRBORNE', TRUE, FALSE, TRUE, TRUE,
 ARRAY['Global'], 'http://www.pacadi.org/these/piponto/module8#SeasonalInfluenza'),

('Grippe pandémique', 'Pandemic Influenza',
 'J09', 'Virus', 'Influenza A (H1N1, H5N1...)',
 'AIRBORNE', TRUE, FALSE, FALSE, TRUE,
 ARRAY['Global'], NULL),

('Tuberculose', 'Tuberculosis',
 'A15', 'Bacteria', 'Mycobacterium tuberculosis',
 'AIRBORNE', FALSE, FALSE, TRUE, TRUE,
 ARRAY['Global', 'Sub-Saharan Africa', 'South-East Asia'], NULL),

('Rougeole', 'Measles',
 'B05', 'Virus', 'Measles morbillivirus',
 'AIRBORNE', FALSE, FALSE, TRUE, TRUE,
 ARRAY['Global'], 'http://www.pacadi.org/these/piponto/module8#Measles'),

-- Maladies vectorielles
('Paludisme', 'Malaria',
 'B50', 'Parasite', 'Plasmodium falciparum / vivax',
 'VECTOR_BORNE', FALSE, TRUE, FALSE, TRUE,
 ARRAY['Sub-Saharan Africa', 'South-East Asia', 'Latin America'],
 'http://www.pacadi.org/these/piponto/module8#Malaria'),

('Dengue', 'Dengue fever',
 'A90', 'Virus', 'Dengue virus (DENV 1-4)',
 'VECTOR_BORNE', FALSE, TRUE, FALSE, TRUE,
 ARRAY['Tropical regions', 'Sub-Saharan Africa', 'Latin America'],
 'http://www.pacadi.org/these/piponto/module8#Dengue'),

('Chikungunya', 'Chikungunya',
 'A92.0', 'Virus', 'Chikungunya virus',
 'VECTOR_BORNE', FALSE, TRUE, FALSE, FALSE,
 ARRAY['Africa', 'South-East Asia', 'Indian Ocean'], NULL),

('Zika', 'Zika virus disease',
 'A92.5', 'Virus', 'Zika virus',
 'VECTOR_BORNE', FALSE, TRUE, FALSE, TRUE,
 ARRAY['Latin America', 'Pacific', 'Africa'], NULL),

-- Maladies entériques
('Choléra', 'Cholera',
 'A00', 'Bacteria', 'Vibrio cholerae',
 'WATERBORNE', FALSE, FALSE, FALSE, TRUE,
 ARRAY['Sub-Saharan Africa', 'South Asia', 'Haiti'], NULL),

('Typhoïde', 'Typhoid fever',
 'A01.0', 'Bacteria', 'Salmonella typhi',
 'FECAL_ORAL', FALSE, FALSE, TRUE, FALSE,
 ARRAY['South Asia', 'Sub-Saharan Africa', 'Latin America'], NULL),

-- Maladies à transmission directe
('Maladie à virus Ebola', 'Ebola virus disease',
 'A98.4', 'Virus', 'Ebola virus',
 'CONTACT_DIRECT', TRUE, FALSE, FALSE, TRUE,
 ARRAY['Central Africa', 'West Africa'],
 'http://www.pacadi.org/these/piponto/module8#EbolaDiseaseInstance'),

('VIH/SIDA', 'HIV/AIDS',
 'B20', 'Virus', 'HIV-1 / HIV-2',
 'SEXUAL', FALSE, FALSE, FALSE, TRUE,
 ARRAY['Global', 'Sub-Saharan Africa'], NULL),

('Hépatite B', 'Hepatitis B',
 'B16', 'Virus', 'Hepatitis B virus (HBV)',
 'BLOODBORNE', FALSE, FALSE, TRUE, TRUE,
 ARRAY['Global', 'Sub-Saharan Africa', 'Asia-Pacific'], NULL),

-- Maladies méningées
('Méningite à méningocoque', 'Meningococcal meningitis',
 'A39', 'Bacteria', 'Neisseria meningitidis',
 'DROPLET', FALSE, FALSE, TRUE, TRUE,
 ARRAY['Sub-Saharan Africa (Meningitis Belt)'], NULL),

-- Maladies neglected tropical
('Schistosomiase', 'Schistosomiasis',
 'B65', 'Parasite', 'Schistosoma',
 'CONTACT_INDIRECT', FALSE, FALSE, FALSE, FALSE,
 ARRAY['Sub-Saharan Africa', 'Nile Valley', 'Brazil'], NULL),

('Leishmaniose', 'Leishmaniasis',
 'B55', 'Parasite', 'Leishmania',
 'VECTOR_BORNE', FALSE, TRUE, FALSE, FALSE,
 ARRAY['Mediterranean', 'East Africa', 'South Asia', 'Latin America'], NULL),

('Trypanosomiase africaine', 'African trypanosomiasis (Sleeping sickness)',
 'B56', 'Parasite', 'Trypanosoma brucei',
 'VECTOR_BORNE', TRUE, TRUE, FALSE, FALSE,
 ARRAY['Sub-Saharan Africa'], NULL),

-- Maladies à prévention vaccinale
('Coqueluche', 'Whooping cough (Pertussis)',
 'A37', 'Bacteria', 'Bordetella pertussis',
 'AIRBORNE', FALSE, FALSE, TRUE, FALSE,
 ARRAY['Global'], NULL),

('Poliomyélite', 'Poliomyelitis',
 'A80', 'Virus', 'Poliovirus',
 'FECAL_ORAL', FALSE, FALSE, TRUE, TRUE,
 ARRAY['Global (eradication ongoing)'], NULL),

('Varicelle', 'Chickenpox (Varicella)',
 'B01', 'Virus', 'Varicella-zoster virus (VZV)',
 'AIRBORNE', FALSE, FALSE, TRUE, FALSE,
 ARRAY['Global'], NULL),

('Oreillons', 'Mumps',
 'B26', 'Virus', 'Mumps virus',
 'DROPLET', FALSE, FALSE, TRUE, FALSE,
 ARRAY['Global'], NULL),

('Rubéole', 'Rubella',
 'B06', 'Virus', 'Rubella virus',
 'DROPLET', FALSE, FALSE, TRUE, FALSE,
 ARRAY['Global'], NULL),

-- MPOX (émergente)
('Mpox (variole du singe)', 'Mpox (Monkeypox)',
 'B04', 'Virus', 'Monkeypox virus',
 'CONTACT_DIRECT', TRUE, FALSE, FALSE, TRUE,
 ARRAY['Central Africa', 'West Africa', 'Global (2022 outbreak)'], NULL);


-- =============================================================================
-- TRIGGER : mise à jour automatique de updated_at
-- =============================================================================

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at_models
    BEFORE UPDATE ON models
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_references
    BEFORE UPDATE ON model_references
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_diseases
    BEFORE UPDATE ON diseases
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- =============================================================================
-- COMMENTAIRE GLOBAL
-- =============================================================================

COMMENT ON SCHEMA piponto IS
    'PIPOnto — Plateforme Intégrée de Simulation Épidémiologique. '
    'Bibliothèque de modèles épidémiologiques : 500+ modèles de la littérature. '
    'Version 1.0 — Thèse de doctorat, Université de Thiès / Montréal.';
