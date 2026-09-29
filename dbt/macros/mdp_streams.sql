{# Daily streams from a cumulative play count. `observations` is a relation with the `keys`
   columns, `observed_at` (naive UTC), `play_count` (null when the page read carried none) and `run_id`.
   Returns keys × UTC day from each key's first observation to the bound cycle's day: the day's latest
   count, the change it belongs to (`count_changed_at`, the first observation of that value), the
   difference from the previous changed value ("since the last update"; Spotify refreshes the count on
   its own schedule, so an interval is not a day), and `count_status`: `changed` (the value first
   appeared that day), `unchanged`, `no_count` (read, and the page carried no count) or `no_fetch` (a
   flagged gap: nothing read that day). Nothing is interpolated. A key last read more than `stale_days`
   before the cycle's day (off every tracked list, say) ends at its last read instead of gapping forever. #}
{% macro mdp_count_series(observations, keys, stale_days=28) %}
  {%- set k = keys | join(', ') -%}
  {%- set on_keys -%}{% for key in keys %}{{ ' and ' if not loop.first }}{left}.{{ key }} = {right}.{{ key }}{% endfor %}{%- endset -%}
with mdp_counted as (
    select {{ k }}, observed_at, play_count,
        case when lag(play_count) over (partition by {{ k }} order by observed_at) is null
            or play_count <> lag(play_count) over (partition by {{ k }} order by observed_at) then 1 else 0 end as is_change
    from {{ observations }}
    where play_count is not null
), mdp_numbered as (
    select *, sum(is_change) over (partition by {{ k }} order by observed_at rows between unbounded preceding and current row) as change_no
    from mdp_counted
), mdp_changes as (
    select {{ k }}, change_no, observed_at as count_changed_at,
        play_count - lag(play_count) over (partition by {{ k }} order by change_no) as streams_since_last_update,
        lag(observed_at) over (partition by {{ k }} order by change_no) as previous_count_changed_at
    from mdp_numbered
    where is_change = 1
), mdp_day_counts as (
    select {{ k }}, cast(observed_at as date) as day, change_no, play_count,
        row_number() over (partition by {{ k }}, cast(observed_at as date) order by observed_at desc) as mdp_rn
    from mdp_numbered
), mdp_fetched as (
    select {{ k }}, cast(observed_at as date) as day, max(observed_at) as observed_at
    from {{ observations }}
    group by {{ k }}, cast(observed_at as date)
), mdp_runs as (
    select {{ k }}, cast(observed_at as date) as day, run_id,
        row_number() over (partition by {{ k }}, cast(observed_at as date) order by observed_at desc, run_id) as mdp_rn
    from {{ observations }}
), mdp_bounds as (
    select {{ k }}, min(day) as first_day,
        case when max(day) >= cast({{ mdp_cycle_clock() }} as date) - {{ stale_days }}
            then cast({{ mdp_cycle_clock() }} as date) else max(day) end as last_day
    from mdp_fetched group by {{ k }}
), mdp_spine as (
    select {{ k }}, {{ mdp_day_series('first_day', 'last_day') }} as day
    from mdp_bounds
)
select {% for key in keys %}s.{{ key }}, {% endfor %}s.day,
    cast(d.play_count as bigint) as play_count,
    cast(c.count_changed_at as timestamp) as count_changed_at,
    cast(c.streams_since_last_update as bigint) as streams_since_last_update,
    cast(c.previous_count_changed_at as timestamp) as previous_count_changed_at,
    cast(case when d.play_count is not null then
            case when cast(c.count_changed_at as date) = s.day then 'changed' else 'unchanged' end
        when f.day is not null then 'no_count' else 'no_fetch' end as text) as count_status,
    cast(f.observed_at as timestamp) as observed_at,
    cast(case when r.run_id is null then '[]' else '["' || r.run_id || '"]' end as text) as _run_ids
from mdp_spine s
left join mdp_fetched f on {{ on_keys.replace('{left}', 'f').replace('{right}', 's') }} and f.day = s.day
left join mdp_day_counts d on {{ on_keys.replace('{left}', 'd').replace('{right}', 's') }} and d.day = s.day and d.mdp_rn = 1
left join mdp_changes c on {{ on_keys.replace('{left}', 'c').replace('{right}', 'd') }} and c.change_no = d.change_no
left join mdp_runs r on {{ on_keys.replace('{left}', 'r').replace('{right}', 's') }} and r.day = s.day and r.mdp_rn = 1
{% endmacro %}
