-- Every add has a proof: an earlier full observation of its stream, at its
-- entered_after bound, that lacks the occurrence. Joins, not nested correlated
-- subqueries, so the check stays linear as history grows.
with adds as (
    select * from {{ ref('mart_playlist_events') }} where event_type in ('add','entered_head')
), proofs as (
    select distinct e.platform,e.playlist_id,e.variant,e.stream,e.occurrence_key,e.interval_id
    from adds e
    join {{ ref('int_playlist__snapshots') }} s
        on s.platform=e.platform and s.playlist_id=e.playlist_id and s.variant=e.variant and s.stream=e.stream
        and s.fetch_surface<>'sp_playlist_page' and s.effective_coverage='full'
        and {{ playlist_utc('s.observed_at') }}=e.entered_after
        and {{ playlist_utc('s.observed_at') }}<e.first_observed_at
    left join {{ ref('int_playlist__observations') }} o
        on o.platform=s.platform and o.playlist_id=s.playlist_id and o.variant=s.variant and o.stream=s.stream
        and o.snapshot_id=s.snapshot_id and o.occurrence_key=e.occurrence_key
    where o.snapshot_id is null
)
select e.* from adds e
left join proofs p
    on p.platform=e.platform and p.playlist_id=e.playlist_id and p.variant=e.variant and p.stream=e.stream
    and p.occurrence_key=e.occurrence_key and p.interval_id=e.interval_id
where p.platform is null
