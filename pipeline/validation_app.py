"""
pipeline/validation_app.py
===========================
Interface web de validation manuelle des modèles PIPOnto.

Un seul fichier — templates HTML intégrés, aucune dépendance externe.

Usage :
    cd ~/piponto/pipeline
    source ~/piponto/venv/bin/activate
    pip install flask
    python3 validation_app.py

    → Ouvrir http://localhost:5000

Raccourcis clavier sur la fiche modèle :
    V  → Valider
    R  → Rejeter
    N  → Needs Review
    →  → Article suivant
    ←  → Article précédent
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
from markupsafe import Markup

load_dotenv(Path.home() / "piponto" / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "piponto-validation-2026")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "piponto_db"),
    "user":     os.getenv("DB_USER", "piponto_user"),
    "password": os.getenv("DB_PASSWORD", "piponto2025"),
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("piponto.validation")

# ══════════════════════════════════════════════════════════════════════════════
# ENUMS (miroir du schéma PostgreSQL)
# ══════════════════════════════════════════════════════════════════════════════

FORMALISMS = ['SIR','SIS','SIRS','SEIR','SEIRS','SEIS','SEIRD','SEIRHD',
              'MSIR','MSEIR','ABM','NETWORK','METAPOPULATION','IBM',
              'STOCHASTIC_SIR','STOCHASTIC_SEIR','BRANCHING_PROCESS',
              'RENEWAL_EQUATION','BAYESIAN','MIXED','OTHER']

MODEL_TYPES    = ['DETERMINISTIC','STOCHASTIC','HYBRID']
SPATIAL_STRUCTS= ['NONE','METAPOPULATION','NETWORK','GRID','CONTINUOUS']
POPULATIONS    = ['GENERAL','SCHOOL','ELDERLY','HEALTHCARE_WORKERS','URBAN',
                  'RURAL','CHILDREN_UNDER5','IMMUNOCOMPROMISED','PREGNANT',
                  'LIVESTOCK','MIXED']
PLATFORMS      = ['PYTHON','R','MATLAB','GAMA','NETLOGO','REPAST','MESA',
                  'JULIA','C_CPP','JAVA','MATHEMATICA','OTHER']
PARAM_TYPES    = ['TRANSMISSION_RATE','RECOVERY_RATE','INCUBATION_RATE',
                  'WANING_IMMUNITY_RATE','MORTALITY_RATE','BIRTH_RATE',
                  'NATURAL_DEATH_RATE','VACCINATION_RATE','HOSPITALIZATION_RATE',
                  'R0','SERIAL_INTERVAL','GENERATION_TIME','CASE_FATALITY_RATE',
                  'CONTACT_RATE','VECTOR_BITING_RATE','VECTOR_COMPETENCE','OTHER']

# ══════════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

def get_conn():
    import psycopg2
    import psycopg2.extras
    return psycopg2.connect(**DB_CONFIG)


def get_stats():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE validation_status='PENDING')   AS pending,
                    COUNT(*) FILTER (WHERE validation_status='VALIDATED')  AS validated,
                    COUNT(*) FILTER (WHERE validation_status='REJECTED')   AS rejected,
                    COUNT(*) FILTER (WHERE validation_status='NEEDS_REVIEW') AS needs_review,
                    COUNT(*) AS total
                FROM piponto.models
            """)
            row = cur.fetchone()
            return {
                "pending":      row[0],
                "validated":    row[1],
                "rejected":     row[2],
                "needs_review": row[3],
                "total":        row[4],
                "progress":     round((row[1] + row[2]) / max(row[4], 1) * 100),
            }


