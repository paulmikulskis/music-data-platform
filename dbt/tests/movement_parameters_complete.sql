-- Restore a missing named value in seeds/movement_parameters.csv, then run dbt seed.
with required as (
    {% for name in ['window_max_days', 'stream_min_days', 'editorial_add_points', 'algorithmic_add_points',
        'head_confidence', 'tier_one_followers', 'tier_two_followers', 'playlist_adds_weight',
        'follower_exposure_gain_weight', 'shazam_spread_gain_weight', 'stream_rate_gain_weight', 'stream_interval_min_hours', 'stream_interval_max_hours',
        'shazam_history_days', 'shazam_min_observed_days', 'apple_successor_position_fraction'] %}
    select '{{ name }}' as name{% if not loop.last %} union all {% endif %}
    {% endfor %}
)
select r.name from required r left join {{ ref('movement_parameters') }} p using (name)
where p.value is null or p.value <= 0
    or (r.name = 'apple_successor_position_fraction' and p.value > 1)
