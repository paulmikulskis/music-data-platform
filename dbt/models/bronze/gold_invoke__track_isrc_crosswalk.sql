{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:hourly', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('track_isrc_crosswalk') }}") }}
-- depends_on: {{ ref('bronze_export__targets_hourly') }}
-- depends_on: {{ ref('int_identity__crosswalk_inputs') }}
{{ mdp_invoke('track_isrc_crosswalk', input_relation=ref('int_identity__crosswalk_inputs')) }}
