{{ config(materialized='view', schema='staging', tags=['bronze', 'cadence:daily', 'scope:tenant']) }}
-- depends_on: {{ ref('bronze_invoke__lifecycle_tenant_probe') }}
-- depends_on: {{ ref('bronze_close__daily_tenant') }}
select * from raw.lifecycle_tenant_probe
where tenant_id::text = {{ mdp_literal(env_var('DBT_MDP_SCOPE').replace('tenant:', '')) }}
  and {{ mdp_context().manifest_filter('_dump_id', 'raw.lifecycle_tenant_probe') }}
