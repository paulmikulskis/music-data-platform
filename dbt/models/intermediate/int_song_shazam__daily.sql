{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with chart_days as (
    select distinct chart, chart_date from {{ ref('mart_shazam_chart_daily') }}
), coverage as (
    select chart, chart_date, count(*) over (partition by chart order by cast(chart_date as timestamp)
        range between (interval '1 day' * cast({{ mdp_movement_parameter('shazam_history_days') }} - 1 as integer)) preceding and current row) as observed_days
    from chart_days
), entries as (
    select k.song_key, s.* from {{ ref('mart_shazam_chart_daily') }} s
    join {{ ref('int_song_key__daily') }} k on k.platform = 'apple' and k.platform_track_id = s.apple_song_id
), chart_songs as (
    select song_key, chart, chart_date, max(country) as country, max(city) as city, min(position) as position
    from entries group by 1, 2, 3
), previous as (
    select *, lag(chart_date) over (partition by song_key, chart order by chart_date) as previous_date
    from chart_songs
), rights as (
    select song_key, chart, chart_date, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from entries {{ mdp_json_elements('source_keys', 'k') }} group by 1, 2, 3
)
select s.song_key, s.chart, s.chart_date, s.country, s.city, s.position, r.source_keys,
    case when c.observed_days >= {{ mdp_movement_parameter('shazam_min_observed_days') }}
        then case when s.previous_date is null or s.previous_date < s.chart_date - cast({{ mdp_movement_parameter('shazam_history_days') }} - 1 as integer) then 1 else 0 end end as new_entry
from previous s join coverage c using (chart, chart_date)
join rights r using (song_key, chart, chart_date)
