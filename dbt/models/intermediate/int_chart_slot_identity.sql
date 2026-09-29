{{ config(materialized='table', tags=['silver','cadence:daily','scope:global']) }}
with candidates as (
    select chart,territory,cast(week as text) as week,canonical_track_id,source_key,cast(position as text) as value
    from {{ ref('track_candidates_fixture') }}

)
select *, md5(concat_ws(chr(31),chart,territory,week,canonical_track_id)) as entity_id,
       chart is not null and territory is not null and week is not null and canonical_track_id is not null
       and position(chr(31) in chart)=0 and position(chr(31) in territory)=0
       and position(chr(31) in week)=0 and position(chr(31) in canonical_track_id)=0 as valid_components
from candidates
