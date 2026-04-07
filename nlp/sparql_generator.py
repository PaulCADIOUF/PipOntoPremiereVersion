"""
piponto_nlp/sparql_generator.py
================================
Génère des requêtes SPARQL à partir du résultat NLP.

Requêtes produites :
    Q1 — Modèles candidats (maladie + population + géo)
    Q2 — Paramètres publiés du meilleur modèle
    Q3 — Sources de données disponibles pour le contexte géographique
    Q4 — Provenance complète (traçabilité M6)
"""

from nlp_extractor import NLPExtractionResult

PREFIXES = """
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX m2:   <http://www.pacadi.org/these/piponto/module2#>
PREFIX m4:   <http://www.pacadi.org/these/piponto/module4#>
PREFIX m5:   <http://www.pacadi.org/these/piponto/module5#>
PREFIX m6:   <http://www.pacadi.org/these/piponto/module6#>
PREFIX m8:   <http://www.pacadi.org/these/piponto/module8#>
"""


class SPARQLQueryGenerator:
    """Génère les requêtes SPARQL depuis le résultat NLP."""

    def generate_all(self, result: NLPExtractionResult) -> dict[str, str]:
        """Retourne toutes les requêtes pertinentes."""
        queries = {}
        params = result.to_sparql_params()

        queries["Q1_model_candidates"] = self.q1_model_candidates(params)

        if result.candidate_model_uris:
            best_uri = result.candidate_model_uris[0]["uri"]
            queries["Q2_model_parameters"] = self.q2_model_parameters(best_uri)

        if params["geography_uri"]:
            queries["Q3_data_sources"] = self.q3_data_sources(
                params["geography_uri"]
            )

        queries["Q4_full_provenance"] = self.q4_provenance(params)

        return queries

    def q1_model_candidates(self, params: dict) -> str:
        """
        Q1 — Modèles candidats filtrés par maladie, population, géographie.
        Requête principale du pipeline NLP→M2.
        """
        disease_filter = ""
        pop_filter = ""
        geo_filter = ""

        if params["disease_uri"]:
            disease_filter = f"""
    # Filtre maladie
    ?disease a <{params['disease_uri']}> .
    ?model m8:bestModeledBy ?model .
    FILTER EXISTS {{ ?model m8:bestModeledBy ?disease }}"""

        if params["population_uri_m2"]:
            pop_filter = f"""
    # Filtre population (M2 PopulationContext)
    OPTIONAL {{
        ?model m2:hasTargetPopulation ?popCtx .
        FILTER (?popCtx = <{params['population_uri_m2']}>)
        BIND(0.10 AS ?popBonus)
    }}"""

        if params["geography_uri"]:
            geo_filter = f"""
    # Filtre géographie (M2 GeographicScope)
    OPTIONAL {{
        ?model m2:hasValidatedScope ?geoScope .
        FILTER EXISTS {{
            ?geoScope m8:isLocatedIn <{params['geography_uri']}>
        }}
        BIND(0.05 AS ?geoBonus)
    }}"""

        return f"""{PREFIXES}
# Q1 — Modèles M2 candidats pour la requête NLP
# Maladie   : {params.get('disease_uri', 'non spécifiée')}
# Population: {params.get('population_uri_m2', 'non spécifiée')}
# Géographie: {params.get('geography_uri', 'non spécifiée')}

SELECT ?model ?label ?formalism ?qualityScore
       (COALESCE(?popBonus, 0) + COALESCE(?geoBonus, 0) AS ?contextBonus)
       (?qualityScore + COALESCE(?popBonus, 0) + COALESCE(?geoBonus, 0)
        AS ?relevanceScore)
WHERE {{
    ?model a m2:Model ;
           rdfs:label ?label ;
           m2:hasFormalism ?formalism ;
           m2:hasQualityScore ?qualityScore .
{pop_filter}
{geo_filter}
    FILTER (?qualityScore > 0.7)
}}
ORDER BY DESC(?relevanceScore)
LIMIT 10
"""

    def q2_model_parameters(self, model_uri: str) -> str:
        """
        Q2 — Paramètres publiés du modèle sélectionné.
        Retourne valeurs, intervalles, formules.
        """
        return f"""{PREFIXES}
# Q2 — Paramètres publiés du modèle sélectionné
# Modèle : {model_uri}

SELECT ?param ?symbol ?defaultValue ?minValue ?maxValue ?unit
       ?isCalibrable ?formula
WHERE {{
    <{model_uri}> m2:hasParameter ?param .
    ?param m2:hasSymbol ?symbol ;
           m2:hasDefaultValue ?defaultValue .
    OPTIONAL {{ ?param m2:hasMinValue ?minValue }}
    OPTIONAL {{ ?param m2:hasMaxValue ?maxValue }}
    OPTIONAL {{ ?param m2:hasUnit ?unit }}
    OPTIONAL {{ ?param m2:isCalibratableParam ?isCalibrable }}
    OPTIONAL {{ ?param m2:hasFormula ?formula }}
}}
ORDER BY ?symbol
"""

    def q3_data_sources(self, geography_uri: str) -> str:
        """
        Q3 — Sources de données disponibles pour le territoire.
        """
        return f"""{PREFIXES}
# Q3 — Sources de données pour le contexte géographique
# Territoire : {geography_uri}

SELECT ?source ?label ?sourceType ?url ?format ?coverage
WHERE {{
    <{geography_uri}> m8:hasDataSource ?source .
    ?source rdfs:label ?label .
    OPTIONAL {{ ?source rdf:type ?sourceType }}
    OPTIONAL {{ ?source m8:hasSourceURL ?url }}
    OPTIONAL {{ ?source m8:hasDataFormat ?format }}
    OPTIONAL {{ ?source m8:hasCoverageArea ?coverage }}
}}
ORDER BY ?label
"""

    def q4_provenance(self, params: dict) -> str:
        """
        Q4 — Traçabilité complète (M6 SimulationProvenance).
        Permet de retrouver toutes les simulations passées similaires.
        """
        filters = []
        if params["geography_uri"]:
            filters.append(
                f"    FILTER (?geo = <{params['geography_uri']}>)"
            )
        if params["population_uri_m8"]:
            filters.append(
                f"    FILTER (?pop = <{params['population_uri_m8']}>)"
            )
        filter_block = "\n".join(filters)

        return f"""{PREFIXES}
# Q4 — Simulations passées similaires (traçabilité M6)
# Recherche de provenances existantes pour ce contexte

SELECT ?prov ?nlpQuery ?model ?modelLabel ?execTime ?score
WHERE {{
    ?prov a m6:SimulationProvenance ;
          m6:hasNLPQuery ?nlpQuery ;
          m6:usedModel ?model ;
          m6:hasModelSelectionScore ?score .
    ?model rdfs:label ?modelLabel .
    OPTIONAL {{
        ?prov m6:hasExecutionTimestamp ?execTime
    }}
    OPTIONAL {{ ?prov m6:simulatedGeography ?geo }}
    OPTIONAL {{ ?prov m6:simulatedPopulation ?pop }}
{filter_block}
}}
ORDER BY DESC(?score) DESC(?execTime)
LIMIT 5
"""
