-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select song_key, day, count(*) as rows_per_grain
from {{ ref('mart_song_day') }}
group by song_key, day
having count(*) > 1
