{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with tracks as (select * from {{ ref('int_song_cluster_inputs__daily') }}),
codes as (
    select distinct song_key, isrc, false as crosswalk, platform = 'apple' or resolved as eligible_target
    from tracks where isrc is not null and isrc <> ''
    union select distinct song_key, crosswalk_isrc, true, false from tracks where crosswalk_isrc is not null
), isrc_pairs as (
    select a.song_key as left_key, b.song_key as right_key, 'isrc_crosswalk' as method,
        case when a.crosswalk or b.crosswalk then 'crosswalk' else 'same_isrc' end as basis
    from codes a join codes b on a.isrc = b.isrc and a.song_key < b.song_key
    where (not a.crosswalk and not b.crosswalk)
        or (a.crosswalk and not b.crosswalk and b.eligible_target)
        or (b.crosswalk and not a.crosswalk and a.eligible_target)
), variants as (
    select a.song_key as left_key, b.song_key as right_key, 'apple_variant' as method, 'metadata' as basis
    from tracks a join tracks b on a.platform = 'apple' and b.platform = 'apple' and a.song_key < b.song_key
        and a.folded_title = b.folded_title and a.folded_title <> ''
        and a.primary_artist_id = b.primary_artist_id and a.primary_artist_id is not null
        and {{ mdp_fold_name('a.artist_text') }} = {{ mdp_fold_name('b.artist_text') }}
    join {{ ref('song_cluster_rules') }} r on r.method = 'apple_variant'
    where a.duration_ms > 0 and b.duration_ms > 0 and abs(a.duration_ms - b.duration_ms) <= r.duration_tolerance_ms
), cross_platform as (
    select distinct a.song_key as apple_key, b.song_key as spotify_key
    from tracks a join tracks b on a.platform = 'apple' and b.platform = 'spotify'
        and a.folded_title = b.folded_title and a.folded_title <> ''
        and a.folded_artist = b.folded_artist and a.folded_artist <> ''
    join {{ ref('song_cluster_rules') }} r on r.method = 'title_artist_duration'
    where a.duration_ms > 0 and b.duration_ms > 0 and abs(a.duration_ms - b.duration_ms) <= r.duration_tolerance_ms
), unique_pairs as (
    select *, count(*) over (partition by apple_key) as apple_matches,
        count(*) over (partition by spotify_key) as spotify_matches from cross_platform
)
select distinct left_key, right_key, method, basis from isrc_pairs
union select distinct left_key, right_key, method, basis from variants
union select least(apple_key, spotify_key), greatest(apple_key, spotify_key), 'title_artist_duration', 'metadata'
from unique_pairs where apple_matches = 1 and spotify_matches = 1 and apple_key <> spotify_key

union select least(predecessor_key, successor_key), greatest(predecessor_key, successor_key),
    'apple_id_successor', 'chart_replacement'
from {{ ref('int_song_apple_successors__daily') }} where predecessor_key <> successor_key
