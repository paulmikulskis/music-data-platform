{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}
with ranked as (
    select c.cluster_key as song_key, a.age_class, a.age_basis,
        row_number() over (partition by c.cluster_key order by
            case a.age_class when 'catalog' then 0 when 'new' then 1 else 2 end,
            case a.age_basis when 'isrc_year' then 0 when 'apple_id_band' then 1 else 2 end, a.song_key) as pick,
        max(case a.artist_stage when 'established' then 3 when 'developing' then 2 when 'emerging' then 1 else 0 end)
            over (partition by c.cluster_key) as stage,
        min(case when a.artist_stage = 'unknown' or a.artist_stage is null then 0 else 1 end)
            over (partition by c.cluster_key) as all_known
    from {{ ref('int_song_cluster__daily') }} c left join {{ ref('int_song_age__daily') }} a using (song_key)
)
select song_key, coalesce(age_class, 'unknown') as age_class, coalesce(age_basis, 'none') as age_basis,
    case when stage = 3 then 'established' when all_known = 0 then 'unknown'
        when stage = 2 then 'developing' when stage = 1 then 'emerging' else 'unknown' end as artist_stage,
    case when stage = 3 or all_known = 1 then 'musicbrainz_catalog' else 'none' end as artist_stage_basis
from ranked where pick = 1
