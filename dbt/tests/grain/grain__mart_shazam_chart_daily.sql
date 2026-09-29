-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.
select chart, chart_date, position, count(*) as rows_per_grain
from {{ ref('mart_shazam_chart_daily') }}
group by chart, chart_date, position
having count(*) > 1
