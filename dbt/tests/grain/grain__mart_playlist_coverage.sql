-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select platform, playlist_id, variant, stream, observed_at, snapshot_id, count(*) as rows_per_grain
from {{ ref('mart_playlist_coverage') }}
group by platform, playlist_id, variant, stream, observed_at, snapshot_id
having count(*) > 1