def get_model_list(status="PENDING", page=1, per_page=30, search=""):
    offset = (page - 1) * per_page
    with get_conn() as conn:
        with conn.cursor() as cur:
            where_parts = ["m.validation_status = %s"]
            params = [status]
            if search:
                where_parts.append("(m.name ILIKE %s OR m.model_id ILIKE %s)")
                params += [f"%{search}%", f"%{search}%"]
            where = " AND ".join(where_parts)

            cur.execute(f"""
                SELECT m.model_id, m.name, m.formalism, m.model_type,
                       m.extraction_confidence, m.has_code,
                       d.name_fr AS disease,
                       m.created_at,
                       COUNT(p.param_id) AS param_count,
                       COUNT(g.scope_id) AS geo_count
                FROM piponto.models m
                LEFT JOIN piponto.diseases d ON d.disease_id = m.disease_id
                LEFT JOIN piponto.parameters p ON p.model_id = m.model_id
                LEFT JOIN piponto.geographic_scopes g ON g.model_id = m.model_id
                WHERE {where}
                GROUP BY m.model_id, m.name, m.formalism, m.model_type,
                         m.extraction_confidence, m.has_code, d.name_fr, m.created_at
                ORDER BY m.extraction_confidence DESC, m.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])
            models = cur.fetchall()

            cur.execute(f"SELECT COUNT(*) FROM piponto.models m WHERE {where}",
                        params)
            total = cur.fetchone()[0]

    return models, total


def get_model_detail(model_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Modèle principal
            cur.execute("""
                SELECT m.*, d.name_fr AS disease_name, d.name_en AS disease_name_en,
                       r.title AS ref_title, r.authors AS ref_authors,
                       r.journal AS ref_journal, r.year AS ref_year,
                       r.doi AS ref_doi, r.abstract AS ref_abstract,
                       r.pubmed_id AS ref_pmid
                FROM piponto.models m
                LEFT JOIN piponto.diseases d ON d.disease_id = m.disease_id
                LEFT JOIN piponto.model_references r ON r.reference_id = m.reference_id
                WHERE m.model_id = %s
            """, (model_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in cur.description]
            model = dict(zip(cols, row))

            # Paramètres
            cur.execute("""
                SELECT param_id, param_type, symbol, default_value,
                       min_value, max_value, unit, time_unit,
                       is_estimated, notes
                FROM piponto.parameters
                WHERE model_id = %s
                ORDER BY param_id
            """, (model_id,))
            model["params"] = [dict(zip([d[0] for d in cur.description], r))
                               for r in cur.fetchall()]

            # Compartiments
            cur.execute("""
                SELECT compartment_id, symbol, name_en, is_infectious,
                       is_recovered, is_dead, ode_equation
                FROM piponto.compartments
                WHERE model_id = %s ORDER BY ordering
            """, (model_id,))
            model["compartments"] = [dict(zip([d[0] for d in cur.description], r))
                                      for r in cur.fetchall()]

            # Géographies
            cur.execute("""
                SELECT scope_id, country_code, country_name, scope_level,
                       population_size, is_primary_scope
                FROM piponto.geographic_scopes
                WHERE model_id = %s ORDER BY is_primary_scope DESC
            """, (model_id,))
            model["geos"] = [dict(zip([d[0] for d in cur.description], r))
                             for r in cur.fetchall()]

    return model


def get_adjacent_models(model_id, status="PENDING"):
    """Retourne (prev_id, next_id) pour la navigation."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_id FROM piponto.models
                WHERE validation_status = %s
                ORDER BY extraction_confidence DESC, created_at DESC
            """, (status,))
            ids = [r[0] for r in cur.fetchall()]

    if model_id not in ids:
        ids_all = ids
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT model_id FROM piponto.models ORDER BY created_at DESC")
                ids_all = [r[0] for r in cur.fetchall()]
        try:
            idx = ids_all.index(model_id)
        except ValueError:
            return None, None
        prev_id = ids_all[idx - 1] if idx > 0 else None
        next_id = ids_all[idx + 1] if idx < len(ids_all) - 1 else None
        return prev_id, next_id

    try:
        idx = ids.index(model_id)
    except ValueError:
        return None, None
    prev_id = ids[idx - 1] if idx > 0 else None
    next_id = ids[idx + 1] if idx < len(ids) - 1 else None
    return prev_id, next_id


def update_model(model_id, data):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE piponto.models SET
                    name               = %s,
                    formalism          = %s,
                    model_type         = %s,
                    spatial_structure  = %s,
                    is_age_structured  = %s,
                    is_multi_strain    = %s,
                    has_interventions  = %s,
                    platform           = %s,
                    has_code           = %s,
                    implementation_url = %s,
                    primary_population = %s,
                    is_empirically_validated = %s,
                    validation_status  = %s,
                    validated_by       = %s,
                    validated_at       = NOW(),
                    rejection_reason   = %s,
                    updated_at         = NOW()
                WHERE model_id = %s
            """, (
                data.get("name"),
                data.get("formalism"),
                data.get("model_type"),
                data.get("spatial_structure"),
                data.get("is_age_structured") == "on",
                data.get("is_multi_strain") == "on",
                data.get("has_interventions") == "on",
                data.get("platform"),
                data.get("has_code") == "on",
                data.get("implementation_url") or None,
                data.get("primary_population"),
                data.get("is_empirically_validated") == "on",
                data.get("action", "VALIDATED").upper(),
                "researcher",
                data.get("rejection_reason") or None,
                model_id,
            ))
        conn.commit()


