-- depends_on: {{ ref('bronze_invoke__bc_tralbum') }}
-- depends_on: {{ ref('bronze_close__daily') }}
-- Tracks as listed on each release page, from that page's latest fetch.
{{ config(tags=['cadence:daily', 'scope:global']) }}

with visible as (
    select *, max(observed_at) over (partition by page_url) as latest,
        row_number() over (
            partition by page_url, observed_at, position
            order by _landed_seq desc, _dump_id desc
        ) as _rn
    from {{ source('raw', 'bc_tracks') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.bc_tracks') }}
)
select page_url, position, observed_at, track_id, album_id, page_item_type, page_item_id,
    track_num, title, duration_ms, isrc, band_id,
    _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id, _request_id,
    _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from visible where _rn = 1 and observed_at = latest
