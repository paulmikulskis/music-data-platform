{# design: track inputs, priority, exact resolution and the identity pick, shared by the hourly
   chain (materialized relations) and the daily, weekly and tenant copies (inlined over their own
   manifests). Every macro returns a SELECT. #}

{% macro mdp_latest(field, rank) -%}
  {#- The field's value on the highest-ranked row where it is not null. -#}
  {%- if target.type == 'postgres' -%}
    (array_agg({{ field }} order by {{ rank }} desc) filter (where {{ field }} is not null))[1]
  {%- else -%}
    arg_max({{ field }}, {{ rank }}) filter (where {{ field }} is not null)
  {%- endif -%}
{%- endmacro %}

{% macro mdp_hash_join_plan() -%}
  {#- Pre-hook for a table built from CTEs Postgres cannot estimate: the track-input enrichment (its
      row_number() filters) and inlined reference rows. It guesses them at a handful of rows (7 for
      244,633 reference rows on a 1% spine; 1 for the live seed's 28,000 playlist items) and then scans
      one once per row of another in nested loops; hash joins keep the build linear. The nested-loop
      penalty lifts the plan cost past the JIT threshold, so JIT is off too. Both settings end with the
      model's transaction. -#}
  {%- if target.type == 'postgres' -%}
  select set_config('enable_nestloop', 'off', true), set_config('jit', 'off', true)
  {%- else -%}
  select 1
  {%- endif -%}
{%- endmacro %}

{% macro mdp_any(column, values) -%}
  {#- `column` equals one of the values a subquery returns, as an index lookup: an array on Postgres,
      which an index scan reads with hash joins on and nested loops off; a plain IN on DuckDB. -#}
  {%- if target.type == 'postgres' -%}
    {{ column }} = any(array({{ values }}))
  {%- else -%}
    {{ column }} in ({{ values }})
  {%- endif -%}
{%- endmacro %}

{% macro mdp_json_array_elements(expr) -%}
  {#- A set-returning expression over a JSON text array's elements, for the select list. -#}
  {%- if target.type == 'postgres' -%}
    jsonb_array_elements_text(cast({{ expr }} as jsonb))
  {%- else -%}
    unnest(from_json({{ expr }}, '["VARCHAR"]'))
  {%- endif -%}
{%- endmacro %}

{% macro mdp_day_series(low, high) -%}
  {#- A set-returning expression over the UTC days from `low` to `high`, for the select list. -#}
  {%- if target.type == 'postgres' -%}
    cast(generate_series(cast({{ low }} as date), cast({{ high }} as date), interval '1 day') as date)
  {%- else -%}
    cast(unnest(generate_series(cast({{ low }} as date), cast({{ high }} as date), interval 1 day)) as date)
  {%- endif -%}
{%- endmacro %}

{% macro mdp_retry_week(input_ref, closed_at) -%}
  {#- The ISO week of the bound cycle's closed_at minus hash(input_ref) mod 168 hours: each input turns
      over once a week, about 1/168 of them each hourly cycle. -#}
  {#- The week is taken in UTC, so the session time zone never moves an input's turnover hour. -#}
  {%- if target.type == 'postgres' -%}
    to_char({{ playlist_utc(closed_at) }} - (((('x' || substr({{ input_ref }}, 1, 8))::bit(32)::bigint) % 168) * interval '1 hour'), 'IYYY-"W"IW')
  {%- else -%}
    strftime(cast({{ playlist_utc(closed_at) }} as timestamp) - to_hours(cast(cast(('0x' || substr({{ input_ref }}, 1, 8)) as bigint) % 168 as integer)), '%G-W%V')
  {%- endif -%}
{%- endmacro %}

{% macro mdp_identity_clock(fallback) -%}
  {#- The bound cycle's close; local builds without a bound cycle use the latest observation. -#}
  coalesce((select max(closed_at) from {{ source('raw', 'cycles') }}
            where cast(id as text) = {{ mdp_literal(mdp_context().cycle_id) }}), ({{ fallback }}))
{%- endmacro %}

{% macro mdp_track_inputs(touched=none) -%}
  {#- One row per (platform, platform_track_id) observed as a track item in this cycle's manifest, with
      surface enrichment: a Spotify embed row takes the album and artist ids of the page row
      at its position in the same observation group when the track ids match or the titles match and
      the durations agree within two seconds; an embed snapshot takes its owner class from that page
      snapshot; SoundCloud and Bandcamp hydration adds ISRCs. Each field is its latest observed value.
      `touched` names a relation of (platform, platform_track_id) keys to recompute: only their item rows
      are read, with the page rows and snapshots of the observation groups and snapshots they were seen
      in, each through a keyed index before any window, so an hour's build reads what its new dumps
      touch. Every dedupe partition carries the track id, so reading one key never changes another's. -#}
  {%- set ctx = mdp_context() -%}
  {%- set stream = "coalesce({t}.stream, case when {t}.platform = 'spotify' then 'head' else 'full' end)" -%}
  with all_items as (
    select i.platform, i.playlist_id, i.variant, {{ stream | replace('{t}', 'i') }} as stream,
        i.snapshot_id, i.position, i.observation_group, i.observed_at, i.platform_track_id, i.title,
        cast(i.artist_names as text) as artist_names, cast(i.platform_artist_ids as text) as platform_artist_ids,
        i.platform_album_id, i.duration_ms, i.isrc, i.fetch_surface, i._source_key, i._landed_seq, i._dump_id,
        row_number() over (partition by i.platform, i.playlist_id, i.variant, {{ stream | replace('{t}', 'i') }}, i.snapshot_id,
            i.position, i.platform_track_id order by i._landed_seq desc, i._dump_id desc) as _rn
    from {{ source('raw', 'playlist_items') }} i
    where {{ ctx.manifest_filter('i._dump_id', 'raw.playlist_items') }}
      and i.platform_track_id is not null and coalesce(i.item_type, 'track') = 'track'
    {%- if touched %}
      and {{ mdp_any('i.platform_track_id', 'select platform_track_id from ' ~ touched) }}
      and exists (select 1 from {{ touched }} k where k.platform = i.platform and k.platform_track_id = i.platform_track_id)
    {%- endif %}
  ), items as (
    select * from all_items where _rn = 1
  ), groups as (
    select distinct platform, playlist_id, variant, observation_group from items where observation_group is not null
  ), page_rows as (
    select p.platform, p.playlist_id, p.variant, {{ stream | replace('{t}', 'p') }} as stream, p.position, p.observation_group,
        p.platform_track_id, p.title, cast(p.platform_artist_ids as text) as platform_artist_ids, p.platform_album_id,
        p.duration_ms, p._landed_seq,
        row_number() over (partition by p.platform, p.playlist_id, p.variant, {{ stream | replace('{t}', 'p') }}, p.snapshot_id,
            p.position, p.platform_track_id order by p._landed_seq desc, p._dump_id desc) as _rn
    from {{ source('raw', 'playlist_items') }} p
    where {{ ctx.manifest_filter('p._dump_id', 'raw.playlist_items') }} and p.fetch_surface = 'sp_playlist_page'
      and p.platform_track_id is not null and coalesce(p.item_type, 'track') = 'track'
    {%- if touched %}
      and {{ mdp_any('p.observation_group', 'select observation_group from groups') }}
      and exists (select 1 from groups g where g.platform = p.platform and g.playlist_id = p.playlist_id
        and g.variant = p.variant and g.observation_group = p.observation_group)
    {%- endif %}
  ), pages as (
    select * from page_rows where _rn = 1
  ), snapshots as (
    select s.platform, s.playlist_id, s.variant, {{ stream | replace('{t}', 's') }} as stream,
        s.snapshot_id, s.observation_group, s.owner_class, s.fetch_surface,
        {{ mdp_owner_observed_class() }} as observed_class,
        row_number() over (partition by s.platform, s.playlist_id, s.variant, {{ stream | replace('{t}', 's') }}, s.snapshot_id
            order by s._landed_seq desc, s._dump_id desc) as _rn
    from {{ source('raw', 'playlist_snapshots') }} s
    where {{ ctx.manifest_filter('s._dump_id', 'raw.playlist_snapshots') }}
    {%- if touched %}
      -- The snapshots the items were seen in, and the page snapshots of their observation groups.
      and ({{ mdp_any('s.snapshot_id', 'select snapshot_id from items') }}
        or ({{ mdp_any('s.observation_group', 'select observation_group from groups') }} and s.fetch_surface = 'sp_playlist_page'
          and exists (select 1 from groups g where g.platform = s.platform and g.playlist_id = s.playlist_id
            and g.variant = s.variant and g.observation_group = s.observation_group)))
    {%- endif %}
  ), served as (
    -- The observed owner class decides (mdp_owner_rules), never the target's label alone.
    select *, {{ mdp_owner_served_class('observed_class') }} as served_class
    from snapshots
    where _rn = 1
  ), page_classes as (
    select platform, playlist_id, variant, observation_group, max(served_class) as owner_class
    from served
    where fetch_surface = 'sp_playlist_page' and observation_group is not null and served_class <> 'unknown'
    group by 1, 2, 3, 4
  ), classes as (
    -- An embed's own observation first, then its paired page's class, then its label.
    select s.platform, s.playlist_id, s.variant, s.stream, s.snapshot_id,
        case when s.observed_class = 'unknown' then coalesce(p.owner_class, s.served_class)
            else s.served_class end as owner_class
    from served s
    left join page_classes p on p.platform = s.platform and p.playlist_id = s.playlist_id
        and p.variant = s.variant and p.observation_group = s.observation_group
  ), enriched as (
    select i.*, c.owner_class,
        coalesce(i.platform_album_id, p.platform_album_id) as album_id,
        coalesce(nullif(i.platform_artist_ids, '[]'), p.platform_artist_ids) as artist_ids,
        row_number() over (partition by i.platform, i.platform_track_id
            order by i.observed_at, i._landed_seq, i._dump_id, p._landed_seq) as _obs
    from items i
    left join classes c on c.platform = i.platform and c.playlist_id = i.playlist_id and c.variant = i.variant
        and c.stream = i.stream and c.snapshot_id = i.snapshot_id
    left join pages p on i.fetch_surface <> 'sp_playlist_page' and p.platform = i.platform
        and p.playlist_id = i.playlist_id and p.variant = i.variant and p.stream = i.stream
        and p.observation_group = i.observation_group and p.position = i.position
        and (p.platform_track_id = i.platform_track_id
             or (p.title = i.title and abs(p.duration_ms - i.duration_ms) <= 2000))
  ), hydrated as (
    select 'soundcloud' as platform, cast(track_id as text) as platform_track_id, isrc, album_title,
        _landed_seq, _dump_id
    from {{ source('raw', 'sc_tracks') }}
    where {{ ctx.manifest_filter('_dump_id', 'raw.sc_tracks') }}
    {%- if touched %}
      and cast(track_id as text) in (select platform_track_id from items where platform = 'soundcloud')
    {%- endif %}
    union all
    select 'bandcamp', cast(track_id as text), isrc, cast(null as text), _landed_seq, _dump_id
    from {{ source('raw', 'bc_tracks') }}
    where {{ ctx.manifest_filter('_dump_id', 'raw.bc_tracks') }}
    {%- if touched %}
      and cast(track_id as text) in (select platform_track_id from items where platform = 'bandcamp')
    {%- endif %}
  ), hydration as (
    select platform, platform_track_id,
        {{ mdp_latest('isrc', '_landed_seq') }} as isrc, {{ mdp_latest('album_title', '_landed_seq') }} as album_title
    from hydrated group by 1, 2
  ), tracks as (
    select e.platform, e.platform_track_id,
        {{ mdp_latest('e.title', 'e._obs') }} as title,
        {{ mdp_latest('e.artist_names', 'e._obs') }} as artist_names,
        {{ mdp_latest('e.artist_ids', 'e._obs') }} as platform_artist_ids,
        {{ mdp_latest('e.album_id', 'e._obs') }} as platform_album_id,
        {{ mdp_latest('e.duration_ms', 'e._obs') }} as duration_ms,
        {{ mdp_latest('e.isrc', 'e._obs') }} as item_isrc,
        max(case when e.owner_class in ('editorial', 'dsp_algorithmic', 'chart') then 1 else 0 end) = 1 as curated,
        min(e._landed_seq) as first_landed_seq, min(e.observed_at) as first_observed_at,
        max(e.observed_at) as last_observed_at,
        '[' || string_agg(distinct '"' || e._source_key || '"', ',' order by '"' || e._source_key || '"') || ']' as _source_keys
    from enriched e group by 1, 2
  )
  select t.platform, t.platform_track_id, t.title, t.artist_names, t.platform_artist_ids, t.platform_album_id,
      h.album_title, cast(t.duration_ms as bigint) as duration_ms, coalesce(t.item_isrc, h.isrc) as platform_isrc,
      t.curated, t.first_landed_seq, t.first_observed_at, t.last_observed_at, t._source_keys,
      {{ mdp_identity_hash(['t.title', 't.artist_names', 't.platform_artist_ids', 't.platform_album_id', 'h.album_title',
                            'cast(t.duration_ms as bigint)', 'coalesce(t.item_isrc, h.isrc)']) }} as fields_hash
  from tracks t
  left join hydration h on h.platform = t.platform and h.platform_track_id = t.platform_track_id
{%- endmacro %}

{% macro mdp_priority_tracks(inputs, artist_evidence=none) -%}
  select platform, platform_track_id, curated from {{ inputs }} where curated
{%- endmacro %}

{% macro mdp_track_exact(inputs, reference) -%}
  {#- Exact resolution in SQL, no function call: a platform-reported ISRC to its recording through the
      landed ISRC rows, and a platform track URL to its recording through the landed URL rows, each
      only when exactly one recording matches. reference_version is the md5 of the content of every
      reference row the track joins, tombstones included (its ISRC rows, its track and album URL rows,
      and the ISRC rows of its URL recording), so a reload of unchanged rows keeps it. `reference` is
      a relation of mdp_reference_current() rows. -#}
  with isrc_rows as (
    select t.platform, t.platform_track_id, r.mb_table, r.mb_key, r.content_md5, r.tombstoned, r.recording_id
    from {{ inputs }} t join {{ reference }} r on r.mb_table = 'isrc' and r.isrc = t.platform_isrc
  ), url_rows as (
    select t.platform, t.platform_track_id, r.mb_table, r.mb_key, r.content_md5, r.tombstoned,
        r.url_kind, r.entity_type, r.entity_id
    from {{ inputs }} t join {{ reference }} r on r.mb_table = 'url_link' and r.url_platform = t.platform
        and r.url_kind = 'track' and r.url_platform_id = t.platform_track_id
    -- Two equality joins, never one OR join: an unindexed reference relation (the daily copy) would
    -- compare every track with every URL row.
    union all
    select t.platform, t.platform_track_id, r.mb_table, r.mb_key, r.content_md5, r.tombstoned,
        r.url_kind, r.entity_type, r.entity_id
    from {{ inputs }} t join {{ reference }} r on r.mb_table = 'url_link' and r.url_platform = t.platform
        and r.url_kind = 'album' and r.url_platform_id = t.platform_album_id
  ), url_recordings as (
    select platform, platform_track_id, count(distinct entity_id) as candidates, min(entity_id) as recording_id
    from url_rows where not tombstoned and url_kind = 'track' and entity_type = 'recording' group by 1, 2
  ), url_isrc_rows as (
    select u.platform, u.platform_track_id, r.mb_table, r.mb_key, r.content_md5, r.tombstoned, r.isrc
    from url_recordings u join {{ reference }} r on r.mb_table = 'isrc' and r.recording_id = u.recording_id
    where u.candidates = 1
  ), isrc_recordings as (
    select platform, platform_track_id, count(distinct recording_id) as candidates, min(recording_id) as recording_id
    from isrc_rows where not tombstoned group by 1, 2
  ), url_isrcs as (
    select platform, platform_track_id, count(*) as isrc_count, min(isrc) as isrc
    from url_isrc_rows where not tombstoned group by 1, 2
  ), joined as (
    select platform, platform_track_id, mb_table, mb_key, content_md5, tombstoned from isrc_rows
    union select platform, platform_track_id, mb_table, mb_key, content_md5, tombstoned from url_rows
    union select platform, platform_track_id, mb_table, mb_key, content_md5, tombstoned from url_isrc_rows
  ), versions as (
    select platform, platform_track_id,
        md5(string_agg(mb_table || ':' || mb_key || ':' || content_md5 || ':' || cast(tombstoned as text), ','
            order by mb_table || ':' || mb_key || ':' || content_md5 || ':' || cast(tombstoned as text))) as reference_version
    from joined group by 1, 2
  )
  select t.platform, t.platform_track_id, t.platform_isrc,
      case when ir.candidates = 1 then ir.recording_id end as isrc_recording_id,
      coalesce(ir.candidates, 0) as isrc_candidates,
      case when ur.candidates = 1 then ur.recording_id end as url_recording_id,
      coalesce(ur.candidates, 0) as url_candidates,
      ui.isrc as url_isrc, coalesce(ui.isrc_count, 0) as url_isrc_count,
      coalesce(case when ir.candidates = 1 then ir.recording_id end,
               case when ur.candidates = 1 then ur.recording_id end) as exact_recording_id,
      coalesce(t.platform_isrc, ui.isrc) as exact_isrc,
      coalesce(v.reference_version, md5('')) as reference_version
  from {{ inputs }} t
  left join isrc_recordings ir on ir.platform = t.platform and ir.platform_track_id = t.platform_track_id
  left join url_recordings ur on ur.platform = t.platform and ur.platform_track_id = t.platform_track_id
  left join url_isrcs ui on ui.platform = t.platform and ui.platform_track_id = t.platform_track_id
  left join versions v on v.platform = t.platform and v.platform_track_id = t.platform_track_id
{%- endmacro %}

{% macro mdp_identity_pick(outputs, inputs, exact) -%}
  {#- The identity pick per track among manifest-visible enrichment rows: those whose fields_hash equals the
      track's current one, preferring an equal reference_version, then the newest retry_week, then the
      producing cycle's close_no, then the latest _landed_seq; only when none shares the current fields,
      the newest producing cycle's. -#}
  select o.* from (
    select o.*, c.close_no as producing_close_no,
        row_number() over (partition by o.platform, o.platform_track_id order by
            case when o.fields_hash = t.fields_hash then 0 else 1 end,
            case when o.fields_hash = t.fields_hash and o.reference_version = x.reference_version then 0 else 1 end,
            case when o.fields_hash = t.fields_hash then o.retry_week end desc nulls last,
            c.close_no desc nulls last, o._landed_seq desc, o._dump_id desc) as _pick
    from {{ outputs }} o
    join {{ inputs }} t on t.platform = o.platform and t.platform_track_id = o.platform_track_id
    join {{ exact }} x on x.platform = o.platform and x.platform_track_id = o.platform_track_id
    left join {{ source('raw', 'cycles') }} c on c.id = o._cycle_id
  ) o where _pick = 1
{%- endmacro %}

{% macro mdp_track_identity(inputs, exact, reference, resolutions, crosswalk, audit=false) -%}
  {#- One ISRC and one MusicBrainz recording per (platform, platform_track_id), in the order
      platform-reported ISRC, MusicBrainz via the landed rows or mb_resolve's release candidates, the
      crosswalk then mb_resolve's trigram match, both at or above their platform/method floor, each with
      method, confidence and evidence. mb_resolve recordings follow merge redirects to the current
      recording. With audit=true, returns the disagreements the pick overruled instead. -#}
  with res as ({{ mdp_identity_pick(resolutions, inputs, exact) }}),
  xw as ({{ mdp_identity_pick(crosswalk, inputs, exact) }}),
  floors as (select platform, method, cast(floor as double precision) as floor from {{ ref('identity_confidence_floors') }}),
  {#- Every reference read below is a keyed join into `reference`, so the hourly chain reads the rows
      it needs through int_reference__current's indexes and never aggregates the whole spine. #}
  resolved as (
    select r.*, coalesce(cur.recording_gid, moved.recording_gid, r.recording_gid) as current_gid,
        cur.recording_gid is null and moved.recording_gid is not null as redirected
    from res r
    left join {{ reference }} cur on cur.mb_table = 'recording' and not cur.tombstoned and cur.recording_gid = r.recording_gid
    left join {{ reference }} d on d.mb_table = 'redirect' and d.entity_type = 'recording' and not d.tombstoned
        and d.gid = r.recording_gid and cur.recording_gid is null
    left join {{ reference }} moved on moved.mb_table = 'recording' and not moved.tombstoned and moved.recording_id = d.new_id
    left join floors f on f.platform = r.platform and f.method = r.method
    where r.status = 'resolved'
      and (r.method <> 'mb_trigram' or r.confidence >= coalesce(f.floor, 1.01))
  ), xw_recordings as (
    -- A crosswalk ISRC names a recording only when exactly one live ISRC row carries it.
    select xr.platform, xr.platform_track_id, count(distinct i.recording_id) as candidates, min(i.recording_id) as recording_id
    from xw xr join {{ reference }} i on i.mb_table = 'isrc' and not i.tombstoned and i.isrc = xr.isrc
    group by 1, 2
  ), candidates as (
    select t.platform, t.platform_track_id, t.fields_hash, x.reference_version,
        x.platform_isrc, x.isrc_recording_id, x.url_recording_id, x.url_isrc,
        ig.recording_gid as isrc_gid, ug.recording_gid as url_gid,
        rr.method as res_method, rr.current_gid as res_gid, rr.isrc as res_isrc, rr.confidence as res_confidence,
        rr.redirected as res_redirected, rr.evidence as res_evidence,
        case when xr.status = 'resolved' and xr.confidence >= coalesce(f.floor, 1.01) then xr.isrc end as xw_isrc,
        xr.confidence as xw_confidence, xr.evidence as xw_evidence,
        case when xr.status = 'resolved' and xr.confidence >= coalesce(f.floor, 1.01) then xg.recording_gid end as xw_gid,
        res_any.status as res_status, res_any.candidate_count as res_candidates, res_any.retry_week as res_retry_week,
        res_any.producing_close_no as res_close_no
    from {{ inputs }} t
    join {{ exact }} x on x.platform = t.platform and x.platform_track_id = t.platform_track_id
    left join {{ reference }} ig on ig.mb_table = 'recording' and not ig.tombstoned and ig.recording_id = x.isrc_recording_id
    left join {{ reference }} ug on ug.mb_table = 'recording' and not ug.tombstoned and ug.recording_id = x.url_recording_id
    left join resolved rr on rr.platform = t.platform and rr.platform_track_id = t.platform_track_id
    left join res res_any on res_any.platform = t.platform and res_any.platform_track_id = t.platform_track_id
    left join xw xr on xr.platform = t.platform and xr.platform_track_id = t.platform_track_id
    left join floors f on f.platform = t.platform and f.method = 'crosswalk'
    left join xw_recordings xu on xu.platform = t.platform and xu.platform_track_id = t.platform_track_id and xu.candidates = 1
    left join {{ reference }} xg on xg.mb_table = 'recording' and not xg.tombstoned and xg.recording_id = xu.recording_id
  ), picked as (
    select c.*,
        coalesce(platform_isrc, case when url_gid is not null then url_isrc end,
                 case when res_method = 'mb_release' then res_isrc end, xw_isrc,
                 case when res_method = 'mb_trigram' then res_isrc end) as isrc,
        case when platform_isrc is not null then 'platform_isrc'
             when url_gid is not null and url_isrc is not null then 'mb_url'
             when res_method = 'mb_release' and res_isrc is not null then 'mb_release'
             when xw_isrc is not null then 'crosswalk'
             when res_method = 'mb_trigram' and res_isrc is not null then 'mb_trigram' end as isrc_method,
        coalesce(isrc_gid, url_gid, case when res_method = 'mb_release' then res_gid end, xw_gid,
                 case when res_method = 'mb_trigram' then res_gid end) as mb_recording_gid,
        case when isrc_gid is not null then 'platform_isrc' when url_gid is not null then 'mb_url'
             when res_method = 'mb_release' then 'mb_release' when xw_gid is not null then 'crosswalk'
             when res_method = 'mb_trigram' then 'mb_trigram' end as recording_method
    from candidates c
  )
  {%- if audit %}
  -- The pick keeps the higher-precedence method; each lower-precedence answer that disagrees is kept here.
  , alternatives as (
    select platform, platform_track_id, 'isrc' as field, isrc_method as kept_method, isrc as kept_value,
        'platform_isrc' as other_method, platform_isrc as other_value from picked
    union all select platform, platform_track_id, 'isrc', isrc_method, isrc, 'mb_url', url_isrc from picked where url_gid is not null
    union all select platform, platform_track_id, 'isrc', isrc_method, isrc, 'mb_release', res_isrc from picked where res_method = 'mb_release'
    union all select platform, platform_track_id, 'isrc', isrc_method, isrc, 'crosswalk', xw_isrc from picked
    union all select platform, platform_track_id, 'isrc', isrc_method, isrc, 'mb_trigram', res_isrc from picked where res_method = 'mb_trigram'
    union all select platform, platform_track_id, 'recording', recording_method, mb_recording_gid, 'platform_isrc', isrc_gid from picked
    union all select platform, platform_track_id, 'recording', recording_method, mb_recording_gid, 'mb_url', url_gid from picked
    union all select platform, platform_track_id, 'recording', recording_method, mb_recording_gid, 'mb_release', res_gid from picked where res_method = 'mb_release'
    union all select platform, platform_track_id, 'recording', recording_method, mb_recording_gid, 'crosswalk', xw_gid from picked
    union all select platform, platform_track_id, 'recording', recording_method, mb_recording_gid, 'mb_trigram', res_gid from picked where res_method = 'mb_trigram'
  )
  select platform, platform_track_id, field, kept_method, kept_value, other_method, other_value
  from alternatives
  where kept_value is not null and other_value is not null and other_method <> kept_method and other_value <> kept_value
  {%- else %}
  select platform, platform_track_id, isrc, isrc_method,
      case isrc_method when 'platform_isrc' then 1.0 when 'mb_url' then 1.0 when 'mb_release' then res_confidence
          when 'crosswalk' then xw_confidence when 'mb_trigram' then res_confidence end as isrc_confidence,
      mb_recording_gid, recording_method,
      case recording_method when 'platform_isrc' then 1.0 when 'mb_url' then 1.0 when 'mb_release' then res_confidence
          when 'crosswalk' then xw_confidence when 'mb_trigram' then res_confidence end as recording_confidence,
      case when recording_method in ('mb_release', 'mb_trigram') then res_evidence
           when isrc_method = 'crosswalk' then xw_evidence end as evidence,
      coalesce(recording_method in ('mb_release', 'mb_trigram') and res_redirected, false) as redirected,
      res_status as resolution_status, res_candidates as resolution_candidates, res_retry_week as resolution_retry_week,
      res_close_no as resolution_close_no, fields_hash, reference_version
  from picked
  {%- endif %}
{%- endmacro %}

{% macro mdp_track_followers(variant=false) -%}
  {#- design: recording x day (x variant) by platform and owner class, from int_playlist__recording_days.
      Each playlist counts once per (day, platform, playlist, recording) within its variant before
      summing, so duplicates and markets never double a follower count; variants are never summed
      together. _source_keys lists every source of the snapshots the counted playlists were read from,
      for mdp_annotate(). -#}
  {%- set v = 'variant, ' if variant else '' -%}
  {%- set on_variant = 'and k.variant = c.variant ' if variant else '' -%}
  with playlists as (
    select day, platform, {{ v }}playlist_id, mb_recording_gid, max(owner_class) as owner_class,
        max(followers) as followers, min(best_position) as best_position
    from {{ ref('int_playlist__recording_days') }}
    group by day, platform, {{ v }}playlist_id, mb_recording_gid
  ), contributed as (
    select distinct d.day, d.platform, {{ 'd.variant, ' if variant else '' }}p.owner_class, d.mb_recording_gid, d.source_key
    from (select day, platform, {{ v }}playlist_id, mb_recording_gid, {{ mdp_json_array_elements('source_keys') }} as source_key
          from {{ ref('int_playlist__recording_days') }}) d
    join playlists p on p.day = d.day and p.platform = d.platform {{ 'and p.variant = d.variant ' if variant else '' }}and p.playlist_id = d.playlist_id
        and p.mb_recording_gid = d.mb_recording_gid
  ), keys as (
    select day, platform, {{ v }}owner_class, mb_recording_gid,
        '[' || string_agg('"' || source_key || '"', ',' order by source_key) || ']' as source_keys
    from contributed group by day, platform, {{ v }}owner_class, mb_recording_gid
  ), counted as (
    select day, platform, {{ v }}owner_class, mb_recording_gid,
        case when platform = 'spotify' then sum(followers) end as playlist_followers,
        count(*) as list_count, min(best_position) as best_position
    from playlists group by day, platform, {{ v }}owner_class, mb_recording_gid
  )
  select cast(c.day as date) as day, cast(c.platform as text) as platform, {{ 'cast(c.variant as text) as variant, ' if variant else '' }}
      cast(c.owner_class as text) as owner_class, cast(c.mb_recording_gid as text) as mb_recording_gid,
      cast(c.playlist_followers as bigint) as playlist_followers, cast(c.list_count as bigint) as list_count,
      cast(c.best_position as bigint) as best_position, k.source_keys as _source_keys
  from counted c
  left join keys k on k.day = c.day and k.platform = c.platform {{ on_variant }}and k.owner_class = c.owner_class
      and k.mb_recording_gid = c.mb_recording_gid
{%- endmacro %}

{% macro mdp_enrichment_rows(source_key) -%}
  {#- An identity function's rows visible in this cycle's manifest. Every physical copy stays: the identity rule
      pick is their deterministic dedupe, and it ranks the producing cycle's close_no before the landing
      order, so a replayed older cycle's late copy of the same enrichment never displaces a newer one. -#}
  select r.* from {{ source('raw', source_key) }} r
  {%- if source_key == 'mb_resolve' %}
  where {{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_resolve') }}
  {%- elif source_key == 'track_isrc_crosswalk' %}
  where {{ mdp_context().manifest_filter('r._dump_id', 'raw.track_isrc_crosswalk') }}
  {%- else %}{{ exceptions.raise_compiler_error('mdp_enrichment_rows: not an identity enrichment table: ' ~ source_key ~ '; choose a declared table from mdp_functions/identity.py') }}
  {%- endif %}
{%- endmacro %}

{% macro mdp_track_inputs_touched(relation, ctx) -%}
  {#- Track keys touched by a dump stamped in (high-water mark, bound close_no] in any joined input: an
      item, a snapshot (its items, and the items of its observation group) or a hydration row. Each
      branch reads the new dumps through the raw dump index, then the items by their key indexes; a
      null track id never matches a key, so no branch filters them. The high-water mark is read once, at
      compile, through the relation's index. -#}
  {%- set hwm = run_query('select coalesce(max(_hwm_close_no), -1) from ' ~ relation).columns[0].values()[0] | int if execute else -1 -%}
  {%- set fresh -%}
    select dump_id from {{ source('raw', 'dump_stamps') }}
    where scope = 'global' and close_no <= {{ ctx.close_no }} and close_no > {{ hwm }}
  {%- endset -%}
  {%- set new_snapshots -%}
    select platform, playlist_id, variant, snapshot_id, observation_group from {{ source('raw', 'playlist_snapshots') }}
    where {{ mdp_any('_dump_id', fresh ~ " and target_table = 'raw.playlist_snapshots'") }}
  {%- endset -%}
  select platform, platform_track_id from {{ source('raw', 'playlist_items') }}
  where {{ mdp_any('_dump_id', fresh ~ " and target_table = 'raw.playlist_items'") }}
  union
  select i.platform, i.platform_track_id
  from {{ source('raw', 'playlist_items') }} i
  where {{ mdp_any('i.snapshot_id', 'select snapshot_id from (' ~ new_snapshots ~ ') n') }}
      and {{ ctx.manifest_filter('i._dump_id', 'raw.playlist_items') }}
  union
  select i.platform, i.platform_track_id
  from {{ source('raw', 'playlist_items') }} i
  join ({{ new_snapshots }}) n on n.platform = i.platform and n.playlist_id = i.playlist_id
      and n.variant = i.variant and n.observation_group = i.observation_group
  where {{ mdp_any('i.observation_group', 'select observation_group from (' ~ new_snapshots ~ ') n where observation_group is not null') }}
      and {{ ctx.manifest_filter('i._dump_id', 'raw.playlist_items') }}
  union
  select 'soundcloud', cast(track_id as text) from {{ source('raw', 'sc_tracks') }}
  where _dump_id in ({{ fresh }} and target_table = 'raw.sc_tracks')
  union
  select 'bandcamp', cast(track_id as text) from {{ source('raw', 'bc_tracks') }}
  where _dump_id in ({{ fresh }} and target_table = 'raw.bc_tracks')
{%- endmacro %}
