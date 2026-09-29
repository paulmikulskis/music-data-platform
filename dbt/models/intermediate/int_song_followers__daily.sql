{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with rows as (
    select k.song_key, d.* from {{ ref('int_playlist__track_days') }} d
    join {{ ref('int_song_key__daily') }} k
        on k.platform = {{ mdp_song_platform('d.platform') }} and k.platform_track_id = d.platform_track_id
), counts as (
    select song_key, day, {{ mdp_song_platform('platform') }} as platform, playlist_id,
        max(owner_class) as owner_class, max(followers) as followers, min(best_position) as best_position
    from rows group by 1, 2, 3, 4
), rights as (
    select song_key, day, {{ mdp_song_platform('platform') }} as platform, playlist_id,
        {{ mdp_source_keys_agg('k.value') }} as source_keys
    from rows {{ mdp_json_elements('source_keys', 'k') }} group by 1, 2, 3, 4
)
select cast(c.song_key as text) as song_key, cast(c.day as date) as day, cast(c.platform as text) as platform,
    cast(c.playlist_id as text) as playlist_id, cast(c.owner_class as text) as owner_class,
    cast(c.followers as bigint) as followers, cast(c.best_position as integer) as best_position, r.source_keys
from counts c left join rights r using (song_key, day, platform, playlist_id)