def update_param(param_id, data):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE piponto.parameters SET
                    default_value = %s,
                    min_value     = %s,
                    max_value     = %s,
                    param_type    = %s,
                    unit          = %s,
                    notes         = %s
                WHERE param_id = %s
            """, (
                float(data["value"]) if data.get("value") else None,
                float(data["min"]) if data.get("min") else None,
                float(data["max"]) if data.get("max") else None,
                data.get("param_type"),
                data.get("unit"),
                data.get("notes"),
                param_id,
            ))
        conn.commit()


def add_param(model_id, data):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO piponto.parameters
                    (model_id, param_type, symbol, default_value, unit, time_unit, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                model_id,
                data.get("param_type", "OTHER"),
                data.get("symbol", "?"),
                float(data["value"]) if data.get("value") else 0,
                data.get("unit", ""),
                data.get("time_unit", "day"),
                data.get("notes", ""),
            ))
        conn.commit()


def delete_param(param_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM piponto.parameters WHERE param_id = %s",
                        (param_id,))
        conn.commit()

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES HTML
# ══════════════════════════════════════════════════════════════════════════════

BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #0d0f14;
  --surface:  #141720;
  --surface2: #1c2030;
  --border:   #2a2f42;
  --accent:   #e8b84b;
  --accent2:  #4be8a0;
  --danger:   #e84b6a;
  --warn:     #e8944b;
  --text:     #e8eaf2;
  --muted:    #6b7080;
  --font-ui:  'Syne', sans-serif;
  --font-mono:'Space Mono', monospace;
}

html { font-size: 14px; scroll-behavior: smooth; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-ui);
  min-height: 100vh;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── Layout ── */
.layout { display: flex; min-height: 100vh; }

.sidebar {
  width: 220px; min-width: 220px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 1.5rem 1rem;
  display: flex; flex-direction: column; gap: 0.5rem;
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
}

.sidebar .logo {
  font-size: 1.1rem; font-weight: 800;
  color: var(--accent); letter-spacing: 0.05em;
  margin-bottom: 1.5rem;
  display: flex; align-items: center; gap: 0.5rem;
}
.sidebar .logo span { color: var(--text); font-weight: 400; }

.nav-link {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.5rem 0.75rem; border-radius: 6px;
  color: var(--muted); font-size: 0.85rem; font-weight: 600;
  transition: all 0.15s;
  cursor: pointer;
}
.nav-link:hover, .nav-link.active {
  background: var(--surface2); color: var(--text);
  text-decoration: none;
}
.nav-link .dot {
  width: 8px; height: 8px; border-radius: 50%;
  flex-shrink: 0;
}
.dot-pending  { background: var(--warn); }
.dot-validated{ background: var(--accent2); }
.dot-rejected { background: var(--danger); }
.dot-review   { background: var(--accent); }

.sidebar-section {
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.12em;
  color: var(--muted); text-transform: uppercase;
  margin: 1rem 0 0.4rem 0.75rem;
}

/* Stats bar */
.stats-bar {
  margin-top: auto;
  padding: 1rem 0.75rem;
  background: var(--surface2);
  border-radius: 8px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
}
.stats-bar .stat { display: flex; justify-content: space-between; margin-bottom: 0.3rem; }
.stats-bar .stat .val { color: var(--accent); font-weight: 700; }
.progress-bar {
  height: 4px; background: var(--border); border-radius: 2px;
  margin-top: 0.75rem; overflow: hidden;
}
.progress-fill {
  height: 100%; background: var(--accent2); border-radius: 2px;
  transition: width 0.5s ease;
}

/* ── Main ── */
.main { flex: 1; padding: 2rem; overflow-y: auto; }

.page-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 1.5rem;
}
.page-title {
  font-size: 1.5rem; font-weight: 800;
  letter-spacing: -0.02em;
}
.page-title span { color: var(--accent); }

/* ── Cards / Table ── */
.model-table { width: 100%; border-collapse: collapse; }
.model-table th {
  text-align: left; font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted); padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
}
.model-table td {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.model-table tr:hover td { background: var(--surface); cursor: pointer; }

.tag {
  display: inline-flex; align-items: center;
  padding: 0.15rem 0.5rem;
  border-radius: 4px; font-size: 0.7rem; font-weight: 700;
  font-family: var(--font-mono); letter-spacing: 0.03em;
}
.tag-seir    { background: rgba(232,184,75,0.15); color: var(--accent); }
.tag-abm     { background: rgba(75,232,160,0.15); color: var(--accent2); }
.tag-other   { background: rgba(107,112,128,0.2); color: var(--muted); }
.tag-det     { background: rgba(100,120,200,0.2); color: #90a0e0; }
.tag-stoch   { background: rgba(232,148,75,0.15); color: var(--warn); }

.conf-bar {
  height: 6px; background: var(--border); border-radius: 3px;
  width: 60px; overflow: hidden; display: inline-block;
}
.conf-fill { height: 100%; border-radius: 3px; }
.conf-high  { background: var(--accent2); }
.conf-med   { background: var(--accent); }
.conf-low   { background: var(--danger); }

/* ── Search ── */
.search-bar {
  display: flex; gap: 0.75rem; align-items: center;
  margin-bottom: 1.5rem;
}
.search-input {
  flex: 1; max-width: 400px;
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text); font-family: var(--font-ui); font-size: 0.85rem;
  padding: 0.5rem 0.75rem; border-radius: 6px;
  outline: none; transition: border-color 0.15s;
}
.search-input:focus { border-color: var(--accent); }

/* ── Buttons ── */
.btn {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.45rem 0.9rem; border-radius: 6px;
  font-family: var(--font-ui); font-size: 0.8rem; font-weight: 700;
  cursor: pointer; border: none; transition: all 0.15s;
  letter-spacing: 0.02em;
}
.btn-validate { background: var(--accent2); color: #0d150f; }
.btn-validate:hover { filter: brightness(1.1); }
.btn-reject   { background: var(--danger); color: #fff; }
.btn-reject:hover   { filter: brightness(1.1); }
.btn-review   { background: var(--accent); color: #1a1100; }
.btn-review:hover   { filter: brightness(1.1); }
.btn-save     { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
.btn-save:hover { border-color: var(--accent); color: var(--accent); }
.btn-ghost    { background: transparent; color: var(--muted); border: 1px solid var(--border); }
.btn-ghost:hover { color: var(--text); border-color: var(--text); }
.btn-danger   { background: transparent; color: var(--danger); border: 1px solid var(--danger); }
.btn-danger:hover { background: var(--danger); color: #fff; }
.btn-sm { padding: 0.3rem 0.6rem; font-size: 0.72rem; }

/* ── Detail page ── */
.detail-grid {
  display: grid; grid-template-columns: 1fr 380px; gap: 1.5rem;
  align-items: start;
}

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.25rem;
  margin-bottom: 1rem;
}
.card-title {
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--muted);
  margin-bottom: 1rem; padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}

/* Form fields */
.field { margin-bottom: 0.85rem; }
.field label {
  display: block; font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 0.3rem;
}
.field input, .field select, .field textarea {
  width: 100%; background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text); font-family: var(--font-ui); font-size: 0.85rem;
  padding: 0.45rem 0.65rem; border-radius: 6px;
  outline: none; transition: border-color 0.15s;
}
.field input:focus, .field select:focus, .field textarea:focus {
  border-color: var(--accent);
}
.field textarea { resize: vertical; min-height: 70px; }
.field select option { background: var(--surface2); }

.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.field-row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.75rem; }

/* Checkbox */
.check-field {
  display: flex; align-items: center; gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.check-field input[type=checkbox] {
  width: 16px; height: 16px; accent-color: var(--accent);
}
.check-field label {
  font-size: 0.82rem; color: var(--text); cursor: pointer;
  text-transform: none; letter-spacing: 0;
}

/* Params table */
.params-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.params-table th {
  text-align: left; font-size: 0.68rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--border);
}
.params-table td { padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); }
.params-table input {
  background: transparent; border: 1px solid transparent;
  color: var(--text); font-family: var(--font-mono); font-size: 0.8rem;
  padding: 0.2rem 0.4rem; border-radius: 4px; width: 100%;
  outline: none;
}
.params-table input:focus { border-color: var(--accent); background: var(--surface2); }
.symbol { font-family: var(--font-mono); color: var(--accent); font-weight: 700; }

/* Abstract */
.abstract-box {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem;
  font-size: 0.82rem; line-height: 1.6; color: var(--muted);
  max-height: 200px; overflow-y: auto;
}

/* Navigation */
.nav-arrows {
  display: flex; gap: 0.5rem; align-items: center;
}
.nav-counter {
  font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted);
}

/* Status badges */
.badge {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.2rem 0.6rem; border-radius: 20px;
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em;
}
.badge-pending   { background: rgba(232,148,75,0.15); color: var(--warn); }
.badge-validated { background: rgba(75,232,160,0.15); color: var(--accent2); }
.badge-rejected  { background: rgba(232,75,106,0.15); color: var(--danger); }
.badge-review    { background: rgba(232,184,75,0.15); color: var(--accent); }

/* Keyboard hint */
.kbd {
  display: inline-block; background: var(--surface2);
  border: 1px solid var(--border); border-radius: 4px;
  padding: 0.1rem 0.35rem; font-family: var(--font-mono);
  font-size: 0.65rem; color: var(--muted);
}

/* Toast */
.toast {
  position: fixed; bottom: 1.5rem; right: 1.5rem;
  background: var(--surface); border: 1px solid var(--accent2);
  border-radius: 8px; padding: 0.75rem 1.25rem;
  font-size: 0.82rem; color: var(--accent2);
  transform: translateY(100px); opacity: 0;
  transition: all 0.3s; z-index: 1000;
}
.toast.show { transform: translateY(0); opacity: 1; }

/* Pagination */
.pagination {
  display: flex; gap: 0.4rem; align-items: center;
  justify-content: center; margin-top: 1.5rem;
}
.page-btn {
  padding: 0.3rem 0.65rem; border-radius: 5px;
  background: var(--surface); border: 1px solid var(--border);
  color: var(--muted); font-size: 0.8rem; cursor: pointer;
  transition: all 0.15s;
}
.page-btn:hover, .page-btn.current {
  background: var(--accent); color: #1a1100; border-color: var(--accent);
}

/* Compartments */
.comp-list { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.comp-pill {
  padding: 0.25rem 0.6rem; border-radius: 20px;
  font-family: var(--font-mono); font-weight: 700; font-size: 0.8rem;
  border: 1px solid;
}
.comp-S { border-color: #4b9ee8; color: #4b9ee8; background: rgba(75,158,232,0.1); }
.comp-E { border-color: var(--accent); color: var(--accent); background: rgba(232,184,75,0.1); }
.comp-I { border-color: var(--danger); color: var(--danger); background: rgba(232,75,106,0.1); }
.comp-R { border-color: var(--accent2); color: var(--accent2); background: rgba(75,232,160,0.1); }
.comp-D { border-color: var(--muted); color: var(--muted); background: rgba(107,112,128,0.1); }
.comp-V { border-color: #a84be8; color: #a84be8; background: rgba(168,75,232,0.1); }
.comp-H { border-color: var(--warn); color: var(--warn); background: rgba(232,148,75,0.1); }
.comp-default { border-color: var(--border); color: var(--muted); background: var(--surface2); }

/* Flash / alerts */
.alert {
  padding: 0.75rem 1rem; border-radius: 6px;
  font-size: 0.82rem; margin-bottom: 1rem;
  border-left: 3px solid;
}
.alert-success { background: rgba(75,232,160,0.1); border-color: var(--accent2); color: var(--accent2); }
.alert-error   { background: rgba(232,75,106,0.1); border-color: var(--danger);  color: var(--danger); }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted); }
"""

