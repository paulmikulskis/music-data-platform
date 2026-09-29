-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select day, movement_list, rank, count(*) as rows_per_grain
from {{ ref('mart_top_movers') }}
group by day, movement_list, rank
having count(*) > 1
