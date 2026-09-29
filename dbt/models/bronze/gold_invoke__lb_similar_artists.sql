{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('lb_similar_artists') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_lb__similarity_inputs') }}
{{ mdp_invoke('lb_similar_artists', input_relation=ref('int_lb__similarity_inputs')) }}
