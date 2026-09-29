-- Restore the named age rules in seeds/song_age_parameters.csv, then run dbt seed.
with required as (
    {% for name in ['apple_new_id_min', 'apple_new_year_min', 'apple_catalog_id_max',
        'apple_catalog_year_max', 'isrc_new_max_years'] %}
    select '{{ name }}' as name{% if not loop.last %} union all {% endif %}
    {% endfor %}
)
select r.name from required r left join {{ ref('song_age_parameters') }} p using (name)
where p.value is null or p.measured_on is null
