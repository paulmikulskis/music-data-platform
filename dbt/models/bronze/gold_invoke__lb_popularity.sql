{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('lb_popularity') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_lb__popularity_inputs') }}
{{ mdp_invoke('lb_popularity', input_relation=ref('int_lb__popularity_inputs')) }}
