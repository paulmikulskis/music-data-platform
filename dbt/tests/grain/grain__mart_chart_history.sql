-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select chart_name, chart_week, chart_position, count(*) as rows_per_grain
from {{ ref('mart_chart_history') }}
group by chart_name, chart_week, chart_position
having count(*) > 1
