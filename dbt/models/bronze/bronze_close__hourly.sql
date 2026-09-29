{{ config(materialized='table', tags=['bronze', 'close', 'cadence:hourly', 'scope:global']) }}
-- depends_on: {{ ref('bronze_export__targets_hourly') }}

{{ mdp_invoke('cycle_close') }}
