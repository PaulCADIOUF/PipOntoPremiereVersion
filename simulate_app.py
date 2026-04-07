"""
simulate_app.py
================
Application Flask de simulation épidémique PIPOnto.

Interface complète :
  • Saisie en langage naturel → NLP v2 → détection maladie/pays/paramètres
  • Sélection du modèle depuis la bibliothèque PIPOnto
  • Ajustement des paramètres par sliders
  • Courbe épidémique animée (Chart.js)
  • Métriques clés : pic, taux d'attaque, R₀, durée

Prérequis :
    pip install flask requests
    L'API PIPOnto doit tourner sur http://localhost:8000
    uvicorn api.main:app --port 8000

Usage :
    cd ~/piponto
    source venv/bin/activate
    python3 simulate_app.py

    → http://localhost:5001
"""

import os
import json
import logging
import requests
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify
from markupsafe import Markup
from dotenv import load_dotenv

# NLP v2 — ajouter le chemin si nécessaire
import sys
sys.path.insert(0, str(Path(__file__).parent))
try:
    from nlp.nlp_v2 import NLPParser
    NLP_OK = True
except ImportError:
    NLP_OK = False

load_dotenv(Path.home() / "piponto" / ".env")

API_BASE = os.getenv("PIPONTO_API", "http://localhost:8000")
app      = Flask(__name__)
app.secret_key = "piponto-simulate-2026"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("piponto.simulate")

_nlp = NLPParser() if NLP_OK else None

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE HTML PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PIPOnto — Simulation Épidémique</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Instrument+Serif:ital@0;1&family=Bricolage+Grotesque:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root {
  --bg:        #070A0F;
  --surface:   #0D1117;
  --surface2:  #131920;
  --border:    #1E2836;
  --border2:   #2A3A4A;
  --amber:     #F0A500;
  --amber-dim: #7A5400;
  --amber-pale:#1A1200;
  --red:       #E05050;
  --red-pale:  #1A0A0A;
  --green:     #40C880;
  --green-pale:#081A10;
  --blue:      #4A9EE8;
  --blue-pale: #080F1A;
  --purple:    #A06AE0;
  --text:      #C8D4E0;
  --muted:     #4A5A6A;
  --bright:    #E8F0F8;
  --font-serif:'Instrument Serif', Georgia, serif;
  --font-sans: 'Bricolage Grotesque', sans-serif;
  --font-mono: 'DM Mono', monospace;
}

