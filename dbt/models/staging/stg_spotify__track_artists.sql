-- depends_on: {{ ref('gold_invoke__sp_track_artists') }}
{{ config(materialized='view', tags=['gold', 'cadence:daily', 'scope:global']) }}

-- Spotify track artist ids read from the server-rendered track page (sp_track_artists), for tracks
-- no playlist page row identified. One row per track and credited artist; the newest configuration wins.
{{ mdp_playlist_track_artists(mdp_context().manifest_filter('_dump_id', 'raw.sp_track_artists')) }}
