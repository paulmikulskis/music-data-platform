-- depends_on: {{ ref('bronze_invoke__sc_playlist') }}
-- depends_on: {{ ref('bronze_invoke__sc_curator_playlists') }}
-- depends_on: {{ ref('bronze_invoke__sc_curator_playlists_weekly') }}
-- depends_on: {{ ref('bronze_close__daily') }}
-- SoundCloud track detail from hydrations; the latest hydration per track wins.
{{ config(tags=['cadence:daily', 'scope:global']) }}

with visible as (
    select *, row_number() over (
        partition by track_id
        order by observed_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from {{ source('raw', 'sc_tracks') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.sc_tracks') }}
)
select track_id, urn, title, permalink_url, genre, label_name, duration_ms, created_at,
    display_date, release_date, last_modified, playback_count, likes_count, reposts_count,
    comment_count, monetization_model, policy, isrc, upc, p_line, c_line, release_title,
    album_title, publisher_artist, writer_composer, publisher, explicit, uploader_id,
    uploader_permalink, uploader_username, uploader_verified, uploader_followers,
    observed_at, playlist_id as hydrated_in_playlist, snapshot_id as hydrated_in_snapshot,
    _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id, _request_id,
    _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from visible where _rn = 1
