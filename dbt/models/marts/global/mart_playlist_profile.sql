{{ config(tags=['cadence:daily']) }}

with profiles as (
    select s.platform,s.playlist_id,s.variant,s.stream,s.snapshot_id,s.observed_at,
        coalesce(p.title,s.title) as title,coalesce(p.description,s.description) as description,
        coalesce(p.owner_id,s.owner_id) as owner_id,coalesce(p.owner_name,s.owner_name) as owner_name,
        coalesce(nullif(s.owner_class,'unknown'),p.owner_class,'unknown') as owner_class,
        coalesce(p.followers,s.followers) as followers,
        case when s.stream='head' then s.page_total else s.track_count_reported end as track_count_reported
    from {{ ref('int_playlist__snapshots') }} s
    left join {{ ref('stg_playlist__snapshots') }} p on p.snapshot_id=s.page_snapshot_id
)
, projected as (
select cast(platform as text) as platform,cast(playlist_id as text) as playlist_id,cast(variant as text) as variant,
    cast(stream as text) as stream,cast(owner_class as text) as owner_class,
    cast({{ playlist_utc('observed_at') }} as timestamp) as observed_at,cast(snapshot_id as text) as snapshot_id,
    cast(title as text) as title,cast(description as text) as description,
    cast(owner_id as text) as owner_id,cast(owner_name as text) as owner_name,
    cast(followers as bigint) as followers,
    cast(followers-lag(followers) over (partition by platform,playlist_id,variant,stream order by observed_at,snapshot_id) as bigint) as follower_change,
    cast(track_count_reported as bigint) as track_count_reported
from profiles s
)
, annotated as (
select p.*, r.source_keys as _source_keys
from projected p
left join {{ ref('int_playlist__rights') }} r on r.snapshot_id=p.snapshot_id
)
{{ mdp_annotate('annotated') }}
