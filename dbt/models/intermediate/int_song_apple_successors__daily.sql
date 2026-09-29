{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
-- A switch needs adjacent calendar days. A missing chart day proves no replacement.
with observations as (
    select s.apple_song_id, s.chart, s.chart_date, s.position,
        max(s.position) over (partition by s.chart, s.chart_date) as chart_size,
        {{ mdp_fold_name('s.title_text') }} as title,
        {{ mdp_fold_name('s.artist_text') }} as credit,
        nullif(s.apple_primary_artist_id, '') as artist_id,
        nullif(s.isrc, '') as isrc, k.song_key, nullif(k.isrc, '') as identity_isrc
    from {{ ref('mart_shazam_chart_daily') }} s
    join {{ ref('int_song_key__daily') }} k
        on k.platform = 'apple' and k.platform_track_id = s.apple_song_id
), lifetimes as (
    -- An id returning after a gap is not a new successor. Concurrent copies stay separate.
    select apple_song_id, min(chart_date) as first_day, max(chart_date) as last_day
    from observations group by 1
), codes as (
    select apple_song_id, isrc from observations where isrc is not null
    union select apple_song_id, identity_isrc from observations where identity_isrc is not null
), pairs as (
    select distinct a.apple_song_id as predecessor_id, b.apple_song_id as successor_id,
        a.song_key as predecessor_key, b.song_key as successor_key, b.chart_date as switch_day
    from observations a join observations b
        on a.chart = b.chart and b.chart_date = a.chart_date + 1
        and a.apple_song_id <> b.apple_song_id
        and a.title = b.title and a.title <> ''
        -- Full credits must agree even with an equal primary id: a guest can change the song.
        and a.credit = b.credit and a.credit <> ''
        and (a.artist_id = b.artist_id or (a.artist_id is null and b.artist_id is null))
    join lifetimes old on old.apple_song_id = a.apple_song_id and old.last_day = a.chart_date
    join lifetimes fresh on fresh.apple_song_id = b.apple_song_id and fresh.first_day = b.chart_date
    where abs(a.position - b.position) <= {{ mdp_movement_parameter('apple_successor_position_fraction') }}
            * least(a.chart_size, b.chart_size)
        and not exists (
            select 1 from codes x join codes y on x.isrc <> y.isrc
            where x.apple_song_id in (a.apple_song_id, b.apple_song_id)
                and y.apple_song_id in (a.apple_song_id, b.apple_song_id)
        )
), unique_pairs as (
    -- Count ids before collapsing strict keys. Two plausible copies never become one match.
    select *, count(*) over (partition by predecessor_id) as successor_count,
        count(*) over (partition by successor_id) as predecessor_count
    from pairs
)
select predecessor_id, successor_id, predecessor_key, successor_key, switch_day
from unique_pairs where successor_count = 1 and predecessor_count = 1
