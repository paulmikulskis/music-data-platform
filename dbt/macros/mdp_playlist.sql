{# Shared playlist SQL. Global models and tenant copies call the same macro over their own
   inputs, so a tenant cycle rebuilds membership exactly as the global job does. #}

{% macro mdp_playlist_item_rows(manifest) %}
with normalized as (
    select {% for column in ['platform','playlist_id','variant','stream','observation_group','item_type','platform_item_id','featured_track_id','snapshot_id','observed_at','position',
    'platform_track_id','platform_row_id','occurrence','occurrence_key','occurrence_inferred','title',
    'artist_names','platform_artist_ids','platform_album_id','duration_ms','is_explicit','playcount',
    'added_at','added_by','isrc','fetch_surface','_run_id','_dump_id','_landed_seq','_cycle_id',
    '_revision_id','_target_id','_request_id','_source_key','_ingested_at','_extra'] %}
    {% if column == "added_by" %}{{ mdp_pseudonym("added_by") }} as added_by{% elif column == "_extra" %}cast(null as {{ dbt.type_string() }}) as _extra{% elif column == "stream" %}coalesce(stream, case when platform='spotify' then 'head' else 'full' end) as stream{% elif column == "item_type" %}coalesce(item_type, 'track') as item_type{% elif column == "occurrence_key" %}case when item_type is null and occurrence_inferred and occurrence_key not like 'track:%' then 'track:' || occurrence_key else occurrence_key end as occurrence_key{% elif column == "platform_item_id" %}coalesce(platform_item_id, platform_track_id) as platform_item_id{% else %}{{ column }}{% endif %}{% if not loop.last %},{% endif %}
{% endfor %}
    from {{ source('raw', 'playlist_items') }}
    where {{ manifest }}
), visible as (
    select *,row_number() over (
        partition by platform,playlist_id,variant,stream,snapshot_id,position
        order by _landed_seq desc,_dump_id desc) as _rn
    from normalized
)
select * from visible where _rn=1
{% endmacro %}

{# Spotify track page artist ids (sp_track_artists) visible to `manifest`: one row per track
   and credited artist, the newest configuration's landing first. #}
{% macro mdp_playlist_track_artists(manifest) %}
with visible as (
    select * from {{ source('raw', 'sp_track_artists') }}
    where {{ manifest }}
), ranked as (
    select *, row_number() over (
        partition by track_id, artist_id
        order by run_admitted_at desc, _landed_seq desc, _dump_id desc
    ) as _rn
    from visible
)
select
    cast(track_id as text) as platform_track_id,
    cast(artist_id as text) as platform_artist_id,
    cast(artist_name as text) as artist_name,
    cast(position as bigint) as position,
    cast(album_id as text) as platform_album_id,
    cast(title as text) as title,
    cast(duration_ms as bigint) as duration_ms,
    input_ref, input_version, config_version, run_admitted_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
from ranked
where _rn = 1
{% endmacro %}


{% macro mdp_playlist_snapshot_rows(manifest, items) %}
{#- Every owner decision reads the observed owner class (`owner_class_observed`, or for rows landed
    before it the same payload evidence), never the frozen label in `owner_class`. The rules are
    generated from mdp_functions/owners.py into mdp_owner_rules.sql, the source the raw owner-name
    repair also applies: only a row whose observed owner is the platform's own account keeps its
    description, owner id and name, so no mart can serve a private owner's; `owner_key` (that id,
    or any other owner's pseudonym) is for joins and never served. The
    served class is the observed one, which a label only fills when unknown or refines from editorial
    to chart. -#}
with ranked_landings as (
    select *, {{ mdp_owner_observed_class() }} as observed_class,
        row_number() over (partition by platform, playlist_id, variant, stream, snapshot_id
            order by _landed_seq desc, _dump_id desc) as landing_rank
    from {{ source('raw', 'playlist_snapshots') }} where {{ manifest }}
), metadata_groups as (
    select *,
        count(nullif(title, '')) over (partition by platform, playlist_id, variant
            order by observed_at, _landed_seq, _dump_id rows unbounded preceding) as title_group,
        count(nullif(observed_class, 'unknown')) over (partition by platform, playlist_id, variant
            order by observed_at, _landed_seq, _dump_id rows unbounded preceding) as owner_group
    from ranked_landings where landing_rank = 1
), landed as (
    select *,
        max(nullif(title, '')) over (partition by platform, playlist_id, variant, title_group) as last_title,
        max(nullif(observed_class, 'unknown')) over (partition by platform, playlist_id, variant, owner_group) as last_owner,
        max(case when observed_class <> 'unknown' then {{ mdp_owner_served_class('observed_class') }} end) over (partition by platform, playlist_id, variant, owner_group) as last_owner_class,
        max(case when {{ mdp_owner_is_public('observed_class') }} then owner_name end)
            over (partition by platform, playlist_id, variant, owner_group) as last_owner_name,
        max(case when {{ mdp_owner_is_public('observed_class') }} then owner_id end)
            over (partition by platform, playlist_id, variant, owner_group) as last_owner_id
    from metadata_groups
), normalized as (
    select platform, playlist_id, variant, coalesce(stream, case when platform='spotify' then 'head' else 'full' end) as stream, observation_group, snapshot_id, position, observed_at,
        case when platform = 'apple_music' then coalesce(nullif(title, ''), last_title) else title end as title,
        case when {{ mdp_owner_is_public('observed_class') }} then description end as description,
        case when {{ mdp_owner_is_public('observed_class') }} then owner_id
            when platform = 'apple_music' and observed_class = 'unknown' and {{ mdp_owner_is_public('last_owner') }} then last_owner_id end as owner_id,
        case when {{ mdp_owner_is_public('observed_class') }} then owner_id else {{ mdp_pseudonym('owner_id') }} end as owner_key,
        case when platform = 'apple_music' and observed_class = 'unknown' and {{ mdp_owner_is_public('last_owner') }} then last_owner_name
            else {{ mdp_owner_served_name('observed_class') }} end as owner_name,
        case when platform = 'apple_music' and observed_class = 'unknown'
            then coalesce(last_owner_class, {{ mdp_owner_served_class('observed_class') }})
            else {{ mdp_owner_served_class('observed_class') }} end as owner_class,
        observed_class as owner_class_observed,
        followers, track_count_reported, items_observed, coverage, observation, content_ref, platform_version, snapshot_hash, membership_hash, content_hash, case when {{ mdp_owner_is_public('observed_class') }} then canonical_url end as canonical_url, etag, continuation, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as attributes, fetch_surface, tier, cadence, _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id, _request_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra
    from landed
), visible as (
    select *, row_number() over (
        partition by platform, playlist_id, variant, stream, snapshot_id
        order by _landed_seq desc, _dump_id desc
    ) as _rn
    from normalized
), counts as (
    select platform, playlist_id, variant, stream, snapshot_id, fetch_surface, count(*) as items_visible
    from {{ items }} group by 1,2,3,4,5,6
), checked as (
    select v.*, coalesce(c.items_visible, 0) as items_visible,
        v.coverage = 'full' and v.observation = 'content'
        and coalesce(c.items_visible, 0) = v.items_observed
        and not v.continuation
        as content_complete
    from visible v left join counts c
        on v.platform=c.platform and v.playlist_id=c.playlist_id
        and v.variant=c.variant and v.stream=c.stream and v.snapshot_id=c.snapshot_id
        and v.fetch_surface=c.fetch_surface
    where v._rn=1
)
select s.*,
    -- Partial referenced content still proves presence, but never absence.
    case when s.observation='unchanged' and r.observation='content' then r.snapshot_id
         when s.observation='content' then s.snapshot_id end as effective_content_id,
    case when s.observation='unchanged' then r.items_visible else s.items_visible end as effective_items_visible,
    case when s.content_complete or (s.observation='unchanged' and s.coverage <> 'partial' and r.content_complete)
         then 'full' else 'partial' end as effective_coverage
from checked s left join checked r
    on s.content_ref=r.snapshot_id and s.platform=r.platform and s.playlist_id=r.playlist_id
    and s.variant=r.variant and s.stream=r.stream and s.fetch_surface=r.fetch_surface
    and r.observed_at <= s.observed_at
    and r.observation='content'
    and s.content_hash=r.content_hash and s.membership_hash=r.membership_hash
    and (s.track_count_reported=r.track_count_reported or
         (s.track_count_reported is null and r.track_count_reported is null))
    and s.continuation=r.continuation
{% endmacro %}


{# `playlists`, when given, restricts to playlists it lists and keeps every item of each. #}
{% macro mdp_playlist_snapshots(stg_snapshots, stg_items, playlists=none) %}
-- Page evidence must be from this target's paired fetch, never just the same day.
with paired as (
    select s.*, p.snapshot_id as page_snapshot_id, p.track_count_reported as page_total,
        p.owner_class as page_owner_class,
        row_number() over (partition by s.platform,s.playlist_id,s.variant,s.stream,s.snapshot_id
            order by p.observed_at desc,p.snapshot_id desc) as page_rank
    from {{ stg_snapshots }} s
    left join {{ stg_snapshots }} p
        on s.platform='spotify' and s.stream='head' and p.platform=s.platform
        and p.playlist_id=s.playlist_id and p.variant=s.variant and p.stream=s.stream
        and p.fetch_surface='sp_playlist_page' and p.observation_group=s.observation_group
        and p._run_id=s._run_id and p._target_id=s._target_id and p._cycle_id=s._cycle_id
        and abs({{ dbt.datediff('s.observed_at','p.observed_at','second') }}) <= 300
    where s.fetch_surface<>'sp_playlist_page'
    {% if playlists is not none %}
        and exists (select 1 from {{ playlists }} l
            where l.platform=s.platform and l.playlist_id=s.playlist_id and l.variant=s.variant)
    {% endif %}
)
-- The page's first 30 rows (all rows for shorter lists) that agree with the embed at
-- the same position, by track id or by title with durations within two seconds.
, first_rows as (
select s.snapshot_id, count(*) as matched
from paired s
join {{ stg_items }} p
    on p.snapshot_id=s.page_snapshot_id and p.platform=s.platform and p.playlist_id=s.playlist_id
    and p.variant=s.variant and p.stream=s.stream and p.position<=30
join {{ stg_items }} e
    on e.snapshot_id=s.effective_content_id and e.platform=s.platform and e.playlist_id=s.playlist_id
    and e.variant=s.variant and e.stream=s.stream and e.position=p.position
where s.page_rank=1
    and (p.platform_track_id=e.platform_track_id or
        (p.title=e.title and abs(p.duration_ms-e.duration_ms)<=2000))
group by s.snapshot_id
)
-- Extent is corroborated only below 100: there the embed cannot be truncated, so an
-- insertion or deletion between the two fetches shows up as a count mismatch.
, evidence as (
select s.*,
    coalesce(s.page_total between 0 and 99 and s.effective_coverage='full'
        and s.effective_items_visible=s.page_total
        and coalesce(f.matched,0) = case when s.page_total<30 then s.page_total else 30 end, false) as extent_valid
from paired s left join first_rows f on f.snapshot_id=s.snapshot_id
where s.page_rank=1
)
select platform, playlist_id, variant, stream, observation_group, snapshot_id, position, observed_at, title, description, owner_id, owner_key, owner_name,
    -- The embed never observes its owner: its own observation comes first, then the paired page's
    -- class, and the target's label (already in owner_class) only when no page was paired.
    case when owner_class_observed = 'unknown' and page_snapshot_id is not null
        then coalesce(nullif(page_owner_class, 'unknown'), owner_class)
        else owner_class end as owner_class,
    followers, track_count_reported, items_observed, coverage, observation, content_ref, platform_version, snapshot_hash, membership_hash, content_hash, canonical_url, etag, continuation, attributes, fetch_surface, tier, cadence, _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id, _request_id, _source_key, _ingested_at, cast(null as {{ 'jsonb' if target.type == 'postgres' else 'json' }}) as _extra, _rn, items_visible, content_complete, effective_content_id, effective_items_visible, page_snapshot_id, page_total, page_owner_class, page_rank, extent_valid,
    -- Fewer rows than positions 1 to min(100, total) means rows are missing. More rows
    -- than the total is a fetch race: the embed is still whole, only its extent is not.
    case when stream='head' and page_total>=0
        and effective_items_visible<least(100,page_total) then 'partial'
        else effective_coverage end as effective_coverage
from evidence
{% endmacro %}


{% macro mdp_playlist_observations(snapshots, stg_snapshots, stg_items) %}
-- The page supplies enrichment only. It never establishes Spotify membership.
with members as (
    select s.platform, s.playlist_id, s.variant, s.stream, s.snapshot_id, s.observed_at,
        s.effective_coverage, s.extent_valid, s.page_total, s.page_snapshot_id as paired_page_id,
        coalesce(nullif(s.owner_class, 'unknown'),s.page_owner_class,'unknown') as owner_class, s.membership_hash as snapshot_hash, s._cycle_id,
        i.item_type, i.platform_item_id, i.featured_track_id, i.position, i.occurrence_key, i.occurrence_inferred, i.platform_track_id, i.title, i.duration_ms,
        i.platform_album_id, i.platform_artist_ids, i.playcount
    from {{ snapshots }} s
    join {{ stg_items }} i
        on i.snapshot_id=s.effective_content_id and i.platform=s.platform
        and i.playlist_id=s.playlist_id and i.variant=s.variant and i.stream=s.stream
        and i.fetch_surface=s.fetch_surface

), candidates as (
    select m.*, p.snapshot_id as page_snapshot_id,
        p.platform_track_id as page_track_id, p.title as page_title,
        p.duration_ms as page_duration_ms, p.platform_album_id as page_album_id,
        p.platform_artist_ids as page_artist_ids, p.playcount as page_playcount,
        row_number() over (partition by m.platform,m.playlist_id,m.variant,m.stream,m.snapshot_id,m.position
            order by ps.observed_at desc, ps.snapshot_id desc) as rn
    from members m
    left join {{ stg_snapshots }} ps
        on m.platform='spotify' and ps.platform=m.platform and ps.playlist_id=m.playlist_id
        and ps.variant=m.variant and ps.fetch_surface='sp_playlist_page'
        and ps.snapshot_id=m.paired_page_id and ps.stream=m.stream
    left join {{ stg_items }} p
        on p.snapshot_id=ps.effective_content_id and p.platform=m.platform
        and p.playlist_id=m.playlist_id and p.variant=m.variant and p.stream=m.stream and p.position=m.position
), matched as (
    select *, coalesce(page_track_id=platform_track_id or
        (page_title=title and abs(page_duration_ms-duration_ms)<=2000), false) as page_match
    from candidates where rn=1
)
select platform,playlist_id,variant,stream,item_type,platform_item_id,featured_track_id,
    extent_valid,page_total,owner_class,snapshot_id,observed_at,effective_coverage,snapshot_hash,
    position,occurrence_key,occurrence_inferred,platform_track_id,title,duration_ms,page_match,page_track_id,
    case when platform<>'spotify' or stream='full' then platform_album_id
         when page_match then page_album_id end as platform_album_id,
    case when platform<>'spotify' or stream='full' then platform_artist_ids
         when page_match then page_artist_ids end as platform_artist_ids,
    case when page_match then page_playcount end as playcount
from matched
{% endmacro %}


{% macro mdp_playlist_membership(observations, snapshots) %}
-- Islands come from each occurrence's present rows, never from a snapshot x occurrence
-- grid, so cost grows with the rows observed. A stream's full observations are numbered
-- in observation order: `hi` counts those at or before a snapshot, `lo` those before it.
-- A full observation lacking the occurrence lies between two of its present rows a and b
-- exactly when lo(b) > hi(a).
with snapshots as (
    -- Observations of one stream at the same instant cannot be ordered, so a tie
    -- proves presence only: it counts as partial and never proves an absence.
    select platform,playlist_id,variant,stream,snapshot_id,observed_at,
        coalesce(extent_valid,false) as extent_valid,
        case when effective_coverage='full'
            and count(*) over (partition by platform,playlist_id,variant,stream,observed_at)=1
            then 1 else 0 end as is_full
    from {{ snapshots }}
), numbered as (
    select *,
        sum(is_full) over (partition by platform,playlist_id,variant,stream
            order by observed_at,snapshot_id rows unbounded preceding) as hi,
        min(case when is_full=1 then observed_at end) over (partition by platform,playlist_id,variant,stream) as baseline_at
    from snapshots
), fulls as (
    select platform,playlist_id,variant,stream,hi as full_seq,snapshot_id,observed_at,extent_valid
    from numbered where is_full=1
), marks as (
    -- Per snapshot: the last full observation before it, the last one at or before it,
    -- and the first one after it.
    select n.platform,n.playlist_id,n.variant,n.stream,n.snapshot_id,n.observed_at,n.is_full,
        n.hi-n.is_full as lo,n.hi,n.baseline_at,
        b.observed_at as before_at,b.extent_valid as before_extent,
        c.observed_at as full_at,c.extent_valid as full_extent,
        a.snapshot_id as after_id,a.observed_at as after_at,a.extent_valid as after_extent
    from numbered n
    left join fulls b on b.platform=n.platform and b.playlist_id=n.playlist_id and b.variant=n.variant
        and b.stream=n.stream and b.full_seq=n.hi-n.is_full
    left join fulls c on c.platform=n.platform and c.playlist_id=n.playlist_id and c.variant=n.variant
        and c.stream=n.stream and c.full_seq=n.hi
    left join fulls a on a.platform=n.platform and a.playlist_id=n.playlist_id and a.variant=n.variant
        and a.stream=n.stream and a.full_seq=n.hi+1
), present as (
    -- A present row opens an island when a full absence precedes it, and closes one
    -- when a full absence follows it.
    select m.*,o.occurrence_key,
        case when lag(m.hi) over w is null or m.lo>lag(m.hi) over w then 1 else 0 end as opens,
        case when lead(m.lo) over w is null or lead(m.lo) over w>m.hi then 1 else 0 end as closes
    from {{ observations }} o
    join marks m on m.snapshot_id=o.snapshot_id and m.platform=o.platform and m.playlist_id=o.playlist_id
        and m.variant=o.variant and m.stream=o.stream
    window w as (partition by o.platform,o.playlist_id,o.variant,o.stream,o.occurrence_key
        order by m.observed_at,m.snapshot_id)
), numbered_islands as (
    select *,sum(opens) over (partition by platform,playlist_id,variant,stream,occurrence_key
        order by observed_at,snapshot_id rows unbounded preceding) as island
    from present
), islands as (
    -- An island is keyed by the observation that opened it, never by a running
    -- count, so an observation landing late cannot renumber other intervals.
    select platform,playlist_id,variant,stream,occurrence_key,
        max(case when opens=1 then snapshot_id end) as interval_id,
        max(case when opens=1 then before_at end) as entered_after,
        bool_or(case when opens=1 then before_extent end) as entered_after_extent,
        min(observed_at) as first_observed_at,max(observed_at) as last_observed_at,
        min(baseline_at) as baseline_at,
        min(case when is_full=1 then observed_at end) as first_full_at,
        bool_or(is_full=1) as has_full,
        -- The last full observation at or before the island's last row is its last full presence.
        max(case when closes=1 then full_at end) as last_full_at,
        bool_or(case when closes=1 then full_extent end) as last_full_extent,
        max(case when closes=1 then after_at end) as removed_by,
        max(case when closes=1 then after_id end) as removed_by_snapshot_id,
        bool_or(case when closes=1 then after_extent end) as removed_by_extent
    from numbered_islands group by platform,playlist_id,variant,stream,occurrence_key,island
)
-- Full absence closes every island. A removal event additionally needs a full presence
-- observation in that island; partial-only presence cannot prove one.
select platform,playlist_id,variant,stream,occurrence_key,cast(interval_id as text) as interval_id,
    entered_after,first_observed_at,last_observed_at,
    case when removed_by is not null and has_full then last_full_at end as removed_after,removed_by,
    coalesce(first_observed_at=baseline_at and first_observed_at=first_full_at, false) as is_baseline,
    cast({{ dbt.datediff('entered_after', 'first_observed_at', 'second') }} / 3600.0 as double precision) as uncertainty_hours,
    coalesce(entered_after_extent,false) as entered_after_extent,
    coalesce(removed_by is not null and has_full and last_full_extent,false) as removed_after_extent,
    coalesce(removed_by_extent,false) as removed_by_extent,
    -- The full observation at removed_by that lacks the occurrence: the removal's proof.
    cast(removed_by_snapshot_id as text) as removed_by_snapshot_id
from islands
{% endmacro %}


{# Typed item events with bounds, before rights annotation. #}
{% macro mdp_playlist_events(observations, membership, snapshots) %}
with tied as (
    -- Membership's rule: observations of one stream at the same instant cannot be
    -- ordered, so a tie counts as partial and proves no move either.
    select platform,playlist_id,variant,stream,observed_at
    from {{ snapshots }}
    group by platform,playlist_id,variant,stream,observed_at
    having count(*)>1
), observed as (
    select o.*, m.interval_id,m.entered_after,m.first_observed_at,m.removed_after,m.removed_by,
        m.is_baseline,m.uncertainty_hours,m.entered_after_extent,m.removed_after_extent,m.removed_by_extent,
        m.removed_by_snapshot_id
    from {{ observations }} o join {{ membership }} m
        on o.platform=m.platform and o.playlist_id=m.playlist_id and o.variant=m.variant and o.stream=m.stream
        and o.occurrence_key=m.occurrence_key and o.observed_at>=m.first_observed_at
        and (m.removed_by is null or o.observed_at<m.removed_by)
), complete as (
    -- Moves compare complete observations only: a partial list can carry shifted
    -- inferred ordinals (an unrecoverable row before a duplicate), so it proves presence only.
    select o.platform,o.playlist_id,o.variant,o.stream,o.occurrence_key,o.interval_id,o.snapshot_id,
        lag(o.position) over w as previous_position,
        lag(o.snapshot_hash) over w as previous_hash,
        lag(o.observed_at) over w as previous_at
    from observed o left join tied t
        on t.platform=o.platform and t.playlist_id=o.playlist_id and t.variant=o.variant
        and t.stream=o.stream and t.observed_at=o.observed_at
    where o.effective_coverage='full' and t.observed_at is null
    window w as (partition by o.platform,o.playlist_id,o.variant,o.stream,o.occurrence_key,o.interval_id
        order by o.observed_at,o.snapshot_id)
), presence as (
    select o.*, c.previous_position, c.previous_hash, c.previous_at,
        row_number() over (partition by o.platform,o.playlist_id,o.variant,o.stream,o.occurrence_key,o.interval_id
            order by o.observed_at,o.snapshot_id) as first_row,
        row_number() over (partition by o.platform,o.playlist_id,o.variant,o.stream,o.occurrence_key,o.interval_id
            order by o.observed_at desc,o.snapshot_id desc) as last_row
    from observed o left join complete c
        on c.platform=o.platform and c.playlist_id=o.playlist_id and c.variant=o.variant and c.stream=o.stream
        and c.occurrence_key=o.occurrence_key and c.interval_id=o.interval_id and c.snapshot_id=o.snapshot_id
), events as (
    -- Head transitions are true adds and removes only when the observations on both
    -- sides corroborated their extent (int_playlist__membership carries those flags).
    select *,case when entered_after is not null then
        case when stream='head' and not (extent_valid and entered_after_extent)
            then 'entered_head' else 'add' end
        when is_baseline then 'baseline' else 'entry_unknown' end as event_type,
        observed_at as event_at,uncertainty_hours as event_uncertainty
    from presence where first_row=1
    union all
    select *,case when stream='head' and not (removed_after_extent and removed_by_extent)
        then 'left_head' else 'remove' end,removed_by,
        cast({{ dbt.datediff('removed_after', 'removed_by', 'second') }} / 3600.0 as double precision)
    from presence where last_row=1 and removed_by is not null and removed_after is not null
    union all
    select *,'move',observed_at,
        cast({{ dbt.datediff('previous_at', 'observed_at', 'second') }} / 3600.0 as double precision)
    from presence where previous_position<>position and snapshot_hash<>previous_hash
)
select cast(platform as text) as platform,cast(playlist_id as text) as playlist_id,cast(variant as text) as variant,
    cast(stream as text) as stream,cast(item_type as text) as item_type,
    cast(platform_item_id as text) as platform_item_id,cast(featured_track_id as text) as featured_track_id,
    cast(owner_class as text) as owner_class,
    cast(occurrence_inferred as boolean) as occurrence_inferred,
    cast(occurrence_key as text) as occurrence_key,cast(interval_id as text) as interval_id,
    cast(event_type as text) as event_type,cast({{ playlist_utc('event_at') }} as timestamp) as observed_at,
    cast(platform_track_id as text) as platform_track_id,cast(position as bigint) as position,
    cast(previous_position as bigint) as previous_position,cast({{ playlist_utc('entered_after') }} as timestamp) as entered_after,
    cast({{ playlist_utc('first_observed_at') }} as timestamp) as first_observed_at,cast({{ playlist_utc('removed_after') }} as timestamp) as removed_after,
    cast({{ playlist_utc('removed_by') }} as timestamp) as removed_by,cast(is_baseline as boolean) as is_baseline,
    cast(event_uncertainty as double precision) as uncertainty_hours,cast(case when event_type in ('remove','left_head')
        then removed_by_snapshot_id else snapshot_id end as text) as snapshot_id,
    cast(page_match as boolean) as page_match,cast(platform_album_id as text) as platform_album_id
from events
{% endmacro %}


{# One stream per (platform, playlist_id, variant), selected as of the bound cycle's close. #}
{% macro mdp_membership_current(snapshots, membership, observations) %}
-- "Now" is the bound cycle's close, so a replay of a closed cycle selects the same
-- stream. Local fixture builds have no bound cycle and use the latest visible
-- observation, never the wall clock.
with clock as (
    select coalesce(
        (select max(closed_at) from {{ source('raw','cycles') }}
         where cast(id as text)={{ mdp_literal(mdp_context().cycle_id) }}),
        (select max(observed_at) from {{ snapshots }})
    ) as as_of
), ranked as (
    select *,row_number() over (partition by platform,playlist_id,variant,stream
        order by observed_at desc,snapshot_id desc) as rn,
        -- The window is the observing source's frozen cadence, carried on each
        -- observation (render_cadence for renders), plus one daily consuming cycle.
        case cadence when 'daily' then 24 when 'weekly' then 168
            else {{ var('playlist_full_cadence_hours',168) }} end + 24 as window_hours
    from {{ snapshots }}
), preferred as (
    select *,row_number() over (partition by platform,playlist_id,variant order by
        case when platform<>'spotify' then 0
             when stream='head' and extent_valid then 0
             when stream='full' and {{ dbt.datediff('observed_at','clock.as_of','second') }}
                between 0 and window_hours * 3600 then 1
             when stream='head' then 2 else 3 end) as preference
    from ranked cross join clock where rn=1
), members as (
    select s.platform,s.playlist_id,s.variant,s.stream,o.occurrence_key,o.occurrence_inferred,
        m.interval_id,o.position,o.platform_track_id,o.item_type,o.platform_item_id,o.featured_track_id,
        cast({{ playlist_utc('s.observed_at') }} as timestamp) as observed_at,
        cast(s.effective_coverage as text) as coverage,
        cast(case when s.stream='head' and not s.extent_valid then 'head_only' else 'full' end as text) as extent,
        s.snapshot_id,
        row_number() over (partition by s.platform,s.playlist_id,s.variant,o.occurrence_key
            order by o.observed_at desc,o.snapshot_id desc) as member_rank
    from preferred s
    join {{ membership }} m
        on m.platform=s.platform and m.playlist_id=s.playlist_id and m.variant=s.variant and m.stream=s.stream
        and m.first_observed_at<=s.observed_at and (m.removed_by is null or m.removed_by>s.observed_at)
    join {{ observations }} o
        on o.platform=m.platform and o.playlist_id=m.playlist_id and o.variant=m.variant and o.stream=m.stream
        and o.occurrence_key=m.occurrence_key and o.observed_at between m.first_observed_at and s.observed_at
    where s.preference=1 and (s.platform<>'spotify' or s.stream='head'
        or {{ dbt.datediff('s.observed_at','s.as_of','second') }} between 0 and s.window_hours * 3600)
)
select platform,playlist_id,variant,stream,occurrence_key,occurrence_inferred,interval_id,position,
    platform_track_id,item_type,platform_item_id,featured_track_id,observed_at,coverage,extent,snapshot_id
from members
where member_rank=1
{% endmacro %}


{# Per observation: every source that contributed to its stream up to and including it
   (content, referenced content, paired page and its items), as a sorted JSON array text. #}
{% macro mdp_playlist_source_keys(snapshots, stg_snapshots, stg_items) %}
with contributions_raw as (
    select snapshot_id,_source_key as source_key from {{ stg_snapshots }}
    union
    select snapshot_id,_source_key from {{ stg_items }}
    union
    select s.snapshot_id,r._source_key
    from {{ stg_snapshots }} s
    join {{ stg_snapshots }} r on r.snapshot_id=s.effective_content_id
    union
    select s.snapshot_id,i._source_key
    from {{ stg_snapshots }} s
    join {{ stg_items }} i on i.snapshot_id=s.effective_content_id
    union
    select s.snapshot_id,p._source_key
    from {{ snapshots }} s
    join {{ stg_snapshots }} p on p.snapshot_id=s.page_snapshot_id
    union
    select s.snapshot_id,p._source_key
    from {{ snapshots }} s
    join {{ stg_items }} p on p.snapshot_id=s.page_snapshot_id
), contributors as (
    -- A row without a source key contributes one the registry lacks, so it fails closed.
    select snapshot_id, coalesce(source_key, 'unknown') as source_key from contributions_raw
), contributions as (
    -- Each stream needs only its first use of each source.
    select s.platform,s.playlist_id,s.variant,s.stream,c.source_key,min(s.observed_at) as first_at
    from {{ snapshots }} s
    join contributors c on c.snapshot_id=s.snapshot_id
    group by 1,2,3,4,5
)
select s.platform,s.playlist_id,s.variant,s.stream,s.snapshot_id,
    cast(coalesce('[' || string_agg('"' || c.source_key || '"', ',' order by c.source_key) || ']','[]') as text) as source_keys
from {{ snapshots }} s
left join contributions c
    on c.platform=s.platform and c.playlist_id=s.playlist_id and c.variant=s.variant
    and c.stream=s.stream and c.first_at<=s.observed_at
group by 1,2,3,4,5
{% endmacro %}
