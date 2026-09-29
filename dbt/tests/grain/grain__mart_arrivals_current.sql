-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select song_key, count(*) as rows_per_grain
from {{ ref('mart_arrivals_current') }}
group by song_key
having count(*) > 1
