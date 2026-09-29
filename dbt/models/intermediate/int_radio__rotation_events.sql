{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

-- Recording × event: did a tastemaker station add or upgrade this track? Per recording, by airdate:
-- its first play, its rotation add (the first play in Light, Medium or Heavy rotation), and each upgrade
-- to a heavier rotation than any before. Plays without a recording MBID carry no event. An event counts only
-- once the landed history holds every one of the 90 days before it (the station airs every day, so a day
-- with no landed play is a day the history lacks): the first days after the key is enabled, or after a
-- backfill window lands apart from the rest, would otherwise read every track in rotation as an add.
-- Operator-only until the registry records KEXP's written permission.
{% set lookback_days = 90 %}
with plays as (
    select * from {{ ref('stg_kexp__plays') }} where recording_mbid is not null
), covered as (
    select distinct station, cast(airdate as date) as day from {{ ref('stg_kexp__plays') }}
), ordered as (
    select *,
        row_number() over (partition by station, recording_mbid order by airdate, play_id) as play_number,
        coalesce(max(rotation_rank) over (partition by station, recording_mbid order by airdate, play_id
                                          rows between unbounded preceding and 1 preceding), 0) as prior_rank
    from plays
), events as (
    select station, recording_mbid, airdate, play_id, rotation_status, rotation_rank, artist_mbids,
        release_group_mbid, 'first_play' as event_type
    from ordered where play_number = 1
    union all
    select station, recording_mbid, airdate, play_id, rotation_status, rotation_rank, artist_mbids,
        release_group_mbid, case when prior_rank = 0 then 'rotation_add' else 'rotation_upgrade' end
    from ordered where rotation_rank > prior_rank
), complete as (
    select e.*
    from events e
    where (
        select count(*) from covered c
        where c.station = e.station and c.day >= cast(e.airdate as date) - {{ lookback_days }}
            and c.day < cast(e.airdate as date)
    ) = {{ lookback_days }}
)
select
    cast(station as text) as station,
    cast(recording_mbid as text) as recording_mbid,
    cast(event_type as text) as event_type,
    cast(airdate as timestamp) as airdate,
    cast(play_id as bigint) as play_id,
    cast(rotation_status as text) as rotation_status,
    cast(artist_mbids as text) as artist_mbids,
    cast(release_group_mbid as text) as release_group_mbid,
    cast('["kexp_plays"]' as text) as _source_keys
from complete
