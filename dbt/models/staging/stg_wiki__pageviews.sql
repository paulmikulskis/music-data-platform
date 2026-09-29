-- depends_on: {{ ref('bronze_close__daily') }}
{{ config(tags=['gold', 'cadence:daily', 'scope:global']) }}

-- Human daily views per article (wiki_pageviews): the latest value per QID, project and day,
-- since each cycle refetches the three days before it and Wikimedia fills late data in.
-- It reads the rows landed before this cycle closed (derived_rows=false) and has no edge to the wiki invoke:
-- a Replay reads the same rows, a run of this cycle lands for the next one, and a failed or partial wiki
-- invoke means fewer rows, never a skipped mart.
with visible as (
    select * from {{ source('raw', 'wiki_pageviews') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.wiki_pageviews', derived_rows=false) }}
), ranked as (
    select *, row_number() over (
        partition by qid, project, date
        order by pageview_day desc, run_admitted_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(qid as text) as qid,
    cast(project as text) as project,
    cast(title as text) as title,
    cast(date as date) as day,
    cast(views as bigint) as views,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'wiki_pageviews' as source_key,
    input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
