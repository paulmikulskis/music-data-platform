-- depends_on: {{ ref('bronze_close__daily') }}
-- depends_on: {{ ref('bronze_invoke__lb_fresh_releases') }}
{{ config(tags=['cadence:daily', 'scope:global']) }}

-- ListenBrainz fresh releases (lb_fresh_releases): each release as last observed; tags never land.
with src as (
    select * from {{ source('raw', 'lb_fresh_releases') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.lb_fresh_releases') }}
), ranked as (
    select *, row_number() over (
        partition by release_mbid
        order by observed_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from src
)
select
    cast(release_mbid as text) as release_mbid,
    cast(release_group_mbid as text) as release_group_mbid,
    cast(artist_mbids as text) as artist_mbids,
    cast(artist_credit_name as text) as artist_credit_name,
    cast(release_name as text) as release_name,
    cast(release_group_primary_type as text) as release_group_primary_type,
    cast(release_date as date) as release_date,
    cast(listen_count as bigint) as listen_count,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    'lb_fresh_releases' as source_key,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
