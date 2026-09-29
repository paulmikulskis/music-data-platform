{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}
-- mb_artist_catalog's inputs: each MusicBrainz artist credited on a song in the song layer. The gid comes from the
-- CC0 MusicBrainz spine, so the input declares _source_keys ["mb_spine"]: the songs that select an artist never
-- enter its lineage, and the reading stays learning-eligible. Its version carries the bound cycle's ISO week, so
-- the daily job reads each artist once a week.
with artists as (
    select distinct mb_artist_gid, cast({{ mdp_cycle_week() }} as date) as catalog_week
    from {{ ref('int_song_artists__daily') }}
)
select
    cast(mb_artist_gid as text) as mb_artist_gid,
    catalog_week,
    cast('["mb_spine"]' as text) as _source_keys,
    {{ mdp_input_identity(['mb_artist_gid'], ['mb_artist_gid', 'catalog_week']) }}
from artists
