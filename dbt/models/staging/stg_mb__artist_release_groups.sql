-- depends_on: {{ ref('bronze_close__daily') }}
{{ config(tags=['gold', 'cadence:daily', 'scope:global']) }}

-- The release groups each catalog lookup (mb_artist_catalog) found, one row per kind: primary type, its sorted
-- `;`-joined secondary types, the count and the earliest dated release year. The latest landing of a kind wins.
-- It reads the rows landed before this cycle closed (derived_rows=false), as stg_mb__artist_catalog does.
with visible as (
    select * from {{ source('raw', 'mb_artist_release_groups') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.mb_artist_release_groups', derived_rows=false) }}
), ranked as (
    select *, row_number() over (
        partition by mb_artist_gid, input_version, config_version, primary_type, secondary_types
        order by _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(mb_artist_gid as text) as mb_artist_gid,
    cast(primary_type as text) as primary_type,
    cast(secondary_types as text) as secondary_types,
    cast(release_groups as bigint) as release_groups,
    cast(first_release_year as integer) as first_release_year,
    'mb_artist_catalog' as source_key,
    learning_eligible, input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at,
    cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
