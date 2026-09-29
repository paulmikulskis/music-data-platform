{{ config(materialized='table', schema='marts', tags=['silver', 'cadence:daily', 'scope:tenant'], meta={'tenant_scoped': true}) }}
select *, {{ mdp_literal(mdp_context().cycle_id) }}::text as built_cycle
from {{ ref('lifecycle_tenant_staging') }}
