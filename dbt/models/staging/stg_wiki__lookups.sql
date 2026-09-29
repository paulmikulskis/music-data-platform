-- depends_on: {{ ref('bronze_close__daily') }}
{{ config(tags=['gold', 'cadence:daily', 'scope:global']) }}

-- Each completed lookup of an act's Wikidata item (wiki_sitelinks): how many Wikipedia articles it
-- listed, one row per QID and week; the newest configuration's landing of a lookup wins. A lookup that
-- failed, or has not run, has no row.
-- It reads the rows landed before this cycle closed (derived_rows=false) and has no edge to the wiki invoke:
-- a Replay reads the same rows, a run of this cycle lands for the next one, and a failed or partial wiki
-- invoke means fewer rows, never a skipped mart.
with visible as (
    select * from {{ source('raw', 'wiki_lookups') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.wiki_lookups', derived_rows=false) }}
), ranked as (
    select *, row_number() over (
        partition by qid, sitelinks_week
        order by run_admitted_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(qid as text) as qid,
    cast(entity_qid as text) as entity_qid,
    cast(articles as bigint) as articles,
    cast(sitelinks_week as date) as sitelinks_week,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'wiki_sitelinks' as source_key,
    input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
