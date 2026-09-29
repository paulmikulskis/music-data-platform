select e.* from {{ ref('mart_playlist_events') }} e
where stream='head' and event_type in ('add','remove') and (
    not exists (select 1 from {{ ref('int_playlist__snapshots') }} s
        where s.platform=e.platform and s.playlist_id=e.playlist_id and s.variant=e.variant and s.stream=e.stream
        and s.extent_valid and {{ playlist_utc('s.observed_at') }}=case when e.event_type='add' then e.entered_after else e.removed_after end)
    or not exists (select 1 from {{ ref('int_playlist__snapshots') }} s
        where s.platform=e.platform and s.playlist_id=e.playlist_id and s.variant=e.variant and s.stream=e.stream
        and s.extent_valid and {{ playlist_utc('s.observed_at') }}=e.observed_at)
)
