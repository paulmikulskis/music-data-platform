{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with events as ({{ mdp_song_events() }}), profiles as (
    select platform, playlist_id, snapshot_id, max(followers) as followers
    from {{ ref('mart_playlist_profile') }} group by 1, 2, 3
), chart_days as (
    select chart, chart_date, lag(chart_date) over (partition by chart order by chart_date) as previous_day
    from (select distinct chart, chart_date from {{ ref('int_song_shazam__daily') }}) d
), playlist_entries as (
    select e.song_key, e.day, e.observed_at, e.platform, e.playlist_id as list_id, e.snapshot_id,
        e.owner_class,
        -- owner_class stays the platform's label. list_kind is movement's reading: the seed refines a list the
        -- platform labels editorial or chart, and never turns a user or algorithmic list into one that counts.
        cast({{ mdp_song_list_kind('e.owner_class', 't.list_kind') }} as text) as list_kind,
        e.event_type,
        cast(case when e.event_type = 'entered_head' then {{ mdp_movement_parameter('head_confidence') }} else 1.0 end as double precision) as confidence,
        p.followers,
        cast(coalesce(t.reach_tier, case when p.followers >= {{ mdp_movement_parameter('tier_one_followers') }} then 1 when p.followers >= {{ mdp_movement_parameter('tier_two_followers') }} then 2
            when p.followers is not null then 3 else 4 end) as integer) as list_reach_tier,
        -- Only the seed names a list's country. The fetch storefront is not a market.
        cast(nullif(nullif(nullif(upper(t.market), 'GLOBAL'), 'WORLD'), '') as text) as market,
        e.source_keys,
        cast({{ mdp_json_object([('component', "'arrivals'"), ('relation', "'mart_playlist_events'"),
            ('row_key', mdp_json_object([('platform', 'e.platform'), ('playlist_id', 'e.playlist_id'), ('variant', 'e.variant'), ('stream', 'e.stream'), ('occurrence_key', 'e.occurrence_key'), ('interval_id', 'e.interval_id'), ('event_type', 'e.event_type'), ('observed_at', 'e.observed_at')])),
            ('window', mdp_json_object([('start', mdp_timestamp_text('e.entered_after')), ('end', mdp_timestamp_text('e.observed_at'))])),
            ('input_build', mdp_song_input_build('mart_playlist_events'))]) }} as text) as locator
    from events e left join profiles p using (platform, playlist_id, snapshot_id)
    left join {{ ref('playlist_reach_tiers') }} t using (platform, playlist_id)
    where e.event_type in ('add', 'entered_head') and e.owner_class in ('editorial', 'chart', 'dsp_algorithmic')
), shazam_entries as (
    select s.song_key, s.chart_date as day, cast(s.chart_date as timestamp with time zone) as observed_at,
        'shazam' as platform, s.chart as list_id, cast(null as text) as snapshot_id, 'chart' as owner_class,
        'chart' as list_kind, 'chart_entry' as event_type,
        cast(1 as double precision) as confidence, cast(null as bigint) as followers, 4 as list_reach_tier,
        nullif(nullif(nullif(upper(s.country), 'GLOBAL'), 'WORLD'), '') as market, s.source_keys,
        cast({{ mdp_json_object([('component', "'arrivals'"), ('relation', "'mart_shazam_chart_daily'"),
            ('row_key', mdp_json_object([('chart', 's.chart'), ('chart_date', 's.chart_date'), ('position', 's.position')])),
            ('window', mdp_json_object([('start', 'cast(d.previous_day as text)'), ('end', 'cast(s.chart_date as text)')])),
            ('input_build', mdp_song_input_build('mart_shazam_chart_daily'))]) }} as text) as locator
    from {{ ref('int_song_shazam__daily') }} s join chart_days d using (chart, chart_date)
    left join {{ ref('int_song_shazam__daily') }} prior on prior.song_key = s.song_key
        and prior.chart = s.chart and prior.chart_date = d.previous_day
    where d.previous_day is not null and prior.song_key is null
)
select * from playlist_entries union all select * from shazam_entries
