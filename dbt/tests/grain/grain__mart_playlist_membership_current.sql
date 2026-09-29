-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select platform, playlist_id, variant, occurrence_key, count(*) as rows_per_grain
from {{ ref('mart_playlist_membership_current') }}
group by platform, playlist_id, variant, occurrence_key
having count(*) > 1
