{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with chart_days as (
    select distinct chart, chart_date from {{ ref('int_song_shazam__daily') }}
), coverage as (
    select chart, chart_date, count(*) over (partition by chart order by cast(chart_date as timestamp)
        range between (interval '1 day' * cast({{ mdp_movement_parameter('shazam_history_days') }} - 1 as integer)) preceding and current row) as observed_days from chart_days
), grouped as (
    select c.cluster_key as song_key, s.chart, s.chart_date, max(s.country) as country, max(s.city) as city,
        min(s.position) as position
    from {{ ref('int_song_shazam__daily') }} s join {{ ref('int_song_cluster__daily') }} c using (song_key)
    group by 1, 2, 3
), previous as (
    select *, lag(chart_date) over (partition by song_key, chart order by chart_date) as previous_date from grouped
), rights as (
    select c.cluster_key as song_key, s.chart, s.chart_date, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from {{ ref('int_song_shazam__daily') }} s join {{ ref('int_song_cluster__daily') }} c using (song_key)
    {{ mdp_json_elements('s.source_keys', 'k') }} group by 1, 2, 3
)
select s.song_key, s.chart, s.chart_date, s.country, s.city, s.position, r.source_keys,
    case when c.observed_days >= {{ mdp_movement_parameter('shazam_min_observed_days') }} then case when s.previous_date is null or s.previous_date < s.chart_date - cast({{ mdp_movement_parameter('shazam_history_days') }} - 1 as integer)
        then 1 else 0 end end as new_entry
from previous s join coverage c using (chart, chart_date) left join rights r using (song_key, chart, chart_date)
