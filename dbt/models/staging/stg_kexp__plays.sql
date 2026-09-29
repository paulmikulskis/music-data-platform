-- depends_on: {{ ref('bronze_close__daily') }}
-- depends_on: {{ ref('bronze_invoke__kexp_plays') }}
{{ config(tags=['cadence:daily', 'scope:global']) }}

-- KEXP track plays (kexp_plays), one row per play; a play the daily overlap or a backfill landed
-- again keeps its latest landing. Operator-only: no served mart reads it until the registry records
-- KEXP's written permission.
with src as (
    select * from {{ source('raw', 'radio_plays') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.radio_plays') }}
), ranked as (
    select *, row_number() over (
        partition by station, play_id
        order by _landed_seq desc, _dump_id desc
    ) as _rn
    from src
)
select
    cast(station as text) as station,
    cast(play_id as bigint) as play_id,
    cast({{ playlist_utc('airdate') }} as timestamp) as airdate,
    cast(recording_mbid as text) as recording_mbid,
    cast(artist_mbids as text) as artist_mbids,
    cast(release_group_mbid as text) as release_group_mbid,
    cast(label_mbids as text) as label_mbids,
    cast(rotation_status as text) as rotation_status,
    cast(case rotation_status when 'Heavy' then 3 when 'Medium' then 2 when 'Light' then 1 else 0 end as integer) as rotation_rank,
    cast(is_local as boolean) as is_local,
    cast(is_request as boolean) as is_request,
    cast(is_live as boolean) as is_live,
    cast(artist_text as text) as artist_text,
    cast(song_text as text) as song_text,
    'kexp_plays' as source_key,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
