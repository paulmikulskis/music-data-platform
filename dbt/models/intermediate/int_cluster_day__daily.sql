{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with grouped as (
    select c.cluster_key as song_key, s.day,
        max(case when s.playlists_observed then 1 else 0 end) = 1 as playlists_observed,
        max(case when s.shazam_observed then 1 else 0 end) = 1 as shazam_observed,
        max(case when s.streams_observed then 1 else 0 end) = 1 as streams_observed,
        sum(s.stream_rate) as stream_rate
    from {{ ref('mart_song_day') }} s join {{ ref('int_song_cluster__daily') }} c using (song_key)
    group by 1, 2
), charts as (
    select song_key, chart_date as day, count(*) as shazam_charts, sum(new_entry) as shazam_new_entries
    from {{ ref('int_cluster_shazam__daily') }} group by 1, 2
), names as (
    select song_key, max(title_text) as title_text, max(artist_text) as artist_text
    from {{ ref('int_song_key__daily') }} group by 1
), rights_inputs as (
    -- Keep all member writers across the captured history; aggregate each key set once.
    select distinct c.cluster_key as song_key, s.source_keys
    from {{ ref('mart_song_day') }} s join {{ ref('int_song_cluster__daily') }} c using (song_key)
    union all
    select distinct c.cluster_key, c.source_keys
    from {{ ref('int_song_cluster__daily') }} c
), rights as (
    select song_key, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from rights_inputs {{ mdp_json_elements('source_keys', 'k') }} group by 1
)
select g.*, n.title_text, n.artist_text,
    case when g.shazam_observed then coalesce(c.shazam_charts, 0) end as shazam_charts,
    c.shazam_new_entries, r.source_keys
from grouped g left join charts c using (song_key, day) left join names n using (song_key)
left join rights r using (song_key)
