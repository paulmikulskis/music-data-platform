{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

-- Whether the ListenBrainz statistics refreshed: one row per weekly sitewide fetch, with the
-- statistics run it saw (last_updated, from the artist list) and whether that run is newer than the one
-- the previous fetch saw. The upstream job sometimes skips a week; a week whose fetch saw no newer run
-- reads "not refreshed", and mart_open_listening crosses no rule in it. It is built daily from the
-- weekly landings in the daily manifest, so a tenant daily job always finds it.
with landed as (
    select * from {{ source('raw', 'lb_sitewide') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.lb_sitewide') }} and entity_type = 'artist'
), fetches as (
    select cast(_run_id as text) as run_id, min({{ playlist_utc('observed_at') }}) as observed_at,
        max({{ playlist_utc('last_updated') }}) as last_updated,
        max({{ playlist_utc('window_start') }}) as window_start, max({{ playlist_utc('window_end') }}) as window_end,
        {{ mdp_source_keys_agg('_source_key') }} as _source_keys
    from landed
    group by cast(_run_id as text)
), ordered as (
    select *, lag(last_updated) over (order by observed_at, run_id) as previous_last_updated
    from fetches
)
select
    {{ mdp_local_week('observed_at', 'UTC') }} as week_start,
    cast(observed_at as timestamp) as observed_at,
    cast(last_updated as timestamp) as last_updated,
    cast(previous_last_updated as timestamp) as previous_last_updated,
    cast(window_start as timestamp) as window_start,
    cast(window_end as timestamp) as window_end,
    cast(previous_last_updated is null or last_updated > previous_last_updated as boolean) as refreshed,
    cast(run_id as text) as run_id, _source_keys
from ordered