SIDEBAR_HTML = """
<div class="sidebar">
  <div class="logo">🧬 <span>PIPOnto</span></div>

  <div class="sidebar-section">Navigation</div>
  <a class="nav-link {{ 'active' if active=='list' }}" href="{{ url_for('index') }}">
    <span class="dot dot-pending"></span> À valider
    <span style="margin-left:auto;font-family:var(--font-mono);font-size:0.7rem;color:var(--warn)">
      {{ stats.pending }}
    </span>
  </a>
  <a class="nav-link {{ 'active' if active=='validated' }}"
     href="{{ url_for('index', status='VALIDATED') }}">
    <span class="dot dot-validated"></span> Validés
    <span style="margin-left:auto;font-family:var(--font-mono);font-size:0.7rem;color:var(--accent2)">
      {{ stats.validated }}
    </span>
  </a>
  <a class="nav-link {{ 'active' if active=='rejected' }}"
     href="{{ url_for('index', status='REJECTED') }}">
    <span class="dot dot-rejected"></span> Rejetés
    <span style="margin-left:auto;font-family:var(--font-mono);font-size:0.7rem;color:var(--danger)">
      {{ stats.rejected }}
    </span>
  </a>
  <a class="nav-link {{ 'active' if active=='review' }}"
     href="{{ url_for('index', status='NEEDS_REVIEW') }}">
    <span class="dot dot-review"></span> À revoir
    <span style="margin-left:auto;font-family:var(--font-mono);font-size:0.7rem;color:var(--accent)">
      {{ stats.needs_review }}
    </span>
  </a>

  <div class="stats-bar">
    <div class="stat"><span>Progression</span><span class="val">{{ stats.progress }}%</span></div>
    <div class="stat"><span>Total</span><span class="val">{{ stats.total }}</span></div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{{ stats.progress }}%"></div>
    </div>
  </div>
</div>
"""

INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>PIPOnto — Validation</title>
  <style>{{ css }}</style>
