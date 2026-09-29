{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with recursive days as (
    select day, max(case when family = 'playlists' then window_days end) as playlist_window_days,
        max(case when family = 'shazam' then window_days end) as shazam_window_days,
        max(case when family = 'streams' then rate_window_days end) as stream_window_days
    from {{ ref('int_song_windows__daily') }} group by 1
), offsets as (
    select 0 as days_back
    union all select days_back + 1 from offsets
    where days_back + 1 < 2 * {{ mdp_movement_parameter('window_max_days') }}
), new_entries as (
    select d.day, s.song_key, sum(s.shazam_new_entries) as new_entries
    from {{ ref('int_cluster_day__daily') }} s cross join offsets o join days d on d.day = s.day + o.days_back
    where s.shazam_new_entries > 0 and o.days_back < d.shazam_window_days group by 1, 2
), rates as (
    select d.day, s.song_key,
        case when d.stream_window_days >= {{ mdp_movement_parameter('stream_min_days') }}
            and count(case when o.days_back < d.stream_window_days then s.stream_rate end) >= {{ mdp_movement_parameter('stream_min_days') }}
            and count(case when o.days_back >= d.stream_window_days then s.stream_rate end) >= {{ mdp_movement_parameter('stream_min_days') }}
            then avg(case when o.days_back < d.stream_window_days then s.stream_rate end)
                / nullif(avg(case when o.days_back >= d.stream_window_days then s.stream_rate end), 0) - 1 end as stream_rate_gain
    from {{ ref('int_cluster_day__daily') }} s cross join offsets o join days d on d.day = s.day + o.days_back
    where s.stream_rate is not null and o.days_back < 2*d.stream_window_days
    group by 1, 2, d.stream_window_days
), shazam_days as (
    select distinct day from {{ ref('int_cluster_day__daily') }} where shazam_observed
), baseline_calendar as (
    select day from shazam_days
    union select day - shazam_window_days from days
), observed_baselines as (
    select c.day, max(s.day) over (order by c.day rows between unbounded preceding and current row) as shazam_baseline_day
    from baseline_calendar c left join shazam_days s using (day)
), baselines as (
    select d.day, b.shazam_baseline_day
    from days d join observed_baselines b on b.day = d.day - d.shazam_window_days
), windows as (
    select s.day, s.song_key, s.title_text, s.artist_text,
        d.playlist_window_days, d.shazam_window_days, d.stream_window_days, baseline.shazam_baseline_day,
        s.shazam_charts - b.shazam_charts + coalesce(n.new_entries, 0) as shazam_spread_gain,
        r.stream_rate_gain,
        case when s.playlists_observed then 1 else 0 end as playlists_observed,
        case when s.shazam_observed then 1 else 0 end as shazam_observed,
        case when s.streams_observed then 1 else 0 end as streams_observed
    from {{ ref('int_cluster_day__daily') }} s join days d using (day)
    join baselines baseline on baseline.day = s.day
    left join {{ ref('int_cluster_day__daily') }} b on b.song_key = s.song_key and b.day = baseline.shazam_baseline_day
    left join new_entries n on n.day = s.day and n.song_key = s.song_key
    left join rates r on r.day = s.day and r.song_key = s.song_key
), entered as (
    select d.day, e.song_key, e.platform, e.list_id, max(e.list_kind) as list_kind, max(e.followers) as followers,
        max(case e.list_kind when 'editorial' then {{ mdp_movement_parameter('editorial_add_points') }}
            when 'new_music' then {{ mdp_movement_parameter('editorial_add_points') }}
            when 'dsp_algorithmic' then {{ mdp_movement_parameter('algorithmic_add_points') }} else 0.0 end
            * e.confidence) as weight,
        min(e.list_reach_tier) as list_reach_tier, max(e.observed_at) as last_entered_at
    from {{ ref('int_cluster_entries__daily') }} e cross join offsets o join days d on d.day = e.day + o.days_back
    where e.platform <> 'shazam' and o.days_back < d.playlist_window_days
    group by 1, 2, 3, 4
), totals as (
    -- Chart lists contribute through chart spread; new_entries reach and tier use other list kinds.
    select day, song_key, sum(weight) as playlist_adds, sum(followers) as all_reach, min(list_reach_tier) as all_tier,
        count(case when list_kind <> 'chart' then 1 end) as rising_lists,
        sum(case when list_kind <> 'chart' then followers end) as rising_reach,
        min(case when list_kind <> 'chart' then list_reach_tier end) as rising_tier
    from entered group by 1, 2
), markets as (
    select d.day, e.song_key, count(distinct case when length(e.market) = 2 then e.market end) as market_count,
        max(e.observed_at) as last_entered_at
    from {{ ref('int_cluster_entries__daily') }} e cross join offsets o join days d on d.day = e.day + o.days_back
    where o.days_back < case when e.platform = 'shazam' then d.shazam_window_days else d.playlist_window_days end
    group by 1, 2
), listed as (
    -- The same list_kind rule as int_song_entries__daily: the seed refines only editorial and chart lists.
    select c.cluster_key as song_key, f.day, t.market,
        {{ mdp_song_list_kind('f.owner_class', 't.list_kind') }} as list_kind
    from {{ ref('int_song_followers__daily') }} f
    join {{ ref('int_song_cluster__daily') }} c on c.song_key = f.song_key
    join {{ ref('playlist_reach_tiers') }} t on {{ mdp_song_platform('t.platform') }} = f.platform and t.playlist_id = f.playlist_id
), chart_presence as (
    select song_key, day, market from listed where list_kind = 'chart' and length(market) = 2
    union select song_key, chart_date, upper(country) from {{ ref('int_cluster_shazam__daily') }} where length(country) = 2
), new_music as (
    -- A song that sits on a new-music list in the window counts as new for its list when no proxy dates it.
    select distinct d.day, l.song_key from listed l cross join offsets o join days d on d.day = l.day + o.days_back
    where l.list_kind = 'new_music' and o.days_back < greatest(d.playlist_window_days, 1)
), market_first as (
    select song_key, market, min(day) as first_day from chart_presence group by 1, 2
), chart_markets as (
    select d.day, e.song_key, count(distinct e.market) as chart_spread_gain
    from {{ ref('int_cluster_entries__daily') }} e cross join offsets o join days d on d.day = e.day + o.days_back
    join market_first m on m.song_key = e.song_key and m.market = e.market and m.first_day = e.day
    where e.list_kind = 'chart' and length(e.market) = 2
        and o.days_back < case when e.platform = 'shazam' then d.shazam_window_days else d.playlist_window_days end
    group by 1, 2
), placed as (
    select w.*, a.song_key as entered_key, a.playlist_adds, a.all_reach, a.all_tier, a.rising_lists, a.rising_reach, a.rising_tier,
        m.market_count, m.last_entered_at, c.chart_spread_gain, g.age_class, g.age_basis, g.artist_stage, g.artist_stage_basis,
        -- Three lists never mix. Human-set rules only: catalog first, then new songs split by artist stage.
        case when g.age_class = 'catalog' then 'catalog_entries'
            when g.age_class = 'new' or n.song_key is not null then
                case when g.artist_stage = 'established' then 'established_entries' else 'new_entries' end
            else 'unplaced' end as movement_list
    from windows w left join totals a using (day, song_key) left join markets m using (day, song_key)
    left join chart_markets c using (day, song_key) left join new_music n using (day, song_key)
    left join {{ ref('int_cluster_age__daily') }} g using (song_key)
)
select p.day, p.song_key, c.cluster_key, c.member_song_keys, c.cluster_methods, c.cluster_confidence,
    title_text, artist_text, playlist_window_days, shazam_window_days, stream_window_days,
    shazam_baseline_day, shazam_spread_gain, stream_rate_gain, playlists_observed, shazam_observed, streams_observed,
    cast(greatest(playlist_window_days, shazam_window_days, stream_window_days) as integer) as window_days,
    -- Entries in the window count even when the platform missed today; absence is 0 only on an observed day.
    cast(coalesce(playlist_adds, case when playlists_observed = 1 then 0 end) as double precision) as playlist_adds,
    cast(coalesce(case when entered_key is null then null when movement_list = 'new_entries'
            then case when rising_lists > 0 then rising_reach else 0 end else all_reach end,
        case when playlists_observed = 1 and entered_key is null then 0 end) as double precision) as follower_exposure_gain,
    cast(coalesce(case when movement_list = 'new_entries' then rising_tier else all_tier end, 4) as integer) as list_reach_tier,
    cast(coalesce(market_count, 0) as bigint) as market_count,
    cast(coalesce(chart_spread_gain, 0) as bigint) as chart_spread_gain,
    cast(last_entered_at as timestamp with time zone) as last_entered_at,
    cast(coalesce(age_class, 'unknown') as text) as age_class, cast(coalesce(age_basis, 'none') as text) as age_basis,
    cast(coalesce(artist_stage, 'unknown') as text) as artist_stage,
    cast(coalesce(artist_stage_basis, 'none') as text) as artist_stage_basis,
    cast(movement_list as text) as movement_list
from placed p join {{ ref('int_song_cluster__daily') }} c on c.song_key = p.song_key
