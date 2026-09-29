{{ config(tags=['cadence:daily']) }}

-- Track × UTC day: streams since the last update for the Spotify tracks on tracked editorial and
-- chart lists, from the play counts the playlist pages carry for their first 30 rows (paired with the embed
-- row at the same position). A track on several lists reads one counter; each read time takes its highest count.
with observations as (
    select o.platform_track_id,
        cast({{ playlist_utc('o.observed_at') }} as timestamp) as observed_at,
        max(o.playcount) as play_count,
        max(cast(s._run_id as text)) as run_id,
        max(cast(s._source_key as text)) as source_key
    from {{ ref('int_playlist__observations') }} o
    join {{ ref('stg_playlist__snapshots') }} s
        on s.platform = o.platform and s.playlist_id = o.playlist_id and s.variant = o.variant
        and s.snapshot_id = o.snapshot_id
    where o.platform = 'spotify' and o.platform_track_id is not null and o.playcount is not null
        and o.owner_class in ('editorial', 'chart')
    group by o.platform_track_id, cast({{ playlist_utc('o.observed_at') }} as timestamp)
), series as (
    {{ mdp_count_series('observations', ['platform_track_id']) }}
), sources as (
    select platform_track_id, {{ mdp_source_keys_agg('source_key') }} as _source_keys
    from observations group by platform_track_id
), streams as (
    select cast('spotify' as text) as platform, cast(s.platform_track_id as text) as platform_track_id,
        cast(s.day as date) as day, s.play_count, s.count_changed_at, s.streams_since_last_update,
        s.previous_count_changed_at, s.count_status, cast('playlist_page' as text) as count_source, s.observed_at,
        s._run_ids, k._source_keys
    from series s
    join sources k on k.platform_track_id = s.platform_track_id
)
{{ mdp_annotate('streams') }}
