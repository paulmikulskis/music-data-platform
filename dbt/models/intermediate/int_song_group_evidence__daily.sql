{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}
-- These are the same list, chart and observed-counter counts as mart_song_day.
-- Keeping the count before grouping lets the served song history read grouped Billboard facts.
with observations as (
    select song_key, day, count(*) as n
    from {{ ref('int_song_followers__daily') }} group by 1, 2
    union all
    select song_key, chart_date, count(distinct chart)
    from {{ ref('int_song_shazam__daily') }} group by 1, 2
    union all
    select k.song_key, s.day, cast(1 as bigint)
    from {{ ref('mart_track_daily_streams') }} s
    join {{ ref('int_song_key__daily') }} k
        on k.platform = s.platform and k.platform_track_id = s.platform_track_id
    where s.count_status in ('changed', 'unchanged')
    group by 1, 2
)
select cast(song_key as text) as song_key, cast(sum(n) as bigint) as evidence_count
from observations where day <= {{ mdp_cycle_day() }} group by 1
