{#- design mart_track_playlist_followers: one row per playlist occurrence of a recording per day,
    rebuilt in full every build from membership intervals, a date spine and each playlist's follower
    count as of that day, never from the current-membership table. For each day the stream-selection
    rule of mart_playlist_membership_current applies as of that day's end. An event counts on the day of
    its window's upper bound: an interval counts from the day of first_observed_at and stops the day
    before removed_by; an open interval ends at its stream's last complete observation plus the stream's
    window, or at target deactivation, whichever comes first. A target promoted again counts its later
    intervals. Every "now" is the bound cycle's close. -#}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with clock as (
    select cast({{ playlist_utc(mdp_identity_clock('select max(observed_at) from ' ~ ref('int_playlist__snapshots'))) }} as timestamp) as as_of
), observations as (
    select s.platform, s.playlist_id, s.variant, s.stream, s.snapshot_id,
        cast({{ playlist_utc('s.observed_at') }} as timestamp) as observed_at, s._target_id, s._source_key,
        coalesce(nullif(s.owner_class, 'unknown'), s.page_owner_class, 'unknown') as owner_class,
        s.effective_coverage = 'full' as complete, coalesce(s.extent_valid, false) as extent_valid,
        case s.cadence when 'daily' then 24 when 'weekly' then 168
            else {{ var('playlist_full_cadence_hours', 168) }} end + 24 as window_hours,
        lead(cast({{ playlist_utc('s.observed_at') }} as timestamp)) over (partition by s.platform, s.playlist_id, s.variant, s.stream
            order by s.observed_at, s.snapshot_id) as next_at
    from {{ ref('int_playlist__snapshots') }} s
), stream_days as (
    -- Each stream's latest observation as of every day it stands until the next one.
    select o.*, {{ mdp_day_series('o.observed_at', 'coalesce(o.next_at - interval \'1 day\', c.as_of)') }} as day
    from observations o cross join clock c
    where o.observed_at <= c.as_of
), selected as (
    select * from (
        select d.*, row_number() over (partition by d.platform, d.playlist_id, d.variant, d.day order by
            case when d.platform <> 'spotify' then 0
                 when d.stream = 'head' and d.extent_valid then 0
                 when d.stream = 'full' and cast({{ dbt.dateadd('hour', 'd.window_hours', 'd.observed_at') }} as date) >= d.day then 1
                 when d.stream = 'head' then 2 else 3 end, d.observed_at desc, d.stream) as preference
        from stream_days d
    ) ranked where preference = 1
), last_complete as (
    select platform, playlist_id, variant, stream, max(observed_at) as last_full_at, max(window_hours) as window_hours
    from observations where complete group by 1, 2, 3, 4
), revisions as (
    -- Deactivation counts frozen revisions: a target leaves at the first later revision of its set
    -- that lacks it, and a later revision that holds it again starts a new window.
    select distinct target_set_id, _revision_id, cast({{ playlist_utc('taken_at') }} as timestamp) as taken_at
    from {{ source('raw', 'targets') }}
    where resource_kind = 'playlist' and {{ mdp_revision_filter('_cycle_id') }}
), numbered as (
    select *, row_number() over (partition by target_set_id order by taken_at, _revision_id) as rev_no from revisions
), memberships as (
    select distinct t.id as target_id, n.target_set_id, n.rev_no, n.taken_at
    from {{ source('raw', 'targets') }} t join numbered n on n._revision_id = t._revision_id
    where t.resource_kind = 'playlist'
), islands as (
    select target_id, target_set_id, min(taken_at) as active_from, max(rev_no) as last_rev
    from (select m.*, m.rev_no - row_number() over (partition by m.target_id order by m.rev_no) as grp from memberships m) g
    group by target_id, target_set_id, grp
), windows as (
    select i.target_id, i.active_from, n.taken_at as active_to
    from islands i left join numbered n on n.target_set_id = i.target_set_id and n.rev_no = i.last_rev + 1
), targets as (
    -- Only a target with frozen revisions can be deactivated; one without any has no evidence either way.
    select distinct o.platform, o.playlist_id, o.variant, o._target_id from observations o
    where exists (select 1 from memberships m where m.target_id = o._target_id)
), intervals as (
    select m.platform, m.playlist_id, m.variant, m.stream, m.occurrence_key, m.interval_id,
        cast({{ playlist_utc('m.first_observed_at') }} as timestamp) as first_observed_at,
        cast({{ playlist_utc('m.removed_by') }} as timestamp) as removed_by,
        max(o.platform_track_id) as platform_track_id, min(o.position) as best_position
    from {{ ref('int_playlist__membership') }} m
    join {{ ref('int_playlist__observations') }} o
        on o.platform = m.platform and o.playlist_id = m.playlist_id and o.variant = m.variant and o.stream = m.stream
        and o.occurrence_key = m.occurrence_key and o.observed_at between m.first_observed_at and m.last_observed_at
    where o.item_type = 'track'
    group by 1, 2, 3, 4, 5, 6, 7, 8
), active as (
    select s.day, s.platform, s.playlist_id, s.variant, s.stream, s.owner_class, s.snapshot_id, s._source_key,
        i.occurrence_key, i.interval_id, i.platform_track_id, i.best_position
    from selected s
    join intervals i on i.platform = s.platform and i.playlist_id = s.playlist_id and i.variant = s.variant
        and i.stream = s.stream and cast(i.first_observed_at as date) <= s.day
    left join last_complete l on l.platform = s.platform and l.playlist_id = s.playlist_id
        and l.variant = s.variant and l.stream = s.stream
    where case when i.removed_by is not null then s.day < cast(i.removed_by as date)
               else s.day <= cast({{ dbt.dateadd('hour', 'l.window_hours', 'l.last_full_at') }} as date) end
      and not exists (
          -- A deactivated target stops at deactivation: its playlist counts only within an active window.
          select 1 from targets t where t.platform = s.platform and t.playlist_id = s.playlist_id and t.variant = s.variant
            and not exists (select 1 from windows w where w.target_id = t._target_id
                and cast(w.active_from as date) <= s.day and (w.active_to is null or s.day < cast(w.active_to as date))))
), followers as (
    -- Each playlist's follower count as of the day: its latest observation with a count on or before it.
    select p.platform, p.playlist_id, p.followers,
        {{ mdp_day_series('p.observed_at', "coalesce(p.next_at - interval '1 day', (select as_of from clock))") }} as day
    from (
        -- The profile already stores observed_at as a naive UTC timestamp.
        select platform, playlist_id, observed_at, followers,
            lead(observed_at) over (partition by platform, playlist_id order by observed_at, snapshot_id) as next_at
        from {{ ref('mart_playlist_profile') }} where followers is not null
    ) p
)
select a.day, a.platform, a.playlist_id, a.variant, a.stream, a.owner_class, a.occurrence_key, a.interval_id,
    a.platform_track_id, a.best_position, f.followers, r.source_keys
from active a
left join followers f on f.platform = a.platform and f.playlist_id = a.playlist_id and f.day = a.day
left join {{ ref('int_playlist__rights') }} r on r.snapshot_id = a.snapshot_id
