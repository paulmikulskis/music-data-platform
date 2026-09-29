{{ config(tags=['cadence:daily']) }}
-- design: recording x day by platform and owner class. Contributing playlists are deduplicated by
-- (platform, playlist_id, recording) before summing, so each playlist's follower count counts once even
-- when variants or duplicate occurrences differ (Spotify followers are global per playlist). Spotify
-- reports playlist_followers; Apple exposes no follower counts and reports list_count and best_position.
with annotated as (
    {{ mdp_track_followers() }}
)
{{ mdp_annotate('annotated') }}
