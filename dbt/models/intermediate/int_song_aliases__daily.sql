{{ config(materialized='incremental', unique_key='alias_key', on_schema_change='fail', incremental_strategy='delete+insert', full_refresh=false, tags=['cadence:daily', 'scope:global']) }}
with candidates as (
    select platform || ':' || platform_track_id as alias_key, song_key, platform, platform_track_id, resolved, source_keys
    from {{ ref('int_song_key__daily') }}
    union all
    select 'isrc:' || isrc, song_key, platform, platform_track_id, resolved, source_keys
    from {{ ref('int_song_key__daily') }} where isrc is not null
    {% if is_incremental() %}
    union all
    select old.alias_key, coalesce(k.song_key, old.song_key), old.platform, old.platform_track_id,
        coalesce(k.resolved, old.resolved), {{ mdp_source_keys(arrays=['old.source_keys', 'k.source_keys']) }}
    from {{ this }} old left join {{ ref('int_song_key__daily') }} k
        on k.platform = old.platform and k.platform_track_id = old.platform_track_id
    {% endif %}
), chosen as (
    select *, row_number() over (partition by alias_key order by resolved desc, song_key, platform, platform_track_id) as n
    from candidates
)
select alias_key, song_key, platform, platform_track_id, resolved, source_keys
from chosen where n = 1