</head>
<body>
<div class="layout">
  {{ sidebar }}

  <main class="main">
    <div class="page-header">
      <div class="page-title">
        Bibliothèque <span>{{ status_label }}</span>
      </div>
      <div style="font-family:var(--font-mono);font-size:0.75rem;color:var(--muted)">
        {{ total }} modèles
      </div>
    </div>

    <div class="search-bar">
      <input class="search-input" type="text" id="search"
             placeholder="Rechercher par nom, identifiant..."
             value="{{ search }}"
             onkeyup="if(event.key==='Enter')doSearch()">
      <button class="btn btn-ghost btn-sm" onclick="doSearch()">🔍 Chercher</button>
      {% if search %}
      <a class="btn btn-ghost btn-sm" href="{{ url_for('index', status=status) }}">✕ Effacer</a>
      {% endif %}
    </div>

    <table class="model-table">
      <thead>
        <tr>
          <th>Identifiant</th>
          <th>Formalisme</th>
          <th>Type</th>
          <th>Maladie</th>
          <th>Params</th>
          <th>Géo</th>
          <th>Code</th>
          <th>Confiance</th>
        </tr>
      </thead>
      <tbody>
        {% for m in models %}
        <tr onclick="window.location='{{ url_for('model_detail', model_id=m[0]) }}'">
          <td>
            <div style="font-family:var(--font-mono);font-size:0.78rem;color:var(--accent)">
              {{ m[0] }}
            </div>
            <div style="font-size:0.72rem;color:var(--muted);margin-top:0.1rem">
              {{ (m[1] or '')[:60] }}{% if (m[1] or '')|length > 60 %}…{% endif %}
            </div>
          </td>
          <td>
            <span class="tag {% if m[2] in ['SEIR','SIR','SEIRD','SEIRS','SIS','SIRS'] %}tag-seir
                              {% elif m[2] == 'ABM' %}tag-abm
                              {% else %}tag-other{% endif %}">
              {{ m[2] }}
            </span>
          </td>
          <td>
            <span class="tag {% if m[3] == 'DETERMINISTIC' %}tag-det
                              {% elif m[3] == 'STOCHASTIC' %}tag-stoch
                              {% else %}tag-other{% endif %}">
              {{ (m[3] or 'OTHER')[:3] }}
            </span>
          </td>
          <td style="font-size:0.8rem;color:var(--muted)">{{ m[6] or '—' }}</td>
          <td style="font-family:var(--font-mono);font-size:0.78rem;
                     color:{% if m[8] > 0 %}var(--accent2){% else %}var(--muted){% endif %}">
            {{ m[8] }}
          </td>
          <td style="font-family:var(--font-mono);font-size:0.78rem;
                     color:{% if m[9] > 0 %}var(--accent){% else %}var(--muted){% endif %}">
            {{ m[9] }}
          </td>
          <td style="font-size:0.8rem">
            {% if m[5] %}✅{% else %}<span style="color:var(--muted)">—</span>{% endif %}
          </td>
          <td>
            {% set conf = (m[4] or 0) %}
            <div style="display:flex;align-items:center;gap:0.4rem">
              <div class="conf-bar">
                <div class="conf-fill {% if conf >= 0.7 %}conf-high{% elif conf >= 0.4 %}conf-med{% else %}conf-low{% endif %}"
                     style="width:{{ (conf * 100)|int }}%"></div>
              </div>
              <span style="font-family:var(--font-mono);font-size:0.68rem;color:var(--muted)">
                {{ "%.2f"|format(conf) }}
              </span>
            </div>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="8" style="text-align:center;color:var(--muted);padding:2rem">
          Aucun modèle {{ status_label.lower() }}
        </td></tr>
        {% endfor %}
      </tbody>
    </table>

    <!-- Pagination -->
    {% if total_pages > 1 %}
    <div class="pagination">
      {% if page > 1 %}
      <a href="{{ url_for('index', status=status, page=page-1, search=search) }}"
         class="page-btn">←</a>
      {% endif %}
      {% for p in range(1, total_pages+1) %}
        {% if p == page or (p >= page-2 and p <= page+2) or p == 1 or p == total_pages %}
          <a href="{{ url_for('index', status=status, page=p, search=search) }}"
             class="page-btn {{ 'current' if p==page }}">{{ p }}</a>
        {% elif p == page-3 or p == page+3 %}
          <span style="color:var(--muted)">…</span>
        {% endif %}
      {% endfor %}
      {% if page < total_pages %}
      <a href="{{ url_for('index', status=status, page=page+1, search=search) }}"
         class="page-btn">→</a>
      {% endif %}
    </div>
    {% endif %}
  </main>
</div>

