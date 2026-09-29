-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select platform, playlist_id, variant, stream, occurrence_key, interval_id, count(*) as rows_per_grain
from {{ ref('mart_editorial_entries') }}
group by platform, playlist_id, variant, stream, occurrence_key, interval_id
having count(*) > 1
