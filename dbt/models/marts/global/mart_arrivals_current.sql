{{ config(tags=['cadence:daily']) }}
with entries as (
    select e.*, w.window_days, c.cluster_key, c.member_song_keys, c.cluster_methods, c.cluster_confidence from {{ ref('int_cluster_entries__daily') }} e join {{ ref('int_song_windows__daily') }} w
        on w.day = {{ mdp_cycle_day() }} and w.family = case when e.platform = 'shazam' then 'shazam' else 'playlists' end
    join {{ ref('int_song_cluster__daily') }} c on c.song_key = e.song_key
    where e.day > w.day - w.window_days and e.day <= w.day and e.list_kind in ('editorial', 'new_music', 'chart')
), totals as (
    select song_key, min(list_reach_tier) as all_tier, max(observed_at) as last_entered_at, max(window_days) as window_days,
        -- Chart lists serve Places, so a new_entries row reads its tier from editorial and new-music lists only.
        min(case when list_kind in ('editorial', 'new_music') then list_reach_tier end) as rising_tier,
        count(distinct case when platform <> 'shazam' then platform || ':' || list_id end) as entered_lists,
        count(distinct case when platform = 'shazam' then list_id end) as entered_charts,
        count(distinct case when platform = 'shazam' and split_part(list_id, ':', 2) = 'discovery' then list_id end) as discovery_entries,
        {{ mdp_json_agg(mdp_cluster_evidence('locator', 'e'), 'day, locator') }} as evidence
    from entries e group by 1
), markets as (
    select song_key, count(*) as market_count, {{ mdp_json_agg('market', 'market') }} as markets
    from (select distinct song_key, market from entries where length(market) = 2) m group by 1
), rights as (
    select song_key, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from entries {{ mdp_json_elements('source_keys', 'k') }} group by 1
), placed as (
    select q.*, t.last_entered_at as entered_at, t.window_days as entry_window_days, t.entered_lists, t.entered_charts, t.discovery_entries,
        t.evidence as entry_evidence, cast(coalesce(case when q.movement_list = 'new_entries' then t.rising_tier else t.all_tier end, 4) as integer) as tier
    from {{ ref('int_song_movement__daily') }} q join totals t using (song_key)
    where q.day = {{ mdp_cycle_day() }}
), unfolded as (
    -- Places lists rank new chart markets first. Arrivals carry no score, so new_entries puts emerging artists first.
    -- Exact ties end on a fixed hash of day and song, not key order.
    select cast(row_number() over (partition by q.movement_list order by
            case when q.movement_list in ('established_entries', 'catalog_entries') then q.chart_spread_gain else 0 end desc,
            {{ mdp_song_stage_order('q') }}, q.tier asc, coalesce(m.market_count, 0) desc, q.entered_at desc nulls last,
            md5(cast(q.day as text) || q.song_key)) as bigint) as rank,
        q.song_key, q.cluster_key, q.member_song_keys, q.day, q.title_text, q.artist_text,
        cast(coalesce(m.markets, '[]') as text) as markets,
        cast(q.discovery_entries as bigint) as discovery_entries,
        cast(q.entered_lists as bigint) as entered_lists, cast(q.entered_charts as bigint) as entered_charts,
        cast(q.entry_window_days as integer) as window_days, q.chart_spread_gain, q.tier as list_reach_tier,
        cast(coalesce(m.market_count, 0) as bigint) as market_count, q.entered_at as last_entered_at,
        q.movement_list, q.age_class, q.age_basis, q.artist_stage, q.artist_stage_basis,
        cast('Entered tracked lists or charts over ' || {{ mdp_song_days('q.entry_window_days') }} || '; open evidence.' as text) as reason_rule,
        cast(q.entry_evidence as text) as evidence, r.source_keys as _source_keys
    from placed q left join markets m using (song_key) left join rights r using (song_key)
), rows as ({{ mdp_song_fold_list('unfolded', [
    'rank', 'day', 'song_key', 'cluster_key', 'member_song_keys', 'title_text', 'artist_text',
    'window_days', 'chart_spread_gain', 'list_reach_tier', 'market_count', 'last_entered_at',
    'movement_list', 'age_class', 'age_basis', 'artist_stage', 'artist_stage_basis', 'reason_rule', 'evidence',
    'markets', 'entered_lists', 'entered_charts', 'discovery_entries'], family=false) }})
{{ mdp_annotate('rows') }}
