{{ config(materialized='table', tags=['bronze', 'close', 'cadence:weekly', 'scope:global']) }}
-- depends_on: {{ ref('bronze_export__targets_weekly') }}
-- depends_on: {{ ref('bronze_invoke__billboard_hot100') }}
-- depends_on: {{ ref('bronze_invoke__lb_sitewide') }}
{{ mdp_invoke('cycle_close') }}
