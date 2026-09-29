{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
-- Playlist evidence comes from the same entries the score counts, so a chart add never appears as an add.
with streams as (
    select k.song_key, s.* from {{ ref('mart_track_daily_streams') }} s
    join {{ ref('int_song_key__daily') }} k on k.platform = s.platform and k.platform_track_id = s.platform_track_id
), shazam as (
    select k.song_key, s.* from {{ ref('mart_shazam_chart_daily') }} s
    join {{ ref('int_song_key__daily') }} k on k.platform = 'apple' and k.platform_track_id = s.apple_song_id
)
select song_key, cast(day as date) as day, 'playlist_adds' as component,
    cast({{ mdp_json_object([('component', "'playlist_adds'"), ('relation', "'mart_playlist_events'"),
        ('row_key', 'cast(' ~ mdp_json_get('locator', ['row_key']) ~ ' as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')'),
        ('window', 'cast(' ~ mdp_json_get('locator', ['window']) ~ ' as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')'),
        ('input_build', mdp_song_input_build('mart_playlist_events'))]) }} as text) as locator,
    list_kind
from {{ ref('int_song_entries__daily') }} where platform <> 'shazam' and list_kind in ('editorial', 'new_music', 'dsp_algorithmic')

union all
select song_key, day, 'follower_exposure_gain' as component,
    cast({{ mdp_json_object([('component', "'follower_exposure_gain'"), ('relation', "'mart_playlist_events'"),
        ('row_key', 'cast(' ~ mdp_json_get('locator', ['row_key']) ~ ' as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')'),
        ('window', 'cast(' ~ mdp_json_get('locator', ['window']) ~ ' as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')'),
        ('input_build', mdp_song_input_build('mart_playlist_events'))]) }} as text) as locator,
    list_kind
from {{ ref('int_song_entries__daily') }} where platform <> 'shazam' and followers > 0

union all
select e.song_key, e.day, 'follower_exposure_gain' as component,
    cast({{ mdp_json_object([('component', "'follower_exposure_gain'"), ('relation', "'mart_playlist_profile'"),
        ('row_key', mdp_json_object([('platform', 'p.platform'), ('playlist_id', 'p.playlist_id'), ('variant', 'p.variant'), ('stream', 'p.stream'), ('snapshot_id', 'p.snapshot_id')])),
        ('window', mdp_json_object([('start', mdp_timestamp_text('p.observed_at')), ('end', mdp_timestamp_text('p.observed_at'))])),
        ('input_build', mdp_song_input_build('mart_playlist_profile'))]) }} as text) as locator,
    e.list_kind
from {{ ref('int_song_entries__daily') }} e join {{ ref('mart_playlist_profile') }} p
    on p.platform = e.platform and p.playlist_id = e.list_id and p.snapshot_id = e.snapshot_id
where e.followers > 0 and p.followers = e.followers

union all
select song_key, cast(chart_date as date) as day, 'shazam_spread_gain' as component,
    cast({{ mdp_json_object([('component', "'shazam_spread_gain'"), ('relation', "'mart_shazam_chart_daily'"),
        ('row_key', mdp_json_object([('chart', 'chart'), ('chart_date', 'chart_date'), ('position', 'position')])),
        ('window', mdp_json_object([('start', 'cast(chart_date as text)'), ('end', 'cast(chart_date as text)')])),
        ('input_build', mdp_song_input_build('mart_shazam_chart_daily'))]) }} as text) as locator,
    cast(null as text) as list_kind
from shazam

union all
select song_key, cast(day as date) as day, 'stream_rate_gain' as component,
    cast({{ mdp_json_object([('component', "'stream_rate_gain'"), ('relation', "'mart_track_daily_streams'"),
        ('row_key', mdp_json_object([('platform', 'platform'), ('platform_track_id', 'platform_track_id'), ('day', 'day')])),
        ('window', mdp_json_object([('start', mdp_timestamp_text('previous_count_changed_at')), ('end', mdp_timestamp_text('count_changed_at'))])),
        ('input_build', mdp_song_input_build('mart_track_daily_streams'))]) }} as text) as locator,
    cast(null as text) as list_kind
from streams
