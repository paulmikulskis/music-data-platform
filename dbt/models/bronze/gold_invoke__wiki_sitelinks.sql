{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('wiki_sitelinks') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_wiki__artist_qids') }}
{{ mdp_invoke('wiki_sitelinks', input_relation=ref('int_wiki__artist_qids')) }}
