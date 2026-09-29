{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:hourly', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('mb_resolve') }}") }}
-- depends_on: {{ ref('bronze_export__targets_hourly') }}
-- depends_on: {{ ref('int_identity__resolution_inputs') }}
{{ mdp_invoke('mb_resolve', input_relation=ref('int_identity__resolution_inputs')) }}
