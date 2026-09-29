{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global']) }}
-- One history clock per source family. A missing collection day adds no history.
with days as (
    select distinct day from {{ ref('mart_song_day') }} union select {{ mdp_cycle_day() }}
), observed as (
    select distinct 'playlists' as family, day from {{ ref('mart_song_day') }} where playlists_observed
    union select distinct 'shazam', day from {{ ref('mart_song_day') }} where shazam_observed
    union select distinct 'streams', day from {{ ref('mart_song_day') }} where stream_rate is not null
), families as (
    select 'playlists' as family union all select 'shazam' union all select 'streams'
)
select d.day, f.family, count(o.day) as history_days, min(o.day) as first_day,
    cast(least({{ mdp_movement_parameter('window_max_days') }}, greatest(0, count(o.day) - 1)) as integer) as window_days,
    -- Rate comparisons require two balanced halves, each with at least three rated days.
    cast(least({{ mdp_movement_parameter('window_max_days') }}, floor(count(o.day) / 2.0)) as integer) as rate_window_days
from days d cross join families f left join observed o on o.family = f.family and o.day <= d.day
group by 1, 2
