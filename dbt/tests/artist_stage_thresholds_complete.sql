-- Every artist stage rule needs its value. A missing row would read null and turn every linked artist developing,
-- so the seed fails here instead. Add the row to dbt/seeds/artist_stage_thresholds.csv and run dbt seed.
select r.name
from (
    select 'established_min_release_groups' as name union all select 'established_counted_primary_types'
    union all select 'established_excluded_secondary_types' union all select 'established_min_years'
    union all select 'emerging_max_release_groups' union all select 'emerging_max_years'
) r
left join {{ ref('artist_stage_thresholds') }} t on t.name = r.name
where t.value is null
