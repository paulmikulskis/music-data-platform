{{ config(materialized='table', tags=['bronze', 'close', 'cadence:__CADENCE__', 'scope:__SCOPE__']) }}
-- depends_on: {{ ref('__EXPORT__') }}
__DEPENDENCIES__
{{ mdp_invoke('cycle_close') }}
