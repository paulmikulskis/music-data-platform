{{ config(materialized='table', tags=['bronze', 'invoke', 'cadence:daily', 'scope:tenant'],
          pre_hook="{{ mdp_statement_timeout('lifecycle_tenant_probe') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily_tenant') }}
{{ mdp_invoke('lifecycle_tenant_probe') }}
