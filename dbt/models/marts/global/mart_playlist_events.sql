{{ config(tags=['cadence:daily']) }}

-- Identity uses the bound daily close, even when an event's observation is older.
-- Local fixtures without a mirrored close use observation time, never query time.
with daily_close as (
    select max(coalesce(close_no, 0)) as close_no, max(closed_at) as closed_at
    from {{ source('raw','cycles') }}
    where cast(id as text)={{ mdp_literal(mdp_context().cycle_id) }}
        and cadence='daily' and scope='global'
), playlist_events as (
    {{ mdp_playlist_events(ref('int_playlist__observations'), ref('int_playlist__membership'), ref('int_playlist__snapshots')) }}
), annotated as (
    -- Track items carry this cycle's recording identity; release events stay item-level.
    select e.*, cast(i.isrc as text) as isrc, cast(i.mb_recording_gid as text) as mb_recording_gid,
        cast(i.isrc_method as text) as isrc_method, cast(i.recording_method as text) as recording_method,
        cast(i.recording_confidence as double precision) as confidence,
        cast(c.close_no as bigint) as daily_manifest_close_no,
        cast(coalesce({{ playlist_utc('c.closed_at') }}, e.observed_at) as timestamp) as as_of,
        r.source_keys as _source_keys
    from playlist_events e
    cross join daily_close c
    left join {{ ref('int_playlist__rights') }} r on r.snapshot_id=e.snapshot_id
    left join {{ ref('int_track_identity__daily') }} i
        on e.item_type='track' and i.platform=e.platform and i.platform_track_id=e.platform_track_id
)
{{ mdp_annotate('annotated') }}
