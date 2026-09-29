-- depends_on: {{ ref('bronze_close__daily') }}
-- depends_on: {{ ref('gold_invoke__lb_similar_artists') }}
{{ config(tags=['gold', 'cadence:daily', 'scope:global']) }}

-- Each act's Labs co-listen neighbours per weekly lookup and algorithm (lb_similar_artists); the
-- newest configuration's landing of a lookup wins.
with visible as (
    select * from {{ source('raw', 'lb_similar_artists') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.lb_similar_artists') }}
), ranked as (
    select *, row_number() over (
        partition by reference_mbid, algorithm, similarity_week, neighbour_mbid
        order by run_admitted_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(reference_mbid as text) as reference_mbid,
    cast(neighbour_mbid as text) as neighbour_mbid,
    cast(score as bigint) as score,
    cast(rank as integer) as rank,
    cast(algorithm as text) as algorithm,
    cast(similarity_week as date) as similarity_week,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'lb_similar_artists' as source_key,
    input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
