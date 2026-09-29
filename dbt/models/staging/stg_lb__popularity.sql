-- depends_on: {{ ref('bronze_close__daily') }}
-- depends_on: {{ ref('gold_invoke__lb_popularity') }}
{{ config(tags=['gold', 'cadence:daily', 'scope:global']) }}

-- Each act's ListenBrainz totals as read on each cycle day (lb_popularity); the newest
-- configuration's landing of a day wins. Null totals mean ListenBrainz had no data for the artist.
with visible as (
    select * from {{ source('raw', 'lb_popularity') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.lb_popularity') }}
), ranked as (
    select *, row_number() over (
        partition by mbid, popularity_day
        order by run_admitted_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(mbid as text) as mbid,
    cast(popularity_day as date) as popularity_day,
    cast(total_listen_count as bigint) as total_listen_count,
    cast(total_user_count as bigint) as total_user_count,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'lb_popularity' as source_key,
    input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
