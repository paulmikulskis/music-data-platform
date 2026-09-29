-- Every removal is proved by a full observation that lacks the occurrence and follows
-- a full presence inside its interval. Joins, not correlated subqueries, so the check
-- stays linear.
with removals as (
    select * from {{ ref('mart_playlist_events') }} where event_type in ('remove','left_head')
), presence as (
    select distinct e.platform,e.playlist_id,e.variant,e.stream,e.occurrence_key,e.interval_id
    from removals e
    join {{ ref('int_playlist__observations') }} p
        on p.platform=e.platform and p.playlist_id=e.playlist_id and p.variant=e.variant and p.stream=e.stream
        and p.occurrence_key=e.occurrence_key and p.effective_coverage='full'
        and {{ playlist_utc('p.observed_at') }}=e.removed_after
        and {{ playlist_utc('p.observed_at') }}>=e.first_observed_at
        and {{ playlist_utc('p.observed_at') }}<e.removed_by
)
select e.* from removals e
left join {{ ref('int_playlist__snapshots') }} s
    on e.platform=s.platform and e.playlist_id=s.playlist_id and e.variant=s.variant and e.stream=s.stream
    and e.snapshot_id=s.snapshot_id
left join presence p
    on p.platform=e.platform and p.playlist_id=e.playlist_id and p.variant=e.variant and p.stream=e.stream
    and p.occurrence_key=e.occurrence_key and p.interval_id=e.interval_id
left join {{ ref('int_playlist__observations') }} held
    on held.platform=e.platform and held.playlist_id=e.playlist_id and held.variant=e.variant
    and held.stream=e.stream and held.snapshot_id=e.snapshot_id and held.occurrence_key=e.occurrence_key
where s.effective_coverage is null or s.effective_coverage<>'full' or p.platform is null
    or held.snapshot_id is not null
