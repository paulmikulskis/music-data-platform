{{ config(tags=['cadence:daily']) }}
with events as ({{ mdp_song_events() }}),
event_lists as (
    select song_key, day, platform, playlist_id,
        max(case when event_type in ('add', 'entered_head') and owner_class = 'editorial'
            then case when event_type = 'entered_head' then {{ mdp_movement_parameter('head_confidence') }} else 1.0 end else 0 end) as editorial_adds,
        max(case when event_type in ('add', 'entered_head') and owner_class = 'dsp_algorithmic'
            then case when event_type = 'entered_head' then {{ mdp_movement_parameter('head_confidence') }} else 1.0 end else 0 end) as algorithmic_adds,
        max(case when event_type = 'remove' then 1 else 0 end) as removes
    from events group by 1, 2, 3, 4
), event_days as (
    select song_key, day, sum(editorial_adds) as editorial_adds,
        sum(algorithmic_adds) as algorithmic_adds, sum(removes) as removes
    from event_lists group by 1, 2
), collection_days as (
    select distinct {{ mdp_song_platform('platform') }} as platform,
        cast({{ playlist_utc('observed_at') }} as date) as day
    from {{ ref('int_playlist__snapshots') }}
), collected as (
    select distinct k.song_key, s.day
    from collection_days s join {{ ref('int_song_key__daily') }} k on k.platform = s.platform
), followers as (
    select song_key, day, sum(followers) as playlist_followers, count(*) as list_count, min(best_position) as best_position
    from {{ ref('int_song_followers__daily') }} group by 1, 2
), shazam as (
    select song_key, chart_date as day,
        count(distinct case when city is not null and city <> '' then chart end) as shazam_cities,
        count(distinct country) as shazam_countries, count(distinct chart) as shazam_charts,
        min(position) as shazam_best_position,
        sum(new_entry) as shazam_new_entries
    from {{ ref('int_song_shazam__daily') }} group by 1, 2
) , shazam_days as (
    select distinct chart_date as day from {{ ref('int_song_shazam__daily') }}
), shazam_collected as (
    select distinct k.song_key, s.day from shazam_days s cross join {{ ref('int_song_key__daily') }} k
), counters as (
    select k.song_key, s.*,
        cast({{ dbt.datediff('previous_count_changed_at', 'count_changed_at', 'second') }} as double precision) / 3600.0 as interval_hours,
        lag(day) over (partition by s.platform, s.platform_track_id order by day) as previous_day,
        lag(count_status) over (partition by s.platform, s.platform_track_id order by day) as previous_status,
        max(case when count_status in ('no_fetch', 'no_count') then day end) over (
            partition by s.platform, s.platform_track_id order by day rows unbounded preceding) as last_gap_day
    from {{ ref('mart_track_daily_streams') }} s
    join {{ ref('int_song_key__daily') }} k on k.platform = s.platform and k.platform_track_id = s.platform_track_id
), streams as (
    select song_key, day,
        sum(case when count_status = 'changed' and streams_since_last_update >= 0 and interval_hours between {{ mdp_movement_parameter('stream_interval_min_hours') }} and {{ mdp_movement_parameter('stream_interval_max_hours') }}
            and previous_day = day - 1 and previous_status in ('changed', 'unchanged')
            and (last_gap_day is null or last_gap_day < cast(previous_count_changed_at as date))
            then streams_since_last_update end) as stream_rate,
        max(interval_hours) as stream_interval_hours,
        max(case when count_status in ('changed', 'unchanged') then 1 else 0 end) = 1 as observed
    from counters group by 1, 2
), billboard_entries as (
    select c.song_key, b.chart_week as day, b.chart_position, b.weeks_on_chart, b.is_debut,
        b.source_keys,
        row_number() over (partition by c.song_key, b.chart_week order by b.chart_position) as pick
    from {{ ref('int_billboard_song__daily') }} b
    join {{ ref('int_song_cluster__daily') }} c on c.cluster_key = b.song_key
), billboard as (
    select * from billboard_entries where pick = 1
), days as (
    select song_key, day from shazam_collected union select song_key, day from collected union select song_key, day from event_days
    union select song_key, day from followers union select song_key, day from shazam
    union select song_key, day from streams
    union select song_key, day from billboard
), names as (
    select song_key, max(title_text) as title_text, max(artist_text) as artist_text
    from {{ ref('int_song_key__daily') }} group by 1
), input_rights as (
    select song_key, day, source_keys from {{ ref('int_song_followers__daily') }}
    union all select song_key, day, source_keys from events
    union all select song_key, chart_date, source_keys from {{ ref('int_song_shazam__daily') }}
    union all select song_key, day, source_keys from counters
    union all select song_key, day, source_keys from billboard_entries
), rights as (
    select song_key, day, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from input_rights {{ mdp_json_elements('source_keys', 'k') }} group by 1, 2
), rows as (
    select d.song_key, cast(d.day as date) as day, n.title_text, n.artist_text,
        cast(case when c.day is not null then coalesce(e.editorial_adds, 0) else e.editorial_adds end as double precision) as editorial_adds,
        cast(case when c.day is not null then coalesce(e.algorithmic_adds, 0) else e.algorithmic_adds end as double precision) as algorithmic_adds,
        cast(case when c.day is not null then coalesce(e.removes, 0) else e.removes end as bigint) as removes,
        cast(case when f.list_count is null and c.day is not null then 0 else f.playlist_followers end as bigint) as playlist_followers, cast(f.list_count as bigint) as list_count,
        cast(f.best_position as integer) as best_position, cast(case when sc.day is not null then coalesce(s.shazam_cities, 0) end as bigint) as shazam_cities,
        cast(case when sc.day is not null then coalesce(s.shazam_countries, 0) end as bigint) as shazam_countries, cast(case when sc.day is not null then coalesce(s.shazam_charts, 0) end as bigint) as shazam_charts,
        cast(s.shazam_best_position as integer) as shazam_best_position, cast(s.shazam_new_entries as bigint) as shazam_new_entries,
        cast(c.day is not null or e.day is not null or f.day is not null as boolean) as playlists_observed,
        cast(sc.day is not null as boolean) as shazam_observed,
        cast(coalesce(t.observed, false) as boolean) as streams_observed,
        cast(t.stream_rate as double precision) as stream_rate, cast(t.stream_interval_hours as double precision) as stream_interval_hours,
        cast(b.chart_position as integer) as billboard_position,
        cast(b.weeks_on_chart as integer) as billboard_weeks_on_chart,
        cast(b.is_debut as boolean) as billboard_debut,
        {{ mdp_source_keys(arrays=['r.source_keys', 'identity_rights.source_keys']) }} as _source_keys
    from days d left join names n using (song_key)
    left join shazam_collected sc using (song_key, day)
    left join collected c using (song_key, day) left join event_days e using (song_key, day)
    left join followers f using (song_key, day) left join shazam s using (song_key, day)
    left join billboard b using (song_key, day)
    left join streams t using (song_key, day) left join rights r using (song_key, day)
    left join (
        select song_key, {{ mdp_source_keys_agg('k.value') }} as source_keys
        from {{ ref('int_song_key__daily') }} {{ mdp_json_elements('source_keys', 'k') }} group by 1
    ) identity_rights using (song_key)
    where d.day <= {{ mdp_cycle_day() }}
)
{{ mdp_annotate('rows') }}
