-- depends_on: {{ ref('bronze_close__daily') }}
{{ config(tags=['gold', 'cadence:daily', 'scope:global']) }}

-- Each artist's newest catalog lookup (mb_artist_catalog): found, not_found or special_purpose. Its release groups
-- sit in stg_mb__artist_release_groups under the same input_version and config_version. An artist with no
-- lookup yet has no row.
-- It reads the rows landed before this cycle closed (derived_rows=false) and has no edge to the invoke: a Replay
-- reads the same rows, a run of this cycle lands for the next one, and a failed or slow lookup means an older
-- reading, never a skipped mart.
with visible as (
    select * from {{ source('raw', 'mb_artist_catalog') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.mb_artist_catalog', derived_rows=false) }}
), ranked as (
    select *, row_number() over (
        partition by mb_artist_gid
        order by catalog_week desc, run_admitted_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(mb_artist_gid as text) as mb_artist_gid,
    cast(status as text) as status,
    cast(artist_gid as text) as artist_gid,
    cast(catalog_week as text) as catalog_week,
    cast(mb_generation as text) as mb_generation,
    'mb_artist_catalog' as source_key,
    learning_eligible, input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at,
    cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
