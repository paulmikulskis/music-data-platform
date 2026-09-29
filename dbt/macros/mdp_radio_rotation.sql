{# Rebucket plays and events before aggregation: a UTC week cannot be converted to a tenant week. #}
{% macro mdp_radio_rotation(tz) %}
with plays as (
    select station, recording_mbid, {{ mdp_local_week('airdate', tz) }} as week_start, rotation_rank,
        rotation_status, is_request, is_local, artist_mbids, release_group_mbid, airdate, _source_key
    from {{ ref('stg_kexp__plays') }}
    where recording_mbid is not null
), weekly as (
    select station, recording_mbid, week_start, count(*) as plays, max(rotation_rank) as top_rank,
        sum(case when is_request then 1 else 0 end) as requests, bool_or(coalesce(is_local, false)) as local,
        max(artist_mbids) as artist_mbids, max(release_group_mbid) as release_group_mbid,
        max(case when rotation_rank = 0 then rotation_status end) as other_status,
        {{ mdp_source_keys_agg('_source_key') }} as _source_keys
    from plays
    group by station, recording_mbid, week_start
), events as (
    select station, recording_mbid, {{ mdp_local_week('airdate', tz) }} as week_start,
        bool_or(event_type = 'first_play') as first_played, bool_or(event_type = 'rotation_add') as rotation_added,
        bool_or(event_type = 'rotation_upgrade') as rotation_upgraded
    from {{ ref('int_radio__rotation_events') }}
    group by station, recording_mbid, {{ mdp_local_week('airdate', tz) }}
), annotated as (
    select
        cast(w.station as text) as station,
        cast(w.recording_mbid as text) as recording_mbid,
        cast(w.week_start as date) as week_start,
        cast(w.plays as bigint) as plays,
        cast(case w.top_rank when 3 then 'Heavy' when 2 then 'Medium' when 1 then 'Light' else w.other_status end as text) as top_rotation,
        cast(coalesce(e.first_played, false) as boolean) as first_played,
        cast(coalesce(e.rotation_added, false) as boolean) as rotation_added,
        cast(coalesce(e.rotation_upgraded, false) as boolean) as rotation_upgraded,
        cast(w.artist_mbids as text) as artist_mbids,
        cast(w.release_group_mbid as text) as release_group_mbid,
        cast(w.requests as bigint) as requests,
        cast(w.local as boolean) as local,
        w._source_keys
    from weekly w
    left join events e on e.station = w.station and e.recording_mbid = w.recording_mbid and e.week_start = w.week_start
)
select * from annotated
{% endmacro %}
