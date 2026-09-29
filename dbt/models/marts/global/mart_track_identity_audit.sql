{{ config(materialized='view', tags=['silver','cadence:daily']) }}
with chosen as (
{{ fold_by_priority(ref('int_chart_slot_identity'), 'track', 'position') }}
)
select c.*, p.rank as priority_rank, c.source_key=w.source_key and c.value=w.value as selected,
       w.source_key as selected_source, w.value as selected_value
from {{ ref('int_chart_slot_identity') }} c
left join {{ ref('source_priority') }} p on p.source_key=c.source_key and p.entity='track' and p.field='position'
left join chosen w on w.entity_id=c.entity_id