html { font-size: 14px; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── Fond grille ── */
body::before {
  content:'';
  position:fixed; inset:0;
  background-image:
    linear-gradient(rgba(240,165,0,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(240,165,0,0.03) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events:none; z-index:0;
}

/* ── En-tête ── */
header {
  position:relative; z-index:10;
  border-bottom: 1px solid var(--border);
  padding: 1rem 2rem;
  display:flex; align-items:center; justify-content:space-between;
  background: linear-gradient(180deg, rgba(13,17,23,0.98) 0%, rgba(7,10,15,0.90) 100%);
  backdrop-filter: blur(8px);
}
.logo {
  display:flex; align-items:center; gap:0.75rem;
}
.logo-mark {
  width:36px; height:36px; border-radius:8px;
  background: linear-gradient(135deg, var(--amber) 0%, #C06000 100%);
  display:flex; align-items:center; justify-content:center;
  font-size:1.2rem;
}
.logo-text { font-size:1.1rem; font-weight:700; color:var(--bright); }
.logo-sub  { font-size:0.72rem; color:var(--muted); font-family:var(--font-mono); }

.api-status {
  display:flex; align-items:center; gap:0.4rem;
  font-family:var(--font-mono); font-size:0.7rem; color:var(--muted);
}
.api-dot {
  width:6px; height:6px; border-radius:50%;
  background:var(--green);
  box-shadow:0 0 6px var(--green);
  animation: pulse-dot 2s infinite;
}
.api-dot.error { background:var(--red); box-shadow:0 0 6px var(--red); }
@keyframes pulse-dot {
  0%,100%{opacity:1} 50%{opacity:0.4}
}

/* ── Layout ── */
.layout {
  position:relative; z-index:1;
  display:grid; grid-template-columns:1fr 420px;
  gap:0; min-height:calc(100vh - 57px);
}

/* ── Panneau gauche ── */
.panel-left {
  border-right:1px solid var(--border);
  padding:1.5rem;
  overflow-y:auto;
}

/* ── Zone de saisie NLP ── */
.nlp-zone {
  margin-bottom:1.5rem;
}
.nlp-label {
  font-size:0.68rem; font-weight:600; letter-spacing:0.12em;
  text-transform:uppercase; color:var(--amber); margin-bottom:0.6rem;
  display:flex; align-items:center; gap:0.4rem;
}
.nlp-label::before {
  content:''; display:inline-block;
  width:12px; height:2px; background:var(--amber);
}

.nlp-input-wrap {
  position:relative;
}
.nlp-textarea {
  width:100%; min-height:80px;
  background:var(--surface);
  border:1px solid var(--border2);
  border-radius:10px;
  color:var(--bright); font-family:var(--font-sans); font-size:0.95rem;
  padding:0.9rem 1rem 2.5rem 1rem;
  resize:vertical; outline:none;
  transition: border-color 0.2s, box-shadow 0.2s;
  line-height:1.5;
}
.nlp-textarea:focus {
  border-color:var(--amber);
  box-shadow:0 0 0 3px rgba(240,165,0,0.08),
             0 0 20px rgba(240,165,0,0.05);
}
.nlp-textarea::placeholder { color:var(--muted); font-style:italic; }

.nlp-btn {
  position:absolute; bottom:0.6rem; right:0.6rem;
  background:var(--amber); color:#1A0D00;
  border:none; border-radius:6px;
  padding:0.35rem 0.9rem;
  font-family:var(--font-sans); font-size:0.78rem; font-weight:700;
  cursor:pointer; transition:all 0.15s;
}
.nlp-btn:hover { background:#FFB800; transform:scale(1.02); }
.nlp-btn:disabled { background:var(--amber-dim); cursor:not-allowed; transform:none; }

/* Chips d'exemples */
.examples {
  display:flex; flex-wrap:wrap; gap:0.4rem; margin-top:0.6rem;
}
.chip {
  padding:0.25rem 0.65rem; border-radius:20px;
  background:var(--surface2); border:1px solid var(--border2);
  font-size:0.72rem; color:var(--muted); cursor:pointer;
  transition:all 0.15s;
}
.chip:hover { border-color:var(--amber); color:var(--amber); }

/* ── Intent parsé ── */
.intent-box {
  background:var(--surface);
  border:1px solid var(--border2);
  border-left:3px solid var(--amber);
  border-radius:8px;
  padding:0.75rem 1rem;
  margin-bottom:1.25rem;
  display:none;
}
.intent-box.show { display:block; animation: slide-in 0.2s ease; }
@keyframes slide-in { from{opacity:0;transform:translateY(-8px)} to{opacity:1;transform:translateY(0)} }

.intent-title {
  font-size:0.67rem; font-weight:700; letter-spacing:0.1em;
  text-transform:uppercase; color:var(--amber); margin-bottom:0.5rem;
}
.intent-tags { display:flex; flex-wrap:wrap; gap:0.3rem; }
.itag {
  padding:0.2rem 0.55rem; border-radius:4px;
  font-family:var(--font-mono); font-size:0.7rem;
  background:var(--amber-pale); color:var(--amber);
  border:1px solid var(--amber-dim);
}
.itag.country { background:rgba(74,158,232,0.1); color:var(--blue); border-color:rgba(74,158,232,0.3); }
.itag.days    { background:rgba(64,200,128,0.1); color:var(--green); border-color:rgba(64,200,128,0.3); }
.itag.n       { background:rgba(160,106,224,0.1); color:var(--purple); border-color:rgba(160,106,224,0.3); }
.itag.form    { background:rgba(224,80,80,0.1); color:var(--red); border-color:rgba(224,80,80,0.3); }

/* ── Section titre ── */
.section-title {
  font-size:0.67rem; font-weight:700; letter-spacing:0.12em;
  text-transform:uppercase; color:var(--muted);
  margin-bottom:0.75rem;
  display:flex; align-items:center; gap:0.5rem;
}
.section-title .count {
  background:var(--surface2); border:1px solid var(--border2);
  padding:0.1rem 0.45rem; border-radius:20px;
  font-size:0.65rem; color:var(--text);
}

/* ── Cartes modèles ── */
.models-list { display:flex; flex-direction:column; gap:0.6rem; margin-bottom:1.5rem; }

.model-card {
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:10px; padding:0.9rem 1rem;
  cursor:pointer; transition:all 0.15s;
  position:relative; overflow:hidden;
}
.model-card::before {
  content:''; position:absolute; left:0; top:0; bottom:0;
  width:3px; background:var(--border2);
  transition:background 0.15s;
}
.model-card:hover { border-color:var(--border2); transform:translateX(2px); }
.model-card:hover::before { background:var(--amber); }
.model-card.selected {
  border-color:var(--amber);
  background:linear-gradient(135deg, var(--amber-pale) 0%, var(--surface) 100%);
}
.model-card.selected::before { background:var(--amber); }

.model-id {
  font-family:var(--font-mono); font-size:0.72rem;
  color:var(--amber); margin-bottom:0.35rem;
}
.model-name {
  font-size:0.85rem; font-weight:600; color:var(--bright);
  margin-bottom:0.4rem; line-height:1.3;
}
.model-meta {
  display:flex; flex-wrap:wrap; gap:0.3rem; align-items:center;
}
.badge {
  padding:0.15rem 0.5rem; border-radius:4px;
  font-family:var(--font-mono); font-size:0.65rem; font-weight:500;
}
.badge-seir   { background:rgba(240,165,0,0.12); color:var(--amber); }
.badge-abm    { background:rgba(64,200,128,0.12); color:var(--green); }
.badge-other  { background:rgba(74,74,100,0.3); color:var(--muted); }
.badge-stoch  { background:rgba(224,80,80,0.12); color:var(--red); }
.badge-code   { background:rgba(74,158,232,0.12); color:var(--blue); }

.model-conf {
  margin-left:auto;
  font-family:var(--font-mono); font-size:0.68rem; color:var(--muted);
}
.conf-dot {
  display:inline-block; width:6px; height:6px; border-radius:50%;
  margin-right:3px;
}

/* Params du modèle */
.model-params {
  margin-top:0.5rem; padding-top:0.5rem;
  border-top:1px solid var(--border);
  display:flex; flex-wrap:wrap; gap:0.3rem;
}
.param-pill {
  font-family:var(--font-mono); font-size:0.65rem;
  padding:0.1rem 0.4rem; border-radius:3px;
  background:var(--surface2); border:1px solid var(--border2);
  color:var(--text);
}

/* ── Sliders ── */
.sliders-section {
  background:var(--surface);
  border:1px solid var(--border2);
  border-radius:10px; padding:1rem;
  margin-bottom:1.25rem; display:none;
}
.sliders-section.show { display:block; animation:slide-in 0.2s ease; }
.sliders-title {
  font-size:0.67rem; font-weight:700; letter-spacing:0.1em;
  text-transform:uppercase; color:var(--muted); margin-bottom:0.75rem;
}
.slider-row {
  display:grid; grid-template-columns:80px 1fr 80px;
  align-items:center; gap:0.5rem; margin-bottom:0.6rem;
}
.slider-label {
  font-family:var(--font-mono); font-size:0.75rem; color:var(--amber);
}
input[type=range] {
  -webkit-appearance:none; appearance:none;
  width:100%; height:4px;
  background:var(--border2); border-radius:2px; outline:none;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance:none; appearance:none;
  width:14px; height:14px; border-radius:50%;
  background:var(--amber); cursor:pointer;
  box-shadow:0 0 6px rgba(240,165,0,0.5);
}
input[type=range]::-webkit-slider-track { background:var(--border2); }
.slider-val {
  text-align:right;
  font-family:var(--font-mono); font-size:0.75rem; color:var(--bright);
}

/* Bouton simuler */
.simulate-btn {
  width:100%; padding:0.75rem;
  background:linear-gradient(135deg, var(--amber) 0%, #C06000 100%);
  border:none; border-radius:8px;
  font-family:var(--font-sans); font-size:0.9rem; font-weight:700;
  color:#1A0D00; cursor:pointer;
  transition:all 0.2s;
  display:flex; align-items:center; justify-content:center; gap:0.5rem;
  letter-spacing:0.02em;
}
.simulate-btn:hover {
  transform:translateY(-1px);
  box-shadow:0 6px 20px rgba(240,165,0,0.25);
}
.simulate-btn:disabled {
  background:var(--amber-dim); color:var(--muted);
  transform:none; box-shadow:none; cursor:not-allowed;
}
.spin { animation: rotate 0.8s linear infinite; }
@keyframes rotate { to{transform:rotate(360deg)} }

/* ── Panneau droite (résultats) ── */
.panel-right {
  background:var(--surface);
  border-left:1px solid var(--border);
  overflow-y:auto;
  display:flex; flex-direction:column;
}

.results-empty {
  flex:1; display:flex; flex-direction:column;
  align-items:center; justify-content:center; gap:1rem;
  padding:2rem; text-align:center;
}
.results-empty .icon { font-size:3rem; opacity:0.3; }
.results-empty p { color:var(--muted); font-size:0.85rem; max-width:260px; line-height:1.5; }

.results-content { padding:1.25rem; display:none; }
.results-content.show { display:block; animation:slide-in 0.3s ease; }

/* Métriques */
.metrics-grid {
  display:grid; grid-template-columns:1fr 1fr;
  gap:0.6rem; margin-bottom:1.25rem;
}
.metric-card {
  background:var(--surface2);
  border:1px solid var(--border);
  border-radius:8px; padding:0.75rem 0.9rem;
}
.metric-label {
  font-size:0.62rem; font-weight:700; letter-spacing:0.1em;
  text-transform:uppercase; color:var(--muted); margin-bottom:0.25rem;
}
.metric-value {
  font-family:var(--font-mono); font-size:1.4rem; font-weight:500;
  color:var(--bright); line-height:1;
}
.metric-value.red    { color:var(--red); }
.metric-value.amber  { color:var(--amber); }
.metric-value.green  { color:var(--green); }
.metric-value.purple { color:var(--purple); }
.metric-sub {
  font-size:0.65rem; color:var(--muted); margin-top:0.15rem;
}

/* Graphique */
.chart-wrap {
  background:var(--surface2);
  border:1px solid var(--border);
  border-radius:10px; padding:1rem;
  margin-bottom:1rem;
}
.chart-header {
  display:flex; align-items:center; justify-content:space-between;
  margin-bottom:0.75rem;
}
.chart-title {
  font-size:0.68rem; font-weight:700; letter-spacing:0.1em;
  text-transform:uppercase; color:var(--muted);
}
.chart-legend {
  display:flex; gap:0.75rem;
}
.legend-item {
  display:flex; align-items:center; gap:0.3rem;
  font-family:var(--font-mono); font-size:0.62rem; color:var(--muted);
}
.legend-dot {
  width:8px; height:8px; border-radius:2px;
}
canvas { width:100% !important; }

/* Résumé texte */
.summary-box {
  background:var(--amber-pale);
  border:1px solid var(--amber-dim);
  border-radius:8px; padding:0.85rem 1rem;
  font-size:0.82rem; color:var(--text); line-height:1.6;
  font-family:var(--font-serif); font-style:italic;
}

/* ── Panneau ontologique ── */
.onto-panel {
  margin-top:1.25rem;
  background:var(--surface);
  border:1px solid var(--border2);
  border-left:3px solid var(--purple);
  border-radius:10px; padding:1rem;
  display:none;
}
.onto-panel.show { display:block; animation:slide-in 0.25s ease; }
.onto-title {
  font-size:0.67rem; font-weight:700; letter-spacing:0.12em;
  text-transform:uppercase; color:var(--purple); margin-bottom:0.7rem;
  display:flex; align-items:center; gap:0.4rem;
}
.onto-row {
  display:flex; align-items:baseline; gap:0.5rem;
  margin-bottom:0.45rem; flex-wrap:wrap;
}
.onto-key {
  font-family:var(--font-mono); font-size:0.65rem; color:var(--muted);
  min-width:120px; flex-shrink:0;
}
.onto-val {
  font-family:var(--font-mono); font-size:0.7rem; color:var(--text);
  word-break:break-all;
}
.onto-uri {
  color:var(--purple); font-size:0.65rem;
}
.onto-note {
  margin-top:0.6rem; padding:0.6rem 0.75rem;
  background:rgba(160,106,224,0.06);
  border:1px solid rgba(160,106,224,0.2);
  border-radius:6px;
  font-size:0.75rem; color:var(--text); line-height:1.55;
  font-style:italic;
}
.hierarchy-chain {
  display:flex; align-items:center; gap:0.3rem; flex-wrap:wrap;
}
.hier-node {
  font-family:var(--font-mono); font-size:0.65rem;
  padding:0.15rem 0.45rem; border-radius:3px;
  background:rgba(160,106,224,0.1); color:var(--purple);
  border:1px solid rgba(160,106,224,0.25);
}
.hier-arrow { color:var(--muted); font-size:0.7rem; }
.onto-tag {
  padding:0.15rem 0.45rem; border-radius:3px;
  font-family:var(--font-mono); font-size:0.62rem;
  background:rgba(160,106,224,0.1); color:var(--purple);
  border:1px solid rgba(160,106,224,0.2);
  margin:0.1rem;
}
.consistent-yes { color:var(--green); }
.consistent-no  { color:var(--amber); }
.loader-overlay {
  position:fixed; inset:0;
  background:rgba(7,10,15,0.8); backdrop-filter:blur(4px);
  z-index:100;
  display:none; align-items:center; justify-content:center;
  flex-direction:column; gap:1rem;
}
.loader-overlay.show { display:flex; }
.loader-ring {
  width:48px; height:48px;
  border:3px solid var(--border2);
  border-top-color:var(--amber);
  border-radius:50%;
  animation:rotate 0.7s linear infinite;
}
.loader-text {
  font-family:var(--font-mono); font-size:0.8rem; color:var(--muted);
}

/* ── Maladies pills rapides ── */
.disease-pills {
  display:flex; flex-wrap:wrap; gap:0.35rem;
  margin-bottom:1.25rem;
}
.disease-pill {
  padding:0.3rem 0.75rem; border-radius:20px;
  background:var(--surface2); border:1px solid var(--border);
  font-size:0.75rem; color:var(--muted); cursor:pointer;
  transition:all 0.15s;
}
.disease-pill:hover { background:var(--amber-pale); border-color:var(--amber-dim); color:var(--amber); }
.disease-pill.active { background:var(--amber-pale); border-color:var(--amber); color:var(--amber); }

/* ── Toast ── */
.toast {
  position:fixed; bottom:1.5rem; left:50%; transform:translateX(-50%) translateY(100px);
  background:var(--surface); border:1px solid var(--red);
  border-radius:8px; padding:0.6rem 1.2rem;
  font-size:0.78rem; color:var(--red);
  transition:transform 0.3s; z-index:200;
}
.toast.show { transform:translateX(-50%) translateY(0); }

/* ── Responsive ── */
@media(max-width:900px) {
  .layout { grid-template-columns:1fr; }
  .panel-right { border-left:none; border-top:1px solid var(--border); }
}
</style>
</head>
<body>

<div class="loader-overlay" id="loader">
  <div class="loader-ring"></div>
  <div class="loader-text">Simulation en cours...</div>
</div>

<div class="toast" id="toast"></div>

<header>
  <div class="logo">
    <div class="logo-mark">🧬</div>
    <div>
      <div class="logo-text">PIPOnto Simulator</div>
      <div class="logo-sub">Plateforme de Simulation Épidémique</div>
    </div>
  </div>
  <div class="api-status">
    <div class="api-dot" id="apiDot"></div>
    <span id="apiLabel">API PIPOnto</span>
    <span style="margin-left:0.5rem;color:var(--border2)">|</span>
    <span id="apiStats" style="margin-left:0.5rem"></span>
  </div>
</header>

<div class="layout">

  <!-- ── PANNEAU GAUCHE ── -->
  <div class="panel-left">

    <!-- NLP input -->
    <div class="nlp-zone">
      <div class="nlp-label">Décrivez votre simulation</div>
      <div class="nlp-input-wrap">
        <textarea class="nlp-textarea" id="nlpInput"
          placeholder="Ex: Simule une épidémie de COVID-19 en France avec 68 millions de personnes sur 365 jours..."
          onkeydown="if(event.key==='Enter'&&(event.ctrlKey||event.metaKey))analyzeNLP()"></textarea>
        <button class="nlp-btn" id="nlpBtn" onclick="analyzeNLP()">Analyser →</button>
      </div>
      <div class="examples" id="examples"></div>
    </div>

    <!-- Intent parsé -->
    <div class="intent-box" id="intentBox">
      <div class="intent-title">Paramètres détectés</div>
      <div class="intent-tags" id="intentTags"></div>
    </div>

    <!-- Sélection rapide maladies -->
    <div class="section-title">Maladies disponibles</div>
    <div class="disease-pills" id="diseasePills"></div>

    <!-- Liste des modèles -->
    <div class="section-title">
      Modèles correspondants
      <span class="count" id="modelCount">0</span>
    </div>
    <div class="models-list" id="modelsList"></div>

    <!-- Sliders -->
    <div class="sliders-section" id="slidersSection">
      <div class="sliders-title">Paramètres de simulation</div>
      <div id="slidersContent"></div>
      <button class="simulate-btn" id="simBtn" onclick="runSimulation()">
        <span id="simBtnIcon">▶</span>
        <span id="simBtnText">Lancer la simulation</span>
      </button>
    </div>

  </div>

  <!-- ── PANNEAU DROITE (résultats) ── -->
  <div class="panel-right">

    <div class="results-empty" id="resultsEmpty">
      <div class="icon">📈</div>
      <p>Décrivez votre scénario épidémique pour lancer une simulation avec les modèles validés de la bibliothèque PIPOnto.</p>
      <p style="font-size:0.72rem;color:var(--border2);font-family:var(--font-mono)">
        Ctrl+Entrée pour analyser
      </p>
    </div>

    <div class="results-content" id="resultsContent">
      <div class="section-title" id="resultTitle">Résultats</div>

      <div class="metrics-grid" id="metricsGrid"></div>

      <div class="chart-wrap">
        <div class="chart-header">
          <div class="chart-title">Courbe épidémique</div>
          <div class="chart-legend" id="chartLegend"></div>
        </div>
        <canvas id="epicurveChart" height="220"></canvas>
      </div>

      <div class="summary-box" id="summaryBox"></div>

      <!-- Panneau ontologique M8/M2 -->
      <div class="onto-panel" id="ontoPanel">
        <div class="onto-title">🔗 Ancrage Ontologique PIPOnto</div>
        <div id="ontoPanelContent"></div>
      </div>
    </div>

  </div>
</div>

<script>
// ══════════════════════════════════════════════════════════════════════════════
// État global
// ══════════════════════════════════════════════════════════════════════════════
let state = {
  intent:        null,
  models:        [],
  selectedModel: null,
  simParams:     { N: 1000000, days: 365, I0: 10, beta: null, gamma: null, sigma: null },
  chart:         null,
};

const EXAMPLES = [
  "COVID-19 France 68 millions 365 jours",
  "Paludisme Sénégal population rurale 2 millions 6 mois",
  "Grippe UK 60M 180 jours stochastique",
  "Ebola Congo 5M 120 jours soignants",
  "SEIR influenza Italie 500000 personnes",
];

const COMP_COLORS = {
  S: { border:"#4A9EE8", bg:"rgba(74,158,232,0.12)" },
  E: { border:"#F0A500", bg:"rgba(240,165,0,0.10)" },
  I: { border:"#E05050", bg:"rgba(224,80,80,0.12)" },
  R: { border:"#40C880", bg:"rgba(64,200,128,0.10)" },
  D: { border:"#4A5A6A", bg:"rgba(74,90,106,0.10)" },
};

// ══════════════════════════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════════════════════════
async function init() {
  // Exemples
  const ex = document.getElementById("examples");
  EXAMPLES.forEach(e => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = e;
    chip.onclick = () => {
      document.getElementById("nlpInput").value = e;
      analyzeNLP();
    };
    ex.appendChild(chip);
  });

  // Vérifier statut API + charger maladies
  await checkAPI();
  await loadDiseases();
}

async function checkAPI() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    document.getElementById("apiDot").className = "api-dot";
    document.getElementById("apiLabel").textContent = "API connectée";
    document.getElementById("apiStats").textContent =
      `${d.validated_models} modèles`;
  } catch(e) {
    document.getElementById("apiDot").className = "api-dot error";
    document.getElementById("apiLabel").textContent = "API hors ligne";
    document.getElementById("apiStats").textContent = "";
  }
}

async function loadDiseases() {
  try {
    const r = await fetch("/api/diseases");
    const d = await r.json();
    const container = document.getElementById("diseasePills");
    (d.diseases || []).filter(x => x.model_count > 0).forEach(dis => {
      const pill = document.createElement("div");
      pill.className = "disease-pill";
      pill.textContent = `${dis.name_en} (${dis.model_count})`;
      pill.dataset.disease = dis.name_en;
      pill.onclick = () => selectDisease(dis.name_en, pill);
      container.appendChild(pill);
    });
  } catch(e) {}
}

function selectDisease(name, el) {
  document.querySelectorAll(".disease-pill").forEach(p => p.classList.remove("active"));
  el.classList.add("active");
  document.getElementById("nlpInput").value =
    `Simule une épidémie de ${name} sur 365 jours avec 1 million de personnes`;
  analyzeNLP();
}

// ══════════════════════════════════════════════════════════════════════════════
// NLP
// ══════════════════════════════════════════════════════════════════════════════
async function analyzeNLP() {
  const text = document.getElementById("nlpInput").value.trim();
  if (!text) return;

  const btn = document.getElementById("nlpBtn");
  btn.disabled = true;
  btn.textContent = "...";

  try {
    const r  = await fetch("/api/parse", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({text})
    });
    const data = await r.json();
    state.intent = data.intent;
    showIntent(data.intent);
    await searchModels(data.intent);
  } catch(e) {
    showToast("Erreur NLP : " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyser →";
  }
}

function showIntent(intent) {
  const box  = document.getElementById("intentBox");
  const tags = document.getElementById("intentTags");
  tags.innerHTML = "";

  const items = [];
  if (intent.disease_name)   items.push({label:"🦠 " + intent.disease_name, cls:""});
  if (intent.country_name)   items.push({label:"📍 " + intent.country_name,  cls:"country"});
  if (intent.N)              items.push({label:"👥 " + fmt(intent.N),         cls:"n"});
  if (intent.days)           items.push({label:"📅 " + intent.days + "j",     cls:"days"});
  if (intent.formalism)      items.push({label:"🔬 " + intent.formalism,      cls:"form"});
  if (intent.population_type && intent.population_type !== "GENERAL")
                             items.push({label:"👤 " + intent.population_type, cls:"country"});

  if (!items.length) {
    tags.innerHTML = "<span style='color:var(--muted);font-size:0.78rem'>Aucun paramètre détecté — essayez de préciser la maladie ou le pays</span>";
  } else {
    items.forEach(({label, cls}) => {
      const t = document.createElement("div");
      t.className = "itag " + cls;
      t.textContent = label;
      tags.appendChild(t);
    });
  }

  box.classList.add("show");

  // Pré-remplir les params de simulation
  if (intent.N)    state.simParams.N    = intent.N;
  if (intent.days) state.simParams.days = intent.days;
}

// ══════════════════════════════════════════════════════════════════════════════
// Recherche de modèles
// ══════════════════════════════════════════════════════════════════════════════
async function searchModels(intent) {
  const params = new URLSearchParams();
  if (intent.disease_name)  params.set("disease",    intent.disease_name);
  if (intent.country_code)  params.set("country",    intent.country_code);
  if (intent.formalism)     params.set("formalism",  intent.formalism);
  if (intent.population_type && intent.population_type !== "GENERAL")
                            params.set("population", intent.population_type);
  params.set("limit", "5");

  try {
    const r    = await fetch("/api/models?" + params);
    const data = await r.json();
    state.models = data.models || [];
    document.getElementById("modelCount").textContent = data.total || 0;

    if (!state.models.length && intent.disease_name) {
      // ── Fallback ontologique : relaxer les filtres ────────────────────────
      // 1. Retry sans le pays
      const p2 = new URLSearchParams();
      p2.set("disease", intent.disease_name); p2.set("limit","5");
      const r2   = await fetch("/api/models?" + p2);
      const d2   = await r2.json();
      if (d2.models?.length) {
        state.models = d2.models;
        document.getElementById("modelCount").textContent =
          d2.total + " (pays élargi)";
        renderModels(state.models, "pays_elargi");
        return;
      }
      // 2. Retry sans aucun filtre sauf la maladie
      const r3   = await fetch(`/api/models?limit=5`);
      const d3   = await r3.json();
      // 3. Générer un modèle synthétique depuis l'ontologie
      await buildOntoFallbackModel(intent);
      return;
    }
    renderModels(state.models);
  } catch(e) {
    showToast("Erreur recherche : " + e.message);
  }
}

async function buildOntoFallbackModel(intent) {
  // Interroger l'ontologie M8 pour la maladie
  try {
    const r    = await fetch(`/api/ontology/disease/${encodeURIComponent(intent.disease_name)}`);
    const onto = await r.json();

    if (onto.error) { renderModels([], null, intent); return; }

    // Construire un modèle synthétique depuis l'ontologie
    const formalism = onto.primary_formalism || "SEIR";
    const tp        = onto.typical_params || {};
    const r0_mid    = onto.r0_range
      ? ((onto.r0_range[0] + onto.r0_range[1]) / 2).toFixed(2)
      : "2.5";

    // Calculer beta depuis R0 et gamma si possible
    const gamma = tp.gamma || 0.143;
    const beta  = tp.beta  || parseFloat(r0_mid) * gamma;

    const syntheticModel = {
      model_id:             `${formalism}_${intent.disease_name.replace(/[^A-Za-z0-9]/g,"")}_Ontologie_M8`,
      name:                 `Modèle synthétique M8 — ${intent.disease_name}`,
      formalism:            formalism,
      model_type:           "DETERMINISTIC",
      disease_name:         intent.disease_name,
      extraction_confidence: 1.0,
      countries:            [],
      year:                 2026,
      has_code:             false,
      _synthetic:           true,             // flag : pas en BD
      _onto_source:         true,
      _params: {
        beta:  beta,
        gamma: gamma,
        sigma: tp.sigma  || 0.196,
        mu:    tp.mu     || 0.0,
        omega: tp.omega  || 0.0,
      },
      _onto: onto,
    };

    state.models = [syntheticModel];
    document.getElementById("modelCount").textContent = "1 (ontologie M8)";
    renderModels([syntheticModel], "onto_fallback", intent);
  } catch(e) {
    renderModels([], null, intent);
  }
}

function renderModels(models, mode, intent) {
  const list = document.getElementById("modelsList");
  list.innerHTML = "";

  if (!models.length) {
    list.innerHTML = `<div style="color:var(--muted);font-size:0.82rem;padding:1rem;
      text-align:center;border:1px dashed var(--border);border-radius:8px">
      Aucun modèle trouvé pour ce scénario.<br>
      <span style="font-size:0.72rem">Essayez avec moins de filtres.</span>
    </div>`;
    return;
  }

  // Bannière contextuelle selon le mode
  if (mode === "pays_elargi") {
    list.insertAdjacentHTML("beforeend",
      `<div style="font-size:0.7rem;color:var(--amber);padding:0.4rem 0.6rem;
       background:var(--amber-pale);border-radius:6px;margin-bottom:0.4rem">
       ⚠️ Aucun modèle pour ce pays — résultats élargis à la maladie
      </div>`);
  }
  if (mode === "onto_fallback") {
    list.insertAdjacentHTML("beforeend",
      `<div style="font-size:0.7rem;color:var(--purple);padding:0.5rem 0.75rem;
       background:rgba(160,106,224,0.08);border:1px solid rgba(160,106,224,0.25);
       border-radius:6px;margin-bottom:0.5rem;line-height:1.5">
       🔗 <strong>Aucun modèle validé dans la bibliothèque.</strong><br>
       Paramètres calculés depuis l'ontologie PIPOnto M8
       ${intent?.disease_name ? "pour " + intent.disease_name : ""}.
       Les valeurs sont issues de la littérature épidémiologique.
      </div>`);
  }

  models.forEach((m, i) => {
    const card = document.createElement("div");
    card.className = "model-card";
    card.onclick   = () => selectModel(m, i);

    // ── Carte spéciale pour modèle synthétique (ontologie M8) ─────────────
    if (m._synthetic) {
      const p    = m._params || {};
      const onto = m._onto   || {};
      const r0   = onto.r0_range
        ? `R₀ = ${onto.r0_range[0]}–${onto.r0_range[1]}` : "";
      card.innerHTML = `
        <div class="model-id" style="color:var(--purple)">
          🔗 ${m.model_id}
        </div>
        <div class="model-name">${m.name}</div>
        <div class="model-meta" style="margin-top:0.3rem">
          <span class="badge" style="background:rgba(160,106,224,0.15);color:var(--purple)">
            ${m.formalism}
          </span>
          <span class="badge badge-other">Ontologie M8</span>
          <span class="badge badge-other">${r0}</span>
        </div>
        <div class="model-params" style="margin-top:0.4rem">
          ${Object.entries(p).filter(([k,v])=>v>0).map(([k,v])=>
            `<span class="param-pill">${k}=${v.toFixed(4)}</span>`
          ).join("")}
        </div>
        <div style="font-size:0.65rem;color:var(--muted);margin-top:0.4rem;font-style:italic">
          Source : PIPOnto Module M8 — Domaine Épidémiologique
        </div>
      `;
      list.appendChild(card);
      return;
    }

    const formClass = ["SEIR","SIR","SEIRD","SEIRS","SIS"].includes(m.formalism)
      ? "badge-seir"
      : m.formalism === "ABM" ? "badge-abm"
      : ["STOCHASTIC_SIR","STOCHASTIC_SEIR"].includes(m.formalism) ? "badge-stoch"
      : "badge-other";

    const confColor = m.extraction_confidence >= 0.7 ? "#40C880"
                    : m.extraction_confidence >= 0.4 ? "#F0A500" : "#E05050";

    const countries = (m.countries || []).slice(0,4).join(", ") || "—";
    const params = m.param_count
      ? `<span class="param-pill">params: ${m.param_count}</span>` : "";
    const codeTag = m.has_code
      ? `<span class="badge badge-code">code</span>` : "";
    const yearTag = m.year
      ? `<span class="badge badge-other">${m.year}</span>` : "";

    card.innerHTML = `
      <div class="model-id">${m.model_id}</div>
      <div class="model-name">${(m.name||"").substring(0,70)}${(m.name||"").length>70?"…":""}</div>
      <div class="model-meta">
        <span class="badge ${formClass}">${m.formalism}</span>
        <span class="badge badge-other">${m.model_type||""}</span>
        ${yearTag}${codeTag}
        <span class="badge badge-other">📍 ${countries}</span>
        <span class="model-conf">
          <span class="conf-dot" style="background:${confColor}"></span>
          ${(m.extraction_confidence*100).toFixed(0)}%
        </span>
      </div>
      <div class="model-params">${params}</div>
    `;
    list.appendChild(card);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// Sélection modèle + chargement paramètres
// ══════════════════════════════════════════════════════════════════════════════
async function selectModel(model, idx) {
  document.querySelectorAll(".model-card").forEach(c => c.classList.remove("selected"));
  document.querySelectorAll(".model-card")[idx].classList.add("selected");
  state.selectedModel = model;

  // ── Modèle synthétique (ontologie M8) — pas d'appel BD ────────────────────
  if (model._synthetic) {
    const p = model._params || {};
    state.simParams = {
      N:     state.intent?.N    || 1_000_000,
      days:  state.intent?.days || 365,
      I0:    state.intent?.I0   || 10,
      beta:  p.beta  || 0.31,
      gamma: p.gamma || 0.143,
      sigma: p.sigma || 0.196,
    };
    buildSliders(model, { simulation_dict: {
      "β": { value: state.simParams.beta },
      "γ": { value: state.simParams.gamma },
      "σ": { value: state.simParams.sigma },
    }});
    document.getElementById("slidersSection").classList.add("show");
    return;
  }

  // ── Modèle BD : charger ses paramètres ────────────────────────────────────
  try {
    const r    = await fetch(`/api/model/${model.model_id}/params`);
    const data = await r.json();
    if (data.simulation_dict) {
      for (const [sym, info] of Object.entries(data.simulation_dict)) {
        const val = typeof info === "object" ? info.value : info;
        if      (sym === "β")  state.simParams.beta  = val;
        else if (sym === "γ")  state.simParams.gamma = val;
        else if (sym === "σ")  state.simParams.sigma = val;
      }
    }
    buildSliders(model, data);
    document.getElementById("slidersSection").classList.add("show");
  } catch(e) {
    buildSliders(model, {});
    document.getElementById("slidersSection").classList.add("show");
  }
}

function buildSliders(model, paramsData) {
  const N     = state.simParams.N    || (state.intent?.N)    || 1_000_000;
  const days  = state.simParams.days || (state.intent?.days) || 365;
  const beta  = state.simParams.beta  || 0.31;
  const gamma = state.simParams.gamma || 0.143;
  const sigma = state.simParams.sigma || 0.196;
  const I0    = state.simParams.I0   || 10;

  const rows = [
    { id:"sl_N",     label:"N (pop.)",  min:10000, max:1000000000, step:10000,  val:N,     fmt:v=>fmt(v),      key:"N" },
    { id:"sl_days",  label:"Durée (j)", min:30,    max:1095,        step:5,      val:days,  fmt:v=>v+" j",      key:"days" },
    { id:"sl_I0",    label:"I₀",        min:1,     max:1000,        step:1,      val:I0,    fmt:v=>v,           key:"I0" },
    { id:"sl_beta",  label:"β",         min:0.01,  max:2.0,         step:0.01,   val:beta,  fmt:v=>v.toFixed(3),key:"beta" },
    { id:"sl_gamma", label:"γ",         min:0.01,  max:1.0,         step:0.01,   val:gamma, fmt:v=>v.toFixed(3),key:"gamma" },
    { id:"sl_sigma", label:"σ",         min:0.01,  max:1.0,         step:0.01,   val:sigma, fmt:v=>v.toFixed(3),key:"sigma" },
  ];

  const html = rows.map(r => `
    <div class="slider-row">
      <span class="slider-label">${r.label}</span>
      <input type="range" id="${r.id}"
        min="${r.min}" max="${r.max}" step="${r.step}" value="${r.val}"
        oninput="updateSlider('${r.id}','${r.id}_val',${r.min},${r.max},'${r.key}',this.value,'${r.fmt.toString()}')">
      <span class="slider-val" id="${r.id}_val">${r.fmt(r.val)}</span>
    </div>
  `).join("");

  document.getElementById("slidersContent").innerHTML = html;

  // Stocker les valeurs
  state.simParams = { N, days, I0, beta, gamma, sigma };
}

function updateSlider(slId, valId, min, max, key, rawVal) {
  let val = parseFloat(rawVal);
  if (key === "N" || key === "I0" || key === "days") val = Math.round(val);
  state.simParams[key] = val;

  const el = document.getElementById(valId);
  if (key === "N")    el.textContent = fmt(val);
  else if (key === "days") el.textContent = val + " j";
  else                el.textContent = (key === "I0") ? val : val.toFixed(3);
}

// ══════════════════════════════════════════════════════════════════════════════
// Simulation
// ══════════════════════════════════════════════════════════════════════════════
async function runSimulation() {
  if (!state.selectedModel) { showToast("Sélectionnez un modèle d'abord"); return; }

  const btn = document.getElementById("simBtn");
  const ico = document.getElementById("simBtnIcon");
  const txt = document.getElementById("simBtnText");
  btn.disabled = true;
  ico.textContent = "⟳";
  ico.className   = "spin";
  txt.textContent = "Calcul en cours...";
  document.getElementById("loader").classList.add("show");

  const isSynthetic = state.selectedModel?._synthetic;

  const body = {
    // Pour les modèles synthétiques, on passe le formalisme directement
    // sans model_id (pas en BD)
    ...(isSynthetic ? {} : { model_id: state.selectedModel.model_id }),
    formalism: isSynthetic ? state.selectedModel.formalism : undefined,
    N:    state.simParams.N,
    I0:   state.simParams.I0,
    days: state.simParams.days,
    beta:  state.simParams.beta  || undefined,
    gamma: state.simParams.gamma || undefined,
    sigma: state.simParams.sigma || undefined,
  };

  try {
    const r    = await fetch("/api/simulate", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)
    });
    const data = await r.json();

    if (!r.ok) {
      showToast("Erreur : " + (data.detail || r.statusText));
      return;
    }

    showResults(data);

    // Charger les données ontologiques M8/M2
    const disease = state.selectedModel?.disease_name;
    const modelId = state.selectedModel?.model_id;
    await loadOntologyData(disease, modelId);
  } catch(e) {
    showToast("Erreur simulation : " + e.message);
  } finally {
    btn.disabled = false;
    ico.textContent = "▶";
    ico.className   = "";
    txt.textContent = "Relancer";
    document.getElementById("loader").classList.remove("show");
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Affichage des résultats
// ══════════════════════════════════════════════════════════════════════════════
function showResults(data) {
  document.getElementById("resultsEmpty").style.display   = "none";
  document.getElementById("resultsContent").classList.add("show");

  // Titre
  const disease = state.selectedModel?.disease_name || "Épidémie";
  document.getElementById("resultTitle").innerHTML =
    `<span>Résultats — </span><span style="color:var(--amber)">${disease}</span>`;

  // Métriques
  const metrics = [
    { label:"Pic infectieux",    val:fmt(data.peak_infected),         sub:"au jour "+data.peak_day,                    cls:"red" },
    { label:"Taux d'attaque",    val:(data.attack_rate*100).toFixed(1)+"%", sub:fmt(data.total_infected)+" cas",          cls:"amber" },
    { label:"R₀ effectif",       val:data.R0_effective.toFixed(2),    sub: data.R0_effective > 1 ? "épidémie active" : "sous contrôle",
      cls: data.R0_effective > 1 ? "red" : "green" },
    { label:"Durée épidémie",    val:data.epidemic_duration_days+"j", sub:data.formalism+" model",                     cls:"purple" },
  ];

  document.getElementById("metricsGrid").innerHTML = metrics.map(m => `
    <div class="metric-card">
      <div class="metric-label">${m.label}</div>
      <div class="metric-value ${m.cls}">${m.val}</div>
      <div class="metric-sub">${m.sub}</div>
    </div>
  `).join("");

  // Graphique
  drawChart(data);

  // Résumé
  document.getElementById("summaryBox").textContent = data.summary || "";
}

function drawChart(data) {
  const ts = data.time_series;
  const t  = ts.t;
  const comps = Object.keys(ts).filter(k => k !== "t");

  // Légende
  const legend = document.getElementById("chartLegend");
  legend.innerHTML = comps.map(c => `
    <div class="legend-item">
      <div class="legend-dot" style="background:${COMP_COLORS[c]?.border||'#888'}"></div>
      ${c}
    </div>
  `).join("");

  // Détruire l'ancien chart si existant
  if (state.chart) { state.chart.destroy(); state.chart = null; }

  const ctx = document.getElementById("epicurveChart").getContext("2d");
  const datasets = comps.map(c => ({
    label: c,
    data: ts[c],
    borderColor:     COMP_COLORS[c]?.border || "#888",
    backgroundColor: COMP_COLORS[c]?.bg     || "rgba(136,136,136,0.1)",
    borderWidth: c === "I" ? 2.5 : 1.5,
    pointRadius: 0,
    fill: c === "I",
    tension: 0.3,
  }));

  state.chart = new Chart(ctx, {
    type: "line",
    data: { labels: t, datasets },
    options: {
      responsive: true,
      animation: { duration: 800, easing: "easeInOutQuart" },
      interaction: { mode:"index", intersect:false },
      plugins: {
        legend: { display:false },
        tooltip: {
          backgroundColor: "#0D1117",
          borderColor: "#2A3A4A",
          borderWidth: 1,
          titleColor: "#C8D4E0",
          bodyColor: "#C8D4E0",
          titleFont: { family:"DM Mono", size:11 },
          bodyFont:  { family:"DM Mono", size:11 },
          callbacks: {
            title: items => "Jour " + items[0].label,
            label: item  => ` ${item.dataset.label}: ${fmt(item.raw)}`,
          }
        }
      },
      scales: {
        x: {
          ticks: { color:"#4A5A6A", font:{family:"DM Mono",size:10},
                   maxTicksLimit:8 },
          grid: { color:"rgba(30,40,54,0.8)" },
        },
        y: {
          ticks: { color:"#4A5A6A", font:{family:"DM Mono",size:10},
                   callback: v => fmtK(v) },
          grid: { color:"rgba(30,40,54,0.8)" },
        }
      }
    }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// Ontologie
// ══════════════════════════════════════════════════════════════════════════════
async function loadOntologyData(diseaseName, modelId) {
  try {
    const promises = [];
    if (diseaseName) promises.push(
      fetch(`/api/ontology/disease/${encodeURIComponent(diseaseName)}`).then(r => r.json())
    );
    if (modelId) promises.push(
      fetch(`/api/ontology/model/${encodeURIComponent(modelId)}`).then(r => r.json())
    );

    const results = await Promise.all(promises);
    const ontoDisease = diseaseName ? results[0] : null;
    const ontoModel   = modelId    ? results[diseaseName ? 1 : 0] : null;

    showOntoPanel(ontoDisease, ontoModel);
  } catch(e) {
    console.warn("Ontologie non disponible:", e);
  }
}

function showOntoPanel(od, om) {
  const panel   = document.getElementById("ontoPanel");
  const content = document.getElementById("ontoPanelContent");
  if (!od && !om) return;

  let html = "";

  // ── Section maladie (M8) ──
  if (od && !od.error) {
    html += `<div style="margin-bottom:0.9rem">`;
    html += `<div style="font-size:0.62rem;color:var(--muted);font-family:var(--font-mono);
             margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.08em">
             Module M8 — Domaine Épidémiologique</div>`;

    // URI
    html += row("URI canonique",
      `<span class="onto-uri">${od.uri_m8 || "—"}</span>`);

    // ICD-10
    if (od.icd10)
      html += row("ICD-10", `<span class="onto-tag">${od.icd10}</span>`);

    // Transmission
    if (od.transmission_route)
      html += row("Transmission", od.transmission_route.replace(/_/g," "));

    // Formalismes recommandés
    if (od.recommended_formalisms?.length) {
      const tags = od.recommended_formalisms
        .map(f => `<span class="onto-tag">${f}</span>`).join(" ");
      html += row("Formalismes (M8)", tags);
    }

    // R₀ range
    if (od.r0_range)
      html += row("R₀ typique", `${od.r0_range[0]} – ${od.r0_range[1]}`);

    // Paramètres typiques
    if (od.typical_params && Object.keys(od.typical_params).length) {
      const pstr = Object.entries(od.typical_params)
        .map(([k,v]) => `${k}=${v}`).join("  ·  ");
      html += row("Params typiques", `<span style="font-family:var(--font-mono);font-size:0.68rem">${pstr}</span>`);
    }

    // Note
    if (od.ontological_note)
      html += `<div class="onto-note">${od.ontological_note}</div>`;

    html += `</div>`;
  }

  // ── Section modèle (M2 + M4) ──
  if (om && !om.error) {
    html += `<div style="border-top:1px solid var(--border);padding-top:0.8rem;margin-top:0.5rem">`;
    html += `<div style="font-size:0.62rem;color:var(--muted);font-family:var(--font-mono);
             margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.08em">
             Module M2 — Ontologie des Modèles</div>`;

    // URI M2
    html += row("URI modèle (M2)",
      `<span class="onto-uri" style="word-break:break-all">${om.uri_m2||"—"}</span>`);

    // URI M4
    if (om.uri_m4_simulation)
      html += row("URI simulation (M4)",
        `<span class="onto-uri" style="word-break:break-all">${om.uri_m4_simulation}</span>`);

    // Hiérarchie de classes
    if (om.class_hierarchy?.length) {
      const chain = om.class_hierarchy.map((c,i) =>
        `<span class="hier-node">${c}</span>` +
        (i < om.class_hierarchy.length-1 ? '<span class="hier-arrow">→</span>' : "")
      ).join("");
      html += row("Hiérarchie OWL", `<div class="hierarchy-chain">${chain}</div>`);
    }

    // Compartiments
    if (om.compartments?.length)
      html += row("Compartiments",
        om.compartments.map(c => `<span class="onto-tag">${c}</span>`).join(" "));

    // Cohérence
    const cok  = om.ontologically_consistent;
    const cico = cok
      ? `<span class="consistent-yes">✅ Cohérent avec M8</span>`
      : `<span class="consistent-no">⚠️  ${om.validation_report?.warning||"Formalisme non standard"}</span>`;
    html += row("Cohérence M2/M8", cico);

    html += `</div>`;
  }

  content.innerHTML = html;
  panel.classList.add("show");
}

function row(label, valHtml) {
  return `<div class="onto-row">
    <span class="onto-key">${label}</span>
    <span class="onto-val">${valHtml}</span>
  </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// Utils
// ══════════════════════════════════════════════════════════════════════════════
function fmt(n) {
  return Number(n).toLocaleString("fr-FR");
}
function fmtK(n) {
  if (n >= 1e9) return (n/1e9).toFixed(1)+"Md";
  if (n >= 1e6) return (n/1e6).toFixed(1)+"M";
  if (n >= 1e3) return (n/1e3).toFixed(0)+"k";
  return n;
}
function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3500);
}

// Start
init();
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES FLASK — proxy vers l'API FastAPI
# ══════════════════════════════════════════════════════════════════════════════

def api_get(path, params=None):
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=10)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 503


def api_post(path, body):
    try:
        r = requests.post(f"{API_BASE}{path}", json=body, timeout=15)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 503


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/ontology/disease/<disease_name>")
def onto_disease(disease_name):
    data, status = api_get(f"/ontology/disease/{disease_name}")
    return jsonify(data), status


@app.route("/api/ontology/model/<model_id>")
def onto_model(model_id):
    data, status = api_get(f"/ontology/model/{model_id}")
    return jsonify(data), status


@app.route("/api/ontology/stats")
def onto_stats():
    data, status = api_get("/ontology/stats")
    return jsonify(data), status


@app.route("/api/health")
def health():
    data, status = api_get("/")
    return jsonify(data), status


@app.route("/api/diseases")
def diseases():
    data, status = api_get("/diseases", {"with_models_only": "true"})
    return jsonify(data), status


@app.route("/api/models")
def models():
    params = {k: v for k, v in request.args.items()}
    data, status = api_get("/models/search", params)
    return jsonify(data), status


@app.route("/api/model/<model_id>/params")
def model_params(model_id):
    data, status = api_get(f"/models/{model_id}/params")
    return jsonify(data), status


@app.route("/api/simulate", methods=["POST"])
def simulate():
    body = request.get_json()
    data, status = api_post("/simulate", body)
    return jsonify(data), status


@app.route("/api/parse", methods=["POST"])
def parse_nlp():
    """Analyse NLP d'une phrase — retourne un intent structuré."""
    text = request.get_json().get("text", "")
    if not text:
        return jsonify({"error": "text requis"}), 400

    if _nlp:
        intent = _nlp.parse(text)
        return jsonify({
            "intent": {
                "disease_name":    intent.disease_name,
                "disease_fr":      intent.disease_fr,
                "country_code":    intent.country_code,
                "country_name":    intent.country_name,
                "N":               intent.N,
                "days":            intent.days,
                "I0":              intent.I0,
                "formalism":       intent.formalism,
                "population_type": intent.population_type,
                "confidence":      intent.confidence,
                "tokens_matched":  intent.tokens_matched,
            },
            "api_params": intent.to_api_params(),
            "summary":    intent.summary(),
        })
    else:
        # Fallback minimal sans NLP v2
        return jsonify({
            "intent": {"disease_name": text, "confidence": 0.3},
            "api_params": {"disease": text, "limit": 5},
            "summary": f"Recherche : {text}",
        })


# ══════════════════════════════════════════════════════════════════════════════
# LANCEMENT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001

    print(f"\n{'═'*55}")
    print(f"  🧬  PIPOnto — Interface de Simulation")
    print(f"{'═'*55}")
    print(f"  NLP v2   : {'✅ actif' if NLP_OK else '⚠️  non disponible'}")
    print(f"  API      : {API_BASE}")
    print(f"  → http://localhost:{port}")
    print(f"{'═'*55}\n")
    print(f"  Prérequis : l'API doit tourner :")
    print(f"  cd ~/piponto && uvicorn api.main:app --port 8000\n")

    app.run(debug=False, port=port, host="0.0.0.0")
