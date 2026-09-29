{{ config(materialized='table', tags=['bronze', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('bc_discover') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
{{ mdp_invoke('bc_discover', target_set='playlist') }}
