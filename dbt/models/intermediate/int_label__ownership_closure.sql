{#- design: each label in the landed spine closure and the root owner its current parent chain ends at.
    A parent is entity0 of a current `label ownership` or `imprint` relationship ("is/was the parent label
    of", "has imprint"); a renamed label follows `label rename` to its successor (entity1). A step with two
    current parents stops the chain there, unresolved, rather than pick one. Distribution is not ownership:
    int_artist__contract_edges reports a label's distributor apart. Open data, not contract truth. -#}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}") }}
with recursive generations as ({{ mdp_reference_generations() }}),
reference as ({{ mdp_reference_current('generations', ['label', 'l_label_label']) }}),
labels as (
    select label_id, label_gid, name from reference where mb_table = 'label' and not tombstoned
),
edges as (
    select label_id1 as child_id, label_id0 as parent_id
    from reference
    where mb_table = 'l_label_label' and not tombstoned and link_type in ('label ownership', 'imprint')
        and not coalesce(ended, false) and end_year is null
    union
    select label_id0, label_id1
    from reference
    where mb_table = 'l_label_label' and not tombstoned and link_type = 'label rename'
),
parents as (
    select child_id, count(distinct parent_id) as parents, min(parent_id) as parent_id
    from edges
    group by child_id
),
walk as (
    select label_id, label_id as node_id, 0 as depth from labels
    union all
    select w.label_id, p.parent_id, w.depth + 1
    from walk w
    join parents p on p.child_id = w.node_id and p.parents = 1
    where w.depth < 10
),
ends as (
    select label_id, node_id, depth, row_number() over (partition by label_id order by depth desc) as _rn
    from walk
)
select
    cast(l.label_id as bigint) as label_id,
    cast(l.label_gid as text) as label_gid,
    cast(l.name as text) as label_name,
    cast(case when coalesce(p.parents, 0) > 1 then null else r.label_gid end as text) as root_label_gid,
    cast(case when coalesce(p.parents, 0) > 1 then null else r.name end as text) as root_label_name,
    cast(e.depth as bigint) as ownership_depth,
    cast(coalesce(p.parents, 0) > 1 as boolean) as ambiguous_parent,
    cast('["mb_spine"]' as text) as _source_keys
from labels l
join ends e on e.label_id = l.label_id and e._rn = 1
left join parents p on p.child_id = e.node_id
left join labels r on r.label_id = e.node_id
