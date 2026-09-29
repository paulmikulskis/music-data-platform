{{ config(materialized='table', tags=['bronze', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('sp_playlist') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
{{ mdp_invoke('sp_playlist', target_set='playlist') }}
