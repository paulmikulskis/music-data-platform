{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}
-- No table holds release dates, so age comes from proxies and never becomes a date.
-- An ISRC year code is the year the code was issued: a reissue can read newer, never older, so the oldest wins.
with tracks as (
    select song_key,
        cast({{ mdp_regex_extract('isrc', "^[A-Z]{2}[A-Z0-9]{3}([0-9]{2})[0-9]{5}$") }} as integer) as code_year,
        case when platform = 'apple' then cast({{ mdp_regex_extract('platform_track_id', '^([0-9]{1,15})$') }} as bigint) end as apple_song_id
    from {{ ref('int_song_key__daily') }}
), clock as (
    select cast(extract(year from {{ mdp_cycle_day() }}) as integer) as cycle_year
), songs as (
    select t.song_key, c.cycle_year,
        min(case when t.code_year <= mod(c.cycle_year, 100) then 2000 + t.code_year else 1900 + t.code_year end) as isrc_year,
        min(t.apple_song_id) as apple_song_id
    from tracks t cross join clock c group by 1, 2
), bands as (
    -- The dated seed holds the measured bands; its expiry test requires a new calendar-year review.
    select *, case when apple_song_id >= {{ mdp_song_age_parameter('apple_new_id_min') }} then {{ mdp_song_age_parameter('apple_new_year_min') }} end as apple_year_from,
        case when apple_song_id < {{ mdp_song_age_parameter('apple_catalog_id_max') }} then {{ mdp_song_age_parameter('apple_catalog_year_max') }} end as apple_year_to
    from songs
), aged as (
    select *, case when isrc_year is not null then case when isrc_year >= cycle_year - {{ mdp_song_age_parameter('isrc_new_max_years') }} then 'new' else 'catalog' end
            when apple_year_from >= cycle_year - {{ mdp_song_age_parameter('isrc_new_max_years') }} then 'new'
            when apple_year_to <= cycle_year - {{ mdp_song_age_parameter('isrc_new_max_years') }} - 1 then 'catalog'
            else 'unknown' end as age_class
    from bands
), stages as (
    -- A song takes its most established credited artist: "A feat. B" with B established reads established.
    -- It stays unknown until every credited artist has a lookup, so a partial week never reads emerging.
    select a.song_key,
        case when count(s.mb_artist_gid) < count(*) then 0
            else max(case s.artist_stage when 'established' then 3 when 'developing' then 2
                when 'emerging' then 1 else 0 end) end as stage_rank
    from {{ ref('int_song_artists__daily') }} a
    left join {{ ref('int_artist_stage__daily') }} s on s.mb_artist_gid = a.mb_artist_gid
    group by 1
)
select cast(g.song_key as text) as song_key, cast(g.isrc_year as integer) as isrc_year,
    cast(g.apple_song_id as bigint) as apple_song_id, cast(g.age_class as text) as age_class,
    cast(case when g.isrc_year is not null then 'isrc_year' when g.age_class <> 'unknown' then 'apple_id_band'
        else 'none' end as text) as age_basis,
    cast(case s.stage_rank when 3 then 'established' when 2 then 'developing' when 1 then 'emerging'
        else 'unknown' end as text) as artist_stage,
    cast(case when s.stage_rank > 0 then 'musicbrainz_catalog' else 'none' end as text) as artist_stage_basis
from aged g left join stages s on s.song_key = g.song_key
