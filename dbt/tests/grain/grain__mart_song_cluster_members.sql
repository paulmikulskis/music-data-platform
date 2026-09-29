-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select song_key, count(*) as rows_per_grain
from {{ ref('mart_song_cluster_members') }}
group by song_key
having count(*) > 1
