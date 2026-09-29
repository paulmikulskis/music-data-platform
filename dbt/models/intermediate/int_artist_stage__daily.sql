{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}") }}
-- Each looked-up MusicBrainz artist's stage from its newest catalog reading, under dbt/seeds/artist_stage_thresholds.csv.
-- Only counted release groups (Album or EP with no excluded secondary type, today) reach the established group
-- rule; first release years and the emerging group rule read every release group. Years compare calendar years.
-- Row-number filters in the staging views read as a few rows to Postgres, so the pre-hook keeps joins hashed.
with rules as (
    select name, value from {{ ref('artist_stage_thresholds') }}
), thresholds as (
    select cast(max(case when name = 'established_min_release_groups' then value end) as integer) as established_groups,
        cast(max(case when name = 'established_min_years' then value end) as integer) as established_years,
        cast(max(case when name = 'emerging_max_release_groups' then value end) as integer) as emerging_groups,
        cast(max(case when name = 'emerging_max_years' then value end) as integer) as emerging_years
    from rules
), counted_primary as (
    select distinct trim(t.value) as primary_type from rules r {{ mdp_list_elements('r.value', 't') }}
    where r.name = 'established_counted_primary_types' and trim(t.value) <> ''
), excluded_secondary as (
    select distinct trim(t.value) as secondary_type from rules r {{ mdp_list_elements('r.value', 't') }}
    where r.name = 'established_excluded_secondary_types' and trim(t.value) <> ''
), clock as (
    select cast(extract(year from {{ mdp_cycle_day() }}) as integer) as cycle_year
), lookups as (
    select mb_artist_gid, status, input_version, config_version from {{ ref('stg_mb__artist_catalog') }}
), kinds as (
    -- The release groups of each artist's newest lookup only.
    select g.mb_artist_gid, g.primary_type, g.secondary_types, g.release_groups, g.first_release_year
    from {{ ref('stg_mb__artist_release_groups') }} g
    join lookups c on c.mb_artist_gid = g.mb_artist_gid and c.input_version = g.input_version
        and c.config_version = g.config_version
), excluded_kinds as (
    select distinct k.mb_artist_gid, k.primary_type, k.secondary_types
    from kinds k {{ mdp_list_elements('k.secondary_types', 's') }}
    join excluded_secondary x on x.secondary_type = trim(s.value)
), depth as (
    select k.mb_artist_gid, sum(k.release_groups) as release_groups,
        sum(case when p.primary_type is not null and e.mb_artist_gid is null then k.release_groups else 0 end)
            as counted_release_groups,
        min(k.first_release_year) as first_release_year
    from kinds k
    left join counted_primary p on p.primary_type = k.primary_type
    left join excluded_kinds e on e.mb_artist_gid = k.mb_artist_gid and e.primary_type = k.primary_type
        and e.secondary_types = k.secondary_types
    group by 1
), staged as (
    select c.mb_artist_gid, c.status, coalesce(d.release_groups, 0) as release_groups,
        coalesce(d.counted_release_groups, 0) as counted_release_groups, d.first_release_year,
        case when c.status <> 'found' then 'unknown'
            when coalesce(d.counted_release_groups, 0) >= t.established_groups
                or d.first_release_year <= k.cycle_year - t.established_years then 'established'
            when coalesce(d.release_groups, 0) <= t.emerging_groups
                and (d.first_release_year is null or d.first_release_year >= k.cycle_year - t.emerging_years) then 'emerging'
            else 'developing' end as artist_stage
    from lookups c
    left join depth d on d.mb_artist_gid = c.mb_artist_gid
    cross join thresholds t cross join clock k
)
select cast(mb_artist_gid as text) as mb_artist_gid, cast(status as text) as status,
    cast(release_groups as bigint) as release_groups, cast(counted_release_groups as bigint) as counted_release_groups,
    cast(first_release_year as integer) as first_release_year, cast(artist_stage as text) as artist_stage
from staged
