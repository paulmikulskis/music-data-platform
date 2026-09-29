{{ config(tags=['cadence:daily']) }}

-- Coverage age is measured to the close of the cycle that observed the row, never
-- to execution time; a row whose cycle is not mirrored (local fixtures) uses its own
-- observation time.
with closes as (
    select cast(id as text) as cycle_id, max(closed_at) as closed_at
    from {{ source('raw','cycles') }} where closed_at is not null group by 1
), history as (
    select s.*,c.closed_at as cycle_closed_at,
        max(case when effective_coverage='full' then observed_at end) over (
        partition by platform,playlist_id,variant,stream order by observed_at,snapshot_id
        rows unbounded preceding) as last_full_at
    from {{ ref('int_playlist__snapshots') }} s
    left join closes c on c.cycle_id=cast(s._cycle_id as text)
)
, projected as (
select cast(platform as text) as platform,cast(playlist_id as text) as playlist_id,cast(variant as text) as variant,
    cast(stream as text) as stream,
    cast(snapshot_id as text) as snapshot_id,cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,
    cast(_cycle_id as text) as cycle_id,cast(_source_key as text) as source_key,
    cast(effective_coverage as text) as coverage,cast(observation as text) as observation,
    cast(items_observed as bigint) as items_observed,cast(items_visible as bigint) as items_visible,
    cast(case when stream='head' then page_total else track_count_reported end as bigint) as track_count_reported,
    cast(case when effective_coverage='full' then 1.0 else effective_items_visible*1.0/nullif(case when stream='head' then page_total else track_count_reported end,0) end as double precision) as observed_share,
    cast({{ playlist_utc('last_full_at') }} as timestamp) as last_full_at,
    cast({{ playlist_utc('coalesce(cycle_closed_at, observed_at)') }} as timestamp) as as_of,
    cast({{ dbt.datediff('last_full_at', 'coalesce(cycle_closed_at, observed_at)', 'second') }} / 86400.0 as double precision) as days_since_full
from history
)
, annotated as (
select p.*, r.source_keys as _source_keys
from projected p
left join {{ ref('int_playlist__rights') }} r on r.snapshot_id=p.snapshot_id
)
{{ mdp_annotate('annotated') }}
