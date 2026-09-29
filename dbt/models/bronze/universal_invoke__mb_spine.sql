{{ config(materialized='table', tags=['universal', 'invoke', 'cadence:weekly', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('mb_spine') }}") }}
-- depends_on: {{ ref('bronze_export__targets_weekly') }}
-- depends_on: {{ ref('int_identity__track_inputs') }}
{{ mdp_invoke('mb_spine', input_relation=ref('int_identity__track_inputs')) }}