<script>
function doSearch() {
  const q = document.getElementById('search').value;
  window.location = `{{ url_for('index') }}?status={{ status }}&search=${encodeURIComponent(q)}`;
}
</script>
</body>
</html>
"""

DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>{{ m.model_id }} — PIPOnto</title>
  <style>{{ css }}</style>
</head>
<body>
<div class="layout">
  {{ sidebar }}

  <main class="main">
    <!-- Header -->
    <div class="page-header" style="margin-bottom:1rem">
      <div>
        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.4rem">
          <a href="{{ url_for('index') }}" style="color:var(--muted);font-size:0.8rem">
            ← Retour
          </a>
          <span class="badge badge-{{ m.validation_status.lower() }}">
            {{ m.validation_status }}
          </span>
          <span style="font-size:0.75rem;color:var(--muted)">
            confiance : <span style="font-family:var(--font-mono);color:var(--accent)">
              {{ "%.2f"|format(m.extraction_confidence or 0) }}
            </span>
          </span>
        </div>
        <div class="page-title" style="font-size:1.2rem">
          <span>{{ m.model_id }}</span>
        </div>
      </div>

      <!-- Navigation -->
      <div class="nav-arrows">
        {% if prev_id %}
        <a href="{{ url_for('model_detail', model_id=prev_id) }}"
           class="btn btn-ghost btn-sm" title="Précédent (←)">← Préc.</a>
        {% endif %}
        <span class="nav-counter">{{ position }} / {{ total_pending }}</span>
        {% if next_id %}
        <a href="{{ url_for('model_detail', model_id=next_id) }}"
           class="btn btn-ghost btn-sm" title="Suivant (→)">Suiv. →</a>
        {% endif %}
      </div>
    </div>

    <!-- Alert flash -->
    {% if flash_msg %}
    <div class="alert alert-success">{{ flash_msg }}</div>
    {% endif %}

    <!-- Raccourcis -->
    <div style="margin-bottom:1.2rem;font-size:0.75rem;color:var(--muted)">
      Raccourcis :
      <span class="kbd">V</span> Valider &nbsp;
      <span class="kbd">R</span> Rejeter &nbsp;
      <span class="kbd">N</span> Needs review &nbsp;
      <span class="kbd">→</span> Suivant &nbsp;
      <span class="kbd">←</span> Précédent
    </div>

    <form method="POST" action="{{ url_for('model_save', model_id=m.model_id) }}"
          id="mainForm">

    <div class="detail-grid">
      <!-- Colonne gauche -->
      <div>

        <!-- Identité du modèle -->
        <div class="card">
          <div class="card-title">Identité du modèle</div>

          <div class="field">
            <label>Nom complet</label>
            <input type="text" name="name" value="{{ m.name or '' }}" required>
          </div>

          <div class="field-row">
            <div class="field">
              <label>Formalisme</label>
              <select name="formalism">
                {% for f in formalisms %}
                <option value="{{ f }}" {{ 'selected' if m.formalism==f }}>{{ f }}</option>
                {% endfor %}
              </select>
            </div>
            <div class="field">
              <label>Type</label>
              <select name="model_type">
                {% for t in model_types %}
                <option value="{{ t }}" {{ 'selected' if m.model_type==t }}>{{ t }}</option>
                {% endfor %}
              </select>
            </div>
          </div>

          <div class="field-row">
            <div class="field">
              <label>Structure spatiale</label>
              <select name="spatial_structure">
                {% for s in spatial_structs %}
                <option value="{{ s }}" {{ 'selected' if m.spatial_structure==s }}>{{ s }}</option>
                {% endfor %}
              </select>
            </div>
            <div class="field">
              <label>Population cible</label>
              <select name="primary_population">
                {% for p in populations %}
                <option value="{{ p }}" {{ 'selected' if m.primary_population==p }}>{{ p }}</option>
                {% endfor %}
              </select>
            </div>
          </div>

          <div class="field-row3">
            <div class="check-field">
              <input type="checkbox" name="is_age_structured" id="age_struct"
                     {{ 'checked' if m.is_age_structured }}>
              <label for="age_struct">Âge-structuré</label>
            </div>
            <div class="check-field">
              <input type="checkbox" name="is_multi_strain" id="multi_strain"
                     {{ 'checked' if m.is_multi_strain }}>
              <label for="multi_strain">Multi-souches</label>
            </div>
            <div class="check-field">
              <input type="checkbox" name="has_interventions" id="interventions"
                     {{ 'checked' if m.has_interventions }}>
              <label for="interventions">Interventions</label>
            </div>
          </div>

          <div class="field-row">
            <div class="field">
              <label>Plateforme</label>
              <select name="platform">
                {% for p in platforms %}
                <option value="{{ p }}" {{ 'selected' if m.platform==p }}>{{ p }}</option>
                {% endfor %}
              </select>
            </div>
            <div class="field">
              <label>URL code</label>
              <input type="text" name="implementation_url"
                     value="{{ m.implementation_url or '' }}"
                     placeholder="https://github.com/...">
            </div>
          </div>

          <div class="field-row">
            <div class="check-field">
              <input type="checkbox" name="has_code" id="has_code"
                     {{ 'checked' if m.has_code }}>
              <label for="has_code">Code disponible</label>
            </div>
            <div class="check-field">
              <input type="checkbox" name="is_empirically_validated" id="emp_valid"
                     {{ 'checked' if m.is_empirically_validated }}>
              <label for="emp_valid">Validé empiriquement</label>
            </div>
          </div>
        </div>

        <!-- Paramètres -->
        <div class="card">
          <div class="card-title">Paramètres épidémiologiques</div>

          {% if m.params %}
          <table class="params-table">
            <thead>
              <tr>
                <th>Symbole</th><th>Type</th><th>Valeur</th>
                <th>Min</th><th>Max</th><th>Unité</th><th></th>
              </tr>
            </thead>
            <tbody>
              {% for p in m.params %}
              <tr id="param-{{ p.param_id }}">
                <td><span class="symbol">{{ p.symbol }}</span></td>
                <td>
                  <select name="pt_{{ p.param_id }}" style="background:var(--surface2);
                    border:1px solid var(--border);color:var(--text);
                    font-size:0.72rem;padding:0.15rem 0.3rem;border-radius:4px">
                    {% for pt in param_types %}
                    <option value="{{ pt }}" {{ 'selected' if p.param_type==pt }}>{{ pt }}</option>
                    {% endfor %}
                  </select>
                </td>
                <td><input type="number" step="any" name="pv_{{ p.param_id }}"
                     value="{{ p.default_value }}" style="width:80px"></td>
                <td><input type="number" step="any" name="pmin_{{ p.param_id }}"
                     value="{{ p.min_value or '' }}" style="width:70px"
                     placeholder="—"></td>
                <td><input type="number" step="any" name="pmax_{{ p.param_id }}"
                     value="{{ p.max_value or '' }}" style="width:70px"
                     placeholder="—"></td>
                <td><input type="text" name="pu_{{ p.param_id }}"
                     value="{{ p.unit or '' }}" style="width:70px"
                     placeholder="day⁻¹"></td>
                <td>
                  <button type="button" class="btn btn-danger btn-sm"
                    onclick="delParam({{ p.param_id }})">✕</button>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}
          <p style="color:var(--muted);font-size:0.8rem;margin-bottom:0.75rem">
            Aucun paramètre extrait automatiquement.
          </p>
          {% endif %}

          <!-- Ajouter un paramètre -->
          <details style="margin-top:0.75rem">
            <summary style="font-size:0.78rem;color:var(--accent);cursor:pointer">
              + Ajouter un paramètre
            </summary>
            <div style="margin-top:0.75rem;display:grid;grid-template-columns:repeat(5,1fr);gap:0.5rem">
              <select id="new_pt" style="background:var(--surface2);border:1px solid var(--border);
                color:var(--text);font-size:0.78rem;padding:0.3rem;border-radius:4px">
                {% for pt in param_types %}<option>{{ pt }}</option>{% endfor %}
              </select>
              <input id="new_sym" type="text" placeholder="β" style="background:var(--surface2);
                border:1px solid var(--border);color:var(--text);font-size:0.78rem;
                padding:0.3rem;border-radius:4px">
              <input id="new_val" type="number" step="any" placeholder="0.3"
                style="background:var(--surface2);border:1px solid var(--border);
                color:var(--text);font-size:0.78rem;padding:0.3rem;border-radius:4px">
              <input id="new_unit" type="text" placeholder="day⁻¹"
                style="background:var(--surface2);border:1px solid var(--border);
                color:var(--text);font-size:0.78rem;padding:0.3rem;border-radius:4px">
              <button type="button" class="btn btn-review btn-sm"
                onclick="addParam('{{ m.model_id }}')">+ Ajouter</button>
            </div>
          </details>
        </div>

        <!-- Compartiments -->
        <div class="card">
          <div class="card-title">Compartiments</div>
          {% if m.compartments %}
          <div class="comp-list">
            {% for c in m.compartments %}
            <div class="comp-pill comp-{{ c.symbol[0] if c.symbol[0] in ['S','E','I','R','D','V','H'] else 'default' }}">
              {{ c.symbol }}
              <span style="font-size:0.65rem;margin-left:0.3rem;opacity:0.7">
                {{ c.name_en or '' }}
              </span>
            </div>
            {% endfor %}
          </div>
          {% else %}
          <span style="color:var(--muted);font-size:0.8rem">Non détectés</span>
          {% endif %}
        </div>

      </div>

      <!-- Colonne droite -->
      <div>

        <!-- Actions de validation -->
        <div class="card" style="position:sticky;top:1rem">
          <div class="card-title">Décision</div>

          <div style="display:flex;flex-direction:column;gap:0.6rem;margin-bottom:1rem">
            <button type="submit" name="action" value="VALIDATED"
                    class="btn btn-validate" style="width:100%;justify-content:center">
              ✓ Valider <span class="kbd" style="margin-left:auto">V</span>
            </button>
            <button type="submit" name="action" value="NEEDS_REVIEW"
                    class="btn btn-review" style="width:100%;justify-content:center">
              ◎ Needs review <span class="kbd" style="margin-left:auto">N</span>
            </button>
            <button type="submit" name="action" value="REJECTED"
                    class="btn btn-reject" style="width:100%;justify-content:center">
              ✕ Rejeter <span class="kbd" style="margin-left:auto">R</span>
            </button>
          </div>

          <div class="field">
            <label>Raison du rejet (si rejeté)</label>
            <textarea name="rejection_reason" rows="2"
                      placeholder="Données insuffisantes, hors sujet...">{{ m.rejection_reason or '' }}</textarea>
          </div>

          <button type="submit" name="action" value="{{ m.validation_status }}"
                  class="btn btn-save btn-sm" style="width:100%;justify-content:center">
            💾 Enregistrer sans changer le statut
          </button>
        </div>

        <!-- Référence article -->
        <div class="card">
          <div class="card-title">Article source</div>
          <div style="font-weight:700;font-size:0.85rem;margin-bottom:0.4rem;line-height:1.4">
            {{ m.ref_title or '—' }}
          </div>
          <div style="font-size:0.78rem;color:var(--muted);margin-bottom:0.5rem">
            {{ (m.ref_authors or '')[:80] }}{% if (m.ref_authors or '')|length > 80 %}…{% endif %}
            {% if m.ref_year %} ({{ m.ref_year }}){% endif %}
          </div>
          <div style="font-size:0.75rem;color:var(--muted);margin-bottom:0.75rem">
            {{ m.ref_journal or '' }}
          </div>
          {% if m.ref_doi %}
          <a href="https://doi.org/{{ m.ref_doi }}" target="_blank"
             class="btn btn-ghost btn-sm">🔗 DOI</a>
          {% endif %}
          {% if m.ref_pmid %}
          <a href="https://pubmed.ncbi.nlm.nih.gov/{{ m.ref_pmid }}" target="_blank"
             class="btn btn-ghost btn-sm">📄 PubMed</a>
          {% endif %}

          {% if m.ref_abstract %}
          <div class="abstract-box" style="margin-top:0.75rem">
            {{ m.ref_abstract }}
          </div>
          {% endif %}
        </div>

        <!-- Géographies -->
        <div class="card">
          <div class="card-title">Géographies détectées</div>
          {% if m.geos %}
          {% for g in m.geos %}
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.4rem">
            <span style="font-family:var(--font-mono);font-size:0.8rem;
                         background:var(--surface2);padding:0.15rem 0.4rem;
                         border-radius:3px;color:var(--accent)">
              {{ g.country_code }}
            </span>
            <span style="font-size:0.82rem">{{ g.country_name or '—' }}</span>
            {% if g.is_primary_scope %}
            <span style="font-size:0.65rem;color:var(--accent2);margin-left:auto">principal</span>
            {% endif %}
          </div>
          {% endfor %}
          {% else %}
          <span style="color:var(--muted);font-size:0.8rem">Non détecté</span>
          {% endif %}
        </div>

        <!-- Notes extraction -->
        {% if m.extraction_notes %}
        <div class="card">
          <div class="card-title">Notes d'extraction</div>
          <div style="font-size:0.75rem;color:var(--muted);font-family:var(--font-mono)">
            {{ m.extraction_notes }}
          </div>
        </div>
        {% endif %}

      </div>
    </div><!-- end detail-grid -->
    </form>
  </main>
</div>

<div class="toast" id="toast"></div>

<script>
// ── Raccourcis clavier ────────────────────────────────────────────────────────
document.addEventListener('keydown', function(e) {
  // Ignorer si on est dans un champ de saisie
  if (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) return;

  const form = document.getElementById('mainForm');
  if (e.key === 'v' || e.key === 'V') submitAction('VALIDATED');
  if (e.key === 'r' || e.key === 'R') submitAction('REJECTED');
  if (e.key === 'n' || e.key === 'N') submitAction('NEEDS_REVIEW');
  {% if next_id %}
  if (e.key === 'ArrowRight') window.location='{{ url_for("model_detail", model_id=next_id) }}';
  {% endif %}
  {% if prev_id %}
  if (e.key === 'ArrowLeft')  window.location='{{ url_for("model_detail", model_id=prev_id) }}';
  {% endif %}
});

function submitAction(action) {
  const form = document.getElementById('mainForm');
  // Créer un input hidden pour l'action
  let inp = document.createElement('input');
  inp.type = 'hidden'; inp.name = 'action'; inp.value = action;
  form.appendChild(inp);
  form.submit();
}

// ── Supprimer un paramètre ────────────────────────────────────────────────────
function delParam(paramId) {
  if (!confirm('Supprimer ce paramètre ?')) return;
  fetch(`/param/${paramId}/delete`, { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        document.getElementById(`param-${paramId}`)?.remove();
        showToast('Paramètre supprimé');
      }
    });
}

// ── Ajouter un paramètre ──────────────────────────────────────────────────────
function addParam(modelId) {
  const data = {
    param_type: document.getElementById('new_pt').value,
    symbol:     document.getElementById('new_sym').value || '?',
    value:      document.getElementById('new_val').value || '0',
    unit:       document.getElementById('new_unit').value || '',
  };
  fetch(`/model/${modelId}/add_param`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) { showToast('Paramètre ajouté'); location.reload(); }
  });
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}
</script>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

def render(template, **kwargs):
    """Helper pour rendre un template avec le CSS et la sidebar."""
    stats  = get_stats()
    active = kwargs.pop("active", "list")
    sidebar = Markup(render_template_string(SIDEBAR_HTML, stats=stats, active=active,
                                     **{k: v for k, v in kwargs.items()
                                        if k not in ("models","m","total")}))
    return render_template_string(
        template,
        css=Markup(BASE_CSS), sidebar=sidebar, stats=stats,
        **kwargs
    )


@app.route("/")
def index():
    status   = request.args.get("status", "PENDING")
    page     = int(request.args.get("page", 1))
    search   = request.args.get("search", "")
    per_page = 30

    models, total = get_model_list(status, page, per_page, search)
    total_pages   = (total + per_page - 1) // per_page

    status_labels = {
        "PENDING": "En attente",
        "VALIDATED": "Validés",
        "REJECTED": "Rejetés",
        "NEEDS_REVIEW": "À revoir",
    }

    active_map = {
        "PENDING": "list", "VALIDATED": "validated",
        "REJECTED": "rejected", "NEEDS_REVIEW": "review",
    }

    return render(INDEX_TEMPLATE,
        active=active_map.get(status, "list"),
        models=models, total=total,
        total_pages=total_pages, page=page,
        status=status, status_label=status_labels.get(status, status),
        search=search,
    )


@app.route("/model/<model_id>")
def model_detail(model_id):
    m = get_model_detail(model_id)
    if not m:
        return "Modèle non trouvé", 404

    prev_id, next_id = get_adjacent_models(model_id, m["validation_status"])
    flash_msg = request.args.get("flash", "")

    # Position dans la liste PENDING
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM piponto.models
                WHERE validation_status = %s
                  AND (extraction_confidence > %s
                       OR (extraction_confidence = %s AND model_id <= %s))
            """, (m["validation_status"], m["extraction_confidence"],
                  m["extraction_confidence"], model_id))
            position = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM piponto.models WHERE validation_status=%s",
                        (m["validation_status"],))
            total_pending = cur.fetchone()[0]

    return render(DETAIL_TEMPLATE,
        active="list",
        m=m,
        prev_id=prev_id, next_id=next_id,
        position=position, total_pending=total_pending,
        flash_msg=flash_msg,
        formalisms=FORMALISMS, model_types=MODEL_TYPES,
        spatial_structs=SPATIAL_STRUCTS, populations=POPULATIONS,
        platforms=PLATFORMS, param_types=PARAM_TYPES,
    )


