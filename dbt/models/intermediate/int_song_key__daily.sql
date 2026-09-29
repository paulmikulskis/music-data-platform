{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with inputs as (select * from {{ ref('int_identity__track_inputs_daily') }}),
tracks as (
    select {{ mdp_song_platform('platform') }} as platform, platform_track_id,
        title as title_text,
        (select string_agg(a.value, ', ' order by a.value) from (select artist_names as names) n
            {{ mdp_json_elements('n.names', 'a') }}) as artist_text, platform_isrc as isrc, {{ mdp_song_first_artist('platform_artist_ids') }} as primary_artist_id, _source_keys as source_keys
    from inputs
    union all
    select 'apple', apple_song_id, title_text, artist_text, isrc, apple_primary_artist_id,
        {{ mdp_source_keys(['_source_key']) }}
    from {{ ref('stg_shazam__chart_entries') }} where apple_song_id is not null
    union all
    select {{ mdp_song_platform('platform') }}, platform_track_id, null, null, null, null, source_keys
    from {{ ref('mart_track_daily_streams') }}
), grouped as (
    select platform, platform_track_id, max(title_text) as title_text, max(artist_text) as artist_text,
        max(primary_artist_id) as primary_artist_id,
        case when count(distinct isrc) = 1 then max(isrc) end as isrc
    from tracks group by 1, 2
), rights as (
    select platform, platform_track_id, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from tracks {{ mdp_json_elements('source_keys', 'k') }} group by 1, 2
), keyed as (
    select t.*, coalesce(i.isrc, t.isrc) as chosen_isrc, i.mb_recording_gid,
        i.recording_method, i.recording_confidence, r.source_keys,
        case when a.candidate_count = 1 then a.mb_artist_gid end as mb_artist_gid
    from grouped t
    left join {{ ref('int_track_identity__daily') }} i
        on {{ mdp_song_platform('i.platform') }} = t.platform and i.platform_track_id = t.platform_track_id
    left join {{ ref('int_artist_identity') }} a
        on {{ mdp_song_platform('a.platform') }} = t.platform and a.platform_artist_id = t.primary_artist_id
    left join rights r on r.platform = t.platform and r.platform_track_id = t.platform_track_id
)
select cast(platform as text) as platform, cast(platform_track_id as text) as platform_track_id,
    cast(coalesce(mb_recording_gid, 'isrc:' || chosen_isrc, platform || ':' || platform_track_id) as text) as song_key,
    cast(chosen_isrc as text) as isrc, cast(mb_recording_gid as text) as mb_recording_gid,
    cast(coalesce(recording_method, case when chosen_isrc is not null then 'isrc' else 'platform' end) as text) as match_method,
    cast(title_text as text) as title_text, cast(artist_text as text) as artist_text,
    cast(source_keys as text) as source_keys, cast(mb_recording_gid is not null as boolean) as resolved,
    cast(recording_confidence as double precision) as confidence,
    cast(primary_artist_id as text) as primary_artist_id,
    cast(coalesce(mb_artist_gid, platform || ':' || primary_artist_id) as text) as primary_artist_key
from keyed
