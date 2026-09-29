-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select family, count(*) as rows_per_grain
from {{ ref('mart_readiness') }}
group by family
having count(*) > 1
