-- depends_on: {{ ref('bronze_close__weekly') }}
-- depends_on: {{ ref('bronze_invoke__lb_sitewide') }}
{{ config(tags=['cadence:weekly', 'scope:global']) }}

-- ListenBrainz sitewide weekly top lists (lb_sitewide): one row per entity type, window, statistics
-- run (last_updated) and rank; the latest landing wins.
with src as (
    select * from {{ source('raw', 'lb_sitewide') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.lb_sitewide') }}
), ranked as (
    select *, row_number() over (
        partition by entity_type, window_start, last_updated, rank
        order by _landed_seq desc, _dump_id desc
    ) as _rn
    from src
)
select
    cast(entity_type as text) as entity_type,
    cast(stats_range as text) as stats_range,
    cast({{ playlist_utc('window_start') }} as timestamp) as window_start,
    cast({{ playlist_utc('window_end') }} as timestamp) as window_end,
    cast({{ playlist_utc('last_updated') }} as timestamp) as last_updated,
    cast(rank as integer) as rank,
    cast(mbid as text) as mbid,
    cast(name as text) as name,
    cast(artist_mbids as text) as artist_mbids,
    cast(listen_count as bigint) as listen_count,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'lb_sitewide' as source_key,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
