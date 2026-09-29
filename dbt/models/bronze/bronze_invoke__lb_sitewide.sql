{{ config(materialized='table', tags=['bronze', 'invoke', 'cadence:weekly', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('lb_sitewide') }}") }}
-- depends_on: {{ ref('bronze_export__targets_weekly') }}
{{ mdp_invoke('lb_sitewide') }}
