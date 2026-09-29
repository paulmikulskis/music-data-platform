-- depends_on: {{ ref('stg_playlist__items') }}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

-- sp_track_artists inputs: Spotify tracks on tracked playlists that no observed row
-- identifies by artist id (embed rows carry names only; page rows carry ids for the first 30).
-- Each track is read once: its version is its id. Oldest first by first landing, so a time budget
-- never starves the backlog.
with tracks as (
    select
        platform_track_id,
        min(_landed_seq) as first_landed_seq,
        min(_source_key) as source_key,
        max(case when platform_artist_ids is not null
            and cast(platform_artist_ids as text) not in ('[]', 'null', '') then 1 else 0 end) as has_artist_ids
    from {{ ref('stg_playlist__items') }}
    where platform = 'spotify' and item_type = 'track' and platform_track_id is not null
    group by platform_track_id
)
select
    cast(platform_track_id as text) as platform_track_id,
    cast(first_landed_seq as bigint) as first_landed_seq,
    cast(source_key as text) as source_key,
    {{ mdp_input_identity(['platform_track_id'], ['platform_track_id']) }}
from tracks
where has_artist_ids = 0
order by first_landed_seq, platform_track_id
