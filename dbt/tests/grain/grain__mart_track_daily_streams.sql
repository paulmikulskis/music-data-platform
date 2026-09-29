-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select platform, platform_track_id, day, count(*) as rows_per_grain
from {{ ref('mart_track_daily_streams') }}
group by platform, platform_track_id, day
having count(*) > 1
