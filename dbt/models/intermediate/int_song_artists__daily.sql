{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}") }}
-- Each MusicBrainz artist credited on a song in the song layer: every artist in its recording's credit, and each
-- platform track's first artist when exactly one MusicBrainz artist carries that artist's URL.
-- For example, "A feat. B" resolved to a recording lists both A and B.
with generations as ({{ mdp_reference_generations() }}),
reference as ({{ mdp_reference_current('generations', ['recording', 'artist_credit_name', 'artist']) }}),
songs as (
    select distinct song_key, mb_recording_gid from {{ ref('int_song_key__daily') }} where mb_recording_gid is not null
), credited as (
    select s.song_key, a.artist_gid as mb_artist_gid
    from songs s
    join reference r on r.mb_table = 'recording' and r.recording_gid = s.mb_recording_gid and not r.tombstoned
    join reference n on n.mb_table = 'artist_credit_name' and n.artist_credit_id = r.artist_credit_id and not n.tombstoned
    join reference a on a.mb_table = 'artist' and a.artist_id = n.artist_id and not a.tombstoned
), linked as (
    select k.song_key, i.mb_artist_gid
    from {{ ref('int_song_key__daily') }} k
    join {{ ref('int_artist_identity') }} i
        on {{ mdp_song_platform('i.platform') }} = k.platform and i.platform_artist_id = k.primary_artist_id
    where i.mb_artist_gid is not null
)
select distinct cast(song_key as text) as song_key, cast(mb_artist_gid as text) as mb_artist_gid
from (select * from credited union all select * from linked) artists
