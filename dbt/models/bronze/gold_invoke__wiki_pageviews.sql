{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('wiki_pageviews') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_wiki__articles') }}
{{ mdp_invoke('wiki_pageviews', input_relation=ref('int_wiki__articles')) }}
