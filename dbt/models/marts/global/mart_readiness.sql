{{ config(tags=['cadence:daily']) }}
with families as (
    select 'playlists' as family, 1 as days_needed,
        'Collect two playlist days to measure entered reach; open song history.' as history_needed
    union all select 'shazam', 1,
        'Collect two chart days to measure spread; open song history.'
    union all select 'streams', cast(2 * {{ mdp_movement_parameter('stream_min_days') }} - 1 as integer),
        'Collect enough rated days in each comparison half; open song history.'
), observed as (
    select distinct 'playlists' as family, day, source_keys from {{ ref('int_cluster_day__daily') }}
    where playlists_observed and day <= {{ mdp_cycle_day() }}
    union all
    select distinct 'shazam', day, source_keys from {{ ref('int_cluster_day__daily') }}
    where shazam_observed and day <= {{ mdp_cycle_day() }}
    union all
    select distinct 'streams', day, source_keys from {{ ref('int_cluster_day__daily') }}
    where stream_rate is not null and day <= {{ mdp_cycle_day() }}
), history as (
    select family, count(distinct day) as history_days, min(day) as first_day
    from observed group by family
), rights as (
    select family, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from (select distinct family, source_keys from observed) inputs
    {{ mdp_json_elements('source_keys', 'k') }} group by family
), list_members as (
    select movement_list, song_key, artist_stage, source_keys from {{ ref('mart_top_movers_current') }}
    union select movement_list, song_key, artist_stage, source_keys from {{ ref('mart_early_signals_current') }}
    union select movement_list, song_key, artist_stage, source_keys from {{ ref('mart_arrivals_current') }}
), list_counts as (
    select movement_list, count(distinct song_key) as list_songs,
        count(distinct case when artist_stage in ('emerging', 'developing', 'established') then song_key end) as stage_known_songs
    from list_members group by 1
), list_rights as (
    select movement_list, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from (select distinct movement_list, source_keys from list_members) inputs
    {{ mdp_json_elements('source_keys', 'k') }} group by 1
), lists as (
    select 'new_entries' as movement_list union all select 'established_entries'
    union all select 'catalog_entries' union all select 'unplaced'
), chart_week as (
    select max(chart_week) as chart_week from {{ ref('mart_chart_history') }}
    where chart_name = 'hot-100' and chart_week <= {{ mdp_cycle_day() }}
), chart_entries as (
    select c.* from {{ ref('mart_chart_history') }} c join chart_week w using (chart_week)
    where c.chart_name = 'hot-100'
), chart_counts as (
    select count(distinct chart_position) as chart_entries,
        count(distinct case when song_key is not null then chart_position end) as keyed_entries
    from chart_entries
), chart_rights as (
    select {{ mdp_source_keys_agg('k.value') }} as source_keys
    from chart_entries {{ mdp_json_elements('source_keys', 'k') }}
), rows as (
    select cast(f.family as text) as family, cast({{ mdp_cycle_day() }} as date) as day,
        cast(coalesce(h.history_days, 0) as bigint) as history_days,
        cast(f.history_needed as text) as history_needed,
        cast(h.first_day + f.days_needed as date) as first_rank_day,
        cast(null as text) as movement_list, cast(null as bigint) as stage_known_songs, cast(null as bigint) as list_songs,
        cast(null as date) as chart_week, cast(null as bigint) as keyed_entries, cast(null as bigint) as chart_entries,
        cast(coalesce(r.source_keys, '[]') as text) as _source_keys
    from families f left join history h using (family) left join rights r using (family)
    union all
    select cast('artist_stage:' || l.movement_list as text), cast({{ mdp_cycle_day() }} as date), cast(0 as bigint),
        cast('Artist size comes from catalog readings; open the song for evidence.' as text), cast(null as date),
        cast(l.movement_list as text), cast(coalesce(c.stage_known_songs, 0) as bigint), cast(coalesce(c.list_songs, 0) as bigint),
        cast(null as date), cast(null as bigint), cast(null as bigint),
        cast(coalesce(r.source_keys, '[]') as text)
    from lists l left join list_counts c using (movement_list) left join list_rights r using (movement_list)
    union all
    select cast('billboard_hot100' as text), cast({{ mdp_cycle_day() }} as date), cast(0 as bigint),
        cast(case when w.chart_week is null then 'Hot 100 not read yet; open mart_chart_history.'
            else cast(c.keyed_entries as text) || ' of 100 Hot 100 entries keyed for week ' || cast(w.chart_week as text)
                || ' (' || cast(c.chart_entries as text) || ' entries read); open mart_chart_history.' end as text),
        cast(null as date), cast(null as text), cast(null as bigint), cast(null as bigint),
        cast(w.chart_week as date),
        cast(case when w.chart_week is not null then c.keyed_entries end as bigint),
        cast(case when w.chart_week is not null then c.chart_entries end as bigint),
        cast(coalesce(r.source_keys, '[]') as text)
    from chart_week w cross join chart_counts c cross join chart_rights r
)
{{ mdp_annotate('rows') }}
