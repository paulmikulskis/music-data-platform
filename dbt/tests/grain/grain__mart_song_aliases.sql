-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select alias_key, count(*) as rows_per_grain
from {{ ref('mart_song_aliases') }}
group by alias_key
having count(*) > 1
