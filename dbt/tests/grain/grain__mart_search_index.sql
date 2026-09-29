-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select object_key, count(*) as rows_per_grain
from {{ ref('mart_search_index') }}
group by object_key
having count(*) > 1
