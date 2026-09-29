{#- design: artist × label × interval. Who is on this act, since when, and who owns that label? Each dated
    affiliation the spine landed (a recording contract, a personal label or publisher, ownership or founding
    of the label), with the label's root owner and its current distributor. Dates carry the precision
    MusicBrainz records (year, month or day). Open data, not contract truth. -#}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}") }}
with generations as ({{ mdp_reference_generations() }}),
reference as ({{ mdp_reference_current('generations', ['l_artist_label', 'l_label_label', 'artist']) }}),
edges as (
    select * from reference where mb_table = 'l_artist_label' and not tombstoned
),
artists as (
    select artist_id, artist_gid, name from reference where mb_table = 'artist' and not tombstoned
),
distributors as (
    select label_id1 as label_id, count(distinct label_id0) as distributors, min(label_id0) as distributor_id
    from reference
    where mb_table = 'l_label_label' and not tombstoned and link_type = 'label distribution'
        and not coalesce(ended, false) and end_year is null
    group by label_id1
)
select
    cast(e.link_id as bigint) as link_id,
    cast(a.artist_gid as text) as artist_gid,
    cast(a.name as text) as artist_name,
    cast(o.label_gid as text) as label_gid,
    cast(o.label_name as text) as label_name,
    cast(e.link_type as text) as link_type,
    cast(cast(e.begin_year as text)
        || case when e.begin_month is null then '' else '-' || lpad(cast(e.begin_month as text), 2, '0') end
        || case when e.begin_month is null or e.begin_day is null then '' else '-' || lpad(cast(e.begin_day as text), 2, '0') end
        as text) as begin_on,
    cast(cast(e.end_year as text)
        || case when e.end_month is null then '' else '-' || lpad(cast(e.end_month as text), 2, '0') end
        || case when e.end_month is null or e.end_day is null then '' else '-' || lpad(cast(e.end_day as text), 2, '0') end
        as text) as end_on,
    cast(coalesce(e.ended, false) or e.end_year is not null as boolean) as ended,
    cast(o.root_label_gid as text) as root_label_gid,
    cast(o.root_label_name as text) as root_label_name,
    cast(o.ambiguous_parent as boolean) as ambiguous_parent,
    cast(case when d.distributors = 1 then dl.label_gid end as text) as distributor_label_gid,
    cast(case when d.distributors = 1 then dl.label_name end as text) as distributor_label_name,
    cast('open data, not contract truth' as text) as evidence,
    cast('["mb_spine"]' as text) as _source_keys
from edges e
join artists a on a.artist_id = e.artist_id
join {{ ref('int_label__ownership_closure') }} o on o.label_id = e.label_id
left join distributors d on d.label_id = e.label_id
left join {{ ref('int_label__ownership_closure') }} dl on dl.label_id = d.distributor_id
