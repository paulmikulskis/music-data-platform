-- depends_on: {{ ref('bronze_close__daily') }}
-- depends_on: {{ ref('bronze_invoke__sz_chart') }}
{{ config(materialized='table', meta={'record_build': true}, post_hook='{{ mdp_record_build() }}', tags=['cadence:daily', 'scope:global']) }}

-- Stored daily so a count and its build stamp describe the same frozen rows.
-- Shazam chart rows, typed and deduplicated by chart, chart date and position: the latest
-- landing wins, since a chart fetched again on the same chart date lands the same rows again. Only
-- sz_chart writes raw.shazam_chart_entries; the Billboard rows stay in raw.chart_entries.
with src as (
    select * from {{ source('raw', 'shazam_chart_entries') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.shazam_chart_entries') }}
),
ranked as (
    select
        *,
        row_number() over (
            partition by chart, chart_date, position
            order by _landed_seq desc, _dump_id desc
        ) as _rn
    from src
)
select
    cast(chart as text)                   as chart,
    cast(split_part(chart, ':', 2) as text) as chart_type,
    cast(split_part(chart, ':', 3) as text) as country,
    cast(nullif(split_part(chart, ':', 4), '') as text) as city,
    cast(chart_date as date)              as chart_date,
    cast(position as integer)             as position,
    cast(apple_song_id as text)           as apple_song_id,
    cast(apple_primary_artist_id as text) as apple_primary_artist_id,
    cast(artist_text as text)             as artist_text,
    cast(title_text as text)              as title_text,
    cast(isrc as text)                    as isrc,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'sz_chart'                            as source_key,
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
