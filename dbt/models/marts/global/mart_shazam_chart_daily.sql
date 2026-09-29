{{ config(tags=['cadence:daily']) }}

-- Chart × chart date × position: which songs, by Apple song id and primary artist id, are on
-- each country, city and discovery chart. Reads raw.shazam_chart_entries only; the daily track
-- identity joins by Apple song id for the recording, never by name. A song on no tracked playlist has
-- no identity row yet and serves with its ids alone.
with entries as (
    select s.*, m.country as iso_country,
        (s.artist_text like '%, %' or s.artist_text like '% & %') as multi_artist_credit,
        i.isrc as identity_isrc, i.isrc_method, i.mb_recording_gid, i.recording_method, i.recording_confidence
    from {{ ref('stg_shazam__chart_entries') }} s
    left join {{ ref('shazam_markets') }} m
        on lower(s.country) = m.slug or upper(s.country) = m.country
    left join {{ ref('int_track_identity__daily') }} i
        on i.platform = 'apple_music' and i.platform_track_id = s.apple_song_id
), annotated as (
    select
        cast(chart as text) as chart,
        cast(chart_type as text) as chart_type,
        cast(iso_country as text) as country,
        cast(city as text) as city,
        cast(chart_date as date) as chart_date,
        cast(position as integer) as position,
        cast(apple_song_id as text) as apple_song_id,
        cast(apple_primary_artist_id as text) as apple_primary_artist_id,
        cast(artist_text as text) as artist_text,
        cast(title_text as text) as title_text,
        cast(coalesce(multi_artist_credit, false) as boolean) as multi_artist_credit,
        cast(coalesce(isrc, identity_isrc) as text) as isrc,
        cast(case when isrc is not null then 'page' else isrc_method end as text) as isrc_source,
        cast(mb_recording_gid as text) as mb_recording_gid,
        cast(recording_method as text) as recording_method,
        cast(recording_confidence as double precision) as confidence,
        cast(observed_at as timestamp) as observed_at,
        {{ mdp_source_keys(['_source_key']) }} as _source_keys
    from entries
)
{{ mdp_annotate('annotated') }}
