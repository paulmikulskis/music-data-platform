{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
-- UNION visits each (root, member) once. Both engines stop when no new pair is reachable.
with recursive nodes as (
    select song_key, max(case when resolved then 1 else 0 end) as resolved
    from {{ ref('int_song_key__daily') }} group by 1
), edges as (
    select e.*, r.confidence
    from {{ ref('int_song_cluster_edges__daily') }} e join {{ ref('song_cluster_rules') }} r using (method)
    where r.enabled or e.basis = 'same_isrc'
), adjacent as (
    select left_key as a, right_key as b from edges union select right_key, left_key from edges
), connected(root, member) as (
    select song_key, song_key from nodes
    union
    select c.root, e.b from connected c join adjacent e on e.a = c.member
), evidence_counts as (
    select * from {{ ref('int_song_group_evidence__daily') }}
), ranked as (
    select c.root as song_key, c.member as cluster_key,
        row_number() over (partition by c.root order by n.resolved desc,
            coalesce(e.evidence_count, 0) desc, c.member) as pick
    from connected c join nodes n on n.song_key = c.member
    left join evidence_counts e on e.song_key = c.member
), membership as (
    select song_key, cluster_key from ranked where pick = 1
), members as (
    select cluster_key, {{ mdp_json_agg('song_key', 'song_key') }} as member_song_keys
    from membership group by 1
), methods as (
    select m.cluster_key, {{ mdp_source_keys_agg('e.method') }} as cluster_methods,
        min(e.confidence) as cluster_confidence
    from membership m join edges e on e.left_key = m.song_key group by 1
), rights_inputs as (
    select m.cluster_key, k.source_keys from membership m join {{ ref('int_song_key__daily') }} k using (song_key)
    union all
    select m.cluster_key, {{ mdp_source_keys(["'track_isrc_crosswalk'"]) }}
    from membership m join edges e on e.left_key = m.song_key where e.basis = 'crosswalk'
    union all
    select m.cluster_key, {{ mdp_source_keys(['t.duration_source_key']) }}
    from membership m join {{ ref('int_song_cluster_inputs__daily') }} t using (song_key)
    where t.duration_source_key is not null
), rights as (
    select cluster_key, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from rights_inputs {{ mdp_json_elements('source_keys', 'k') }} group by 1
)
select cast(m.song_key as text) as song_key, cast(m.cluster_key as text) as cluster_key,
    cast(coalesce(t.cluster_methods, '[]') as text) as cluster_methods,
    cast(coalesce(t.cluster_confidence, 1.0) as double precision) as cluster_confidence,
    cast(s.member_song_keys as text) as member_song_keys, cast(coalesce(r.source_keys, '[]') as text) as source_keys
from membership m join members s using (cluster_key)
left join methods t using (cluster_key) left join rights r using (cluster_key)
