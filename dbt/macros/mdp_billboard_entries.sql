{% macro mdp_billboard_entries(raw_relation) %}
with src as (
    select * from {{ raw_relation }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.chart_entries') }}
),
ranked as (
    select
        *,
        row_number() over (
            partition by chart, week, position
            order by _landed_seq desc, _dump_id desc
        ) as _rn
    from src
)
select
    cast(chart as varchar)         as chart_name,
    cast(week as date)             as chart_week,
    cast(position as integer)      as chart_position,
    cast(title as varchar)         as track_title,
    cast(weeks_on_chart as integer) as weeks_on_chart,
    cast(artist as varchar)        as artist_name,
    'billboard_hot100'             as source_key,
    cast(_ingested_at as timestamp) as ingested_at,
    _run_id,
    _dump_id,
    _landed_seq,
    _cycle_id,
    _revision_id,
    _target_id,
    _request_id,
    _source_key,
    _ingested_at,
    cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1

{% endmacro %}
