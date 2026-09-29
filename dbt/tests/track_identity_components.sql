select * from {{ ref('int_chart_slot_identity') }} where valid_components IS NOT TRUE
