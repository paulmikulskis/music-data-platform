-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select movement_list, family, rank, count(*) as rows_per_grain
from {{ ref('mart_early_signals_current') }}
group by movement_list, family, rank
having count(*) > 1
