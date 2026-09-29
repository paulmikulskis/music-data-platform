-- depends_on: {{ ref('bronze_invoke__sc_hubs') }}
-- depends_on: {{ ref('bronze_invoke__sc_curator_playlists') }}
-- depends_on: {{ ref('bronze_invoke__sc_curator_playlists_weekly') }}
-- depends_on: {{ ref('bronze_close__daily') }}
-- Candidate evidence per (platform, playlist, variant, channel, reference); the
-- latest listing wins. Discovery scoring reads this.
{{ config(tags=['cadence:daily', 'scope:global']) }}

with visible as (
    select *,
        min(observed_at) over (
            partition by platform, playlist_id, variant, discovered_via, via_ref
        ) as first_observed_at,
        row_number() over (
            partition by platform, playlist_id, variant, discovered_via, via_ref
            order by observed_at desc, _landed_seq desc, _dump_id desc
        ) as _rn
    from {{ source('raw', 'playlist_candidates') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.playlist_candidates') }}
)
-- A listed playlist carries no owner class, so its owner may be a private person: no name, and a
-- keyed pseudonym for the id.
select platform, playlist_id, variant, discovered_via, via_ref, first_observed_at,
    observed_at as last_observed_at, position as listing_rank, hint_title,
    {{ mdp_pseudonym('hint_owner_id') }} as hint_owner_id, cast(null as text) as hint_owner_name,
    hint_followers, hint_track_count, hint_version, fetch_surface,
    _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id, _request_id,
    _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from visible where _rn = 1
