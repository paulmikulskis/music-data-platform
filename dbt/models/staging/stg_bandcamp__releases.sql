-- depends_on: {{ ref('bronze_invoke__bc_tralbum') }}
-- depends_on: {{ ref('bronze_close__daily') }}
-- One row per album or track page, as of its latest fetch.
{{ config(tags=['cadence:daily', 'scope:global']) }}

with visible as (
    select *, row_number() over (
        partition by item_type, item_id
        order by observed_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from {{ source('raw', 'bc_releases') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.bc_releases') }}
)
select page_url, observed_at, lastmod, item_type, item_id, band_id, title, artist, upc, isrc,
    album_id, featured_track_id, num_tracks, release_date, publish_date, mod_date, label,
    _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id, _request_id,
    _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from visible where _rn = 1
