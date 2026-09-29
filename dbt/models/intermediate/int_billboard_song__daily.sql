{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with charts as (
    select *, {{ mdp_fold_name('track_title') }} as folded_title,
        {{ mdp_fold_name('artist_name') }} as folded_credit
    from {{ ref('stg_billboard__chart_entries_daily') }} where chart_name = 'hot-100'
), complete_reads as (
    -- A mixture of partial dumps cannot prove absence from one complete chart.
    select chart_week, _dump_id
    from charts group by 1, 2
    having count(distinct chart_position) = 100 and min(chart_position) = 1 and max(chart_position) = 100
), weeks as (
    select chart_week, count(*) > 0 as complete from complete_reads group by 1
), candidates as (
    select distinct b.chart_week, b.chart_position, c.cluster_key, c.cluster_confidence,
        c.cluster_methods, c.source_keys,
        {{ mdp_billboard_credit_method('b.folded_credit', 'k.folded_artist') }} as method
    from charts b join {{ ref('int_song_cluster_inputs__daily') }} k
        on b.folded_title = k.folded_title and nullif(k.folded_artist, '') is not null
    join {{ ref('int_song_cluster__daily') }} c on c.song_key = k.song_key
), ranked_candidates as (
    select *, case when method = 'folded' then 0 else 1 end as match_rank,
        min(case when method = 'folded' then 0 else 1 end)
            over (partition by chart_week, chart_position) as best_rank
    from candidates where method is not null
), preferred_candidates as (
    -- A complete credit identifies the act before any shorter primary-credit guess.
    select * from ranked_candidates where match_rank = best_rank
), possible_history as (
    -- Keep candidates that cannot be served: ambiguity cannot prove prior absence.
    select cluster_key, min(chart_week) as first_possible_week
    from preferred_candidates group by 1
), enabled as (
    select c.*, least(r.confidence, c.cluster_confidence) as confidence
    from preferred_candidates c join {{ ref('billboard_match_rules') }} r on r.method = c.method
    where r.enabled
), matches as (
    select chart_week, chart_position, count(distinct cluster_key) as candidate_groups,
        min(cluster_key) as song_key, min(confidence) as confidence,
        max(case when cluster_methods <> '[]' then 1 else 0 end) as grouped,
        min(method) as method
    from enabled group by 1, 2
), match_rights as (
    select chart_week, chart_position, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from enabled {{ mdp_json_elements('source_keys', 'k') }} group by 1, 2
), keyed as (
    select b.*, case when m.candidate_groups = 1 then m.song_key end as song_key,
        case when m.candidate_groups > 1 then 'ambiguous'
            when m.candidate_groups = 1 and m.grouped = 1 then 'group'
            else coalesce(m.method, 'unmatched') end as billboard_match_method,
        case when m.candidate_groups = 1 then m.confidence end as confidence,
        case when m.candidate_groups = 1 then m.method end as billboard_match_rule,
        coalesce(m.candidate_groups = 1 and m.grouped = 1, false) as matched_by_group,
        coalesce(m.candidate_groups, 0) as candidate_groups,
        coalesce(w.complete, false) as week_complete,
        coalesce(p.complete, false) as previous_week_complete,
        {{ mdp_source_keys(['b.source_key'], ['r.source_keys']) }} as source_keys
    from charts b left join matches m using (chart_week, chart_position)
    left join match_rights r using (chart_week, chart_position)
    left join weeks w on w.chart_week = b.chart_week
    left join weeks p on p.chart_week = b.chart_week - 7
), history as (
    select k.*, p.first_possible_week,
        min(chart_week) over (partition by song_key) as first_week
    from keyed k left join possible_history p on p.cluster_key = k.song_key
)
select cast(chart_week as date) as chart_week, cast(chart_position as integer) as chart_position,
    cast(track_title as varchar) as track_title, cast(artist_name as varchar) as artist_name,
    cast(song_key as text) as song_key, cast(billboard_match_method as text) as billboard_match_method,
    cast(confidence as double precision) as confidence, cast(matched_by_group as boolean) as matched_by_group,
    cast(billboard_match_rule as text) as billboard_match_rule,
    cast(candidate_groups as integer) as candidate_groups, cast(weeks_on_chart as integer) as weeks_on_chart,
    cast(case when song_key is null then null
        when chart_week > first_week or weeks_on_chart > 1 then false
        when first_possible_week < chart_week then null
        when week_complete and previous_week_complete and weeks_on_chart = 1 then true
        end as boolean) as is_debut,
    cast(week_complete as boolean) as week_complete, cast(source_keys as text) as source_keys
from history