@app.route("/model/<model_id>/save", methods=["POST"])
def model_save(model_id):
    data = request.form.to_dict()

    # Mettre à jour les paramètres depuis le formulaire
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT param_id FROM piponto.parameters WHERE model_id=%s",
                        (model_id,))
            param_ids = [r[0] for r in cur.fetchall()]

        for pid in param_ids:
            pdata = {
                "value":      data.get(f"pv_{pid}"),
                "min":        data.get(f"pmin_{pid}"),
                "max":        data.get(f"pmax_{pid}"),
                "param_type": data.get(f"pt_{pid}"),
                "unit":       data.get(f"pu_{pid}"),
            }
            try:
                update_param(pid, pdata)
            except Exception as e:
                logger.warning(f"Param {pid} update error: {e}")

    # Mettre à jour le modèle et son statut
    action = data.get("action", "PENDING")
    update_model(model_id, data)
    logger.info(f"Modèle {model_id} → {action}")

    # Aller au suivant si validé/rejeté
    if action in ("VALIDATED", "REJECTED"):
        _, next_id = get_adjacent_models(model_id, "PENDING")
        if next_id:
            return redirect(url_for("model_detail", model_id=next_id,
                                    flash=f"✓ {model_id} → {action}"))

    return redirect(url_for("model_detail", model_id=model_id,
                             flash=f"✓ Sauvegardé — {action}"))


@app.route("/param/<int:param_id>/delete", methods=["POST"])
def param_delete(param_id):
    try:
        delete_param(param_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/model/<model_id>/add_param", methods=["POST"])
def param_add(model_id):
    try:
        data = request.get_json()
        add_param(model_id, data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


# ══════════════════════════════════════════════════════════════════════════════
# LANCEMENT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"\n{'═'*50}")
    print(f"  PIPOnto — Interface de validation")
    print(f"  → http://localhost:{port}")
    print(f"{'═'*50}\n")
    app.run(debug=False, port=port, host="0.0.0.0")
