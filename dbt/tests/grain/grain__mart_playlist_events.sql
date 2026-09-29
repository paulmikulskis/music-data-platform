-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select platform, playlist_id, variant, stream, occurrence_key, interval_id, event_type, observed_at, count(*) as rows_per_grain
from {{ ref('mart_playlist_events') }}
group by platform, playlist_id, variant, stream, occurrence_key, interval_id, event_type, observed_at
having count(*) > 1
