{{ config(tags=['cadence:daily']) }}
with single_family as (
    select * from {{ ref('int_song_movement__daily') }}
    where day = {{ mdp_cycle_day() }} and {{ mdp_song_positive_families() }} = 1
), components as (
    select *,
        case when playlist_adds > 0 or follower_exposure_gain > 0 then 'playlists'
            when shazam_spread_gain > 0 then 'shazam' else 'streams' end as family,
        case when playlist_adds > 0 then 'playlist_adds'
            when follower_exposure_gain > 0 then 'follower_exposure_gain'
            when shazam_spread_gain > 0 then 'shazam_spread_gain' else 'stream_rate_gain' end as component,
        case when playlist_adds > 0 then playlist_adds
            when follower_exposure_gain > 0 then follower_exposure_gain
            when shazam_spread_gain > 0 then shazam_spread_gain else stream_rate_gain end as value
    from single_family
), selected as (
    select *, cast(case when family = 'playlists' then playlist_window_days
        when family = 'shazam' then day - shazam_baseline_day else stream_window_days end as integer) as family_window_days
    from components
), member_evidence as materialized (
    -- Keep key equality in the scoring join; component-only joins multiply unrelated songs.
    select e.*, c.cluster_key, c.member_song_keys, c.cluster_methods, c.cluster_confidence
    from {{ ref('int_song_evidence__daily') }} e join {{ ref('int_song_cluster__daily') }} c using (song_key)
), evidence as (
    select q.day, q.song_key,
        {{ mdp_json_agg(mdp_cluster_evidence('e.locator', 'e'), 'e.component, e.day, e.locator') }} as evidence
    from selected q join member_evidence e
        on e.cluster_key = q.song_key and e.component = q.component
        and e.day between least(q.shazam_baseline_day, q.day - cast(2 * {{ mdp_movement_parameter('window_max_days') }} - 1 as integer)) and q.day
    where (q.component = 'playlist_adds' and e.day > q.day - q.playlist_window_days)
       or (q.component = 'follower_exposure_gain' and e.day > q.day - q.playlist_window_days
           and (q.movement_list is distinct from 'new_entries' or e.list_kind is distinct from 'chart'))
       or (q.component = 'shazam_spread_gain' and e.day >= q.shazam_baseline_day)
       or (q.component = 'stream_rate_gain' and e.day > q.day - 2 * q.stream_window_days)
    group by 1, 2
), rights_inputs as (
    select distinct q.day, q.song_key, s.source_keys
    from selected q join {{ ref('int_cluster_day__daily') }} s
        on s.song_key = q.song_key and s.day between least(q.shazam_baseline_day, q.day - cast(2 * {{ mdp_movement_parameter('window_max_days') }} - 1 as integer)) and q.day
), rights as (
    select day, song_key, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from rights_inputs
    {{ mdp_json_elements('source_keys', 'k') }} group by 1, 2
), unfolded as (
    select cast(q.family as text) as family,
        -- Places lists rank new chart markets first; new_entries puts emerging artists first among equal values.
        -- Exact ties end on a fixed hash of day and song, not key order.
        cast(row_number() over (partition by q.movement_list, q.family order by
            case when q.movement_list in ('established_entries', 'catalog_entries') then q.chart_spread_gain else 0 end desc,
            case when q.component = 'playlist_adds' then 0 else 1 end,
            q.value desc, {{ mdp_song_stage_order('q') }}, q.list_reach_tier asc, q.market_count desc, q.last_entered_at desc nulls last,
            md5(cast(q.day as text) || q.song_key)) as bigint) as rank,
        q.day, q.song_key, q.cluster_key, q.member_song_keys, q.title_text, q.artist_text, cast(q.family_window_days as integer) as window_days, q.chart_spread_gain, q.list_reach_tier, q.market_count, q.last_entered_at,
        q.movement_list, q.age_class, q.age_basis, q.artist_stage, q.artist_stage_basis,
        cast(q.component as text) as component, cast(q.value as double precision) as value,
        cast(case q.component
            when 'playlist_adds' then 'Seen on playlists over ' || {{ mdp_song_days('q.family_window_days') }} || '; open evidence.'
            when 'follower_exposure_gain' then 'Reaching more playlist followers over ' || {{ mdp_song_days('q.family_window_days') }} || '; open evidence.'
            when 'shazam_spread_gain' then 'Spreading across Shazam charts over ' || {{ mdp_song_days('q.family_window_days') }} || '; open evidence.'
            else 'Daily plays are growing over ' || {{ mdp_song_days('q.family_window_days') }} || '; open evidence.' end as text) as reason_rule,
        cast(coalesce(e.evidence, '[]') as text) as evidence, r.source_keys as _source_keys
    from selected q left join evidence e using (day, song_key) left join rights r using (day, song_key)
), ranked as ({{ mdp_song_fold_list('unfolded', [
    'rank', 'day', 'song_key', 'cluster_key', 'member_song_keys', 'title_text', 'artist_text',
    'window_days', 'chart_spread_gain', 'list_reach_tier', 'market_count', 'last_entered_at',
    'movement_list', 'age_class', 'age_basis', 'artist_stage', 'artist_stage_basis', 'reason_rule', 'evidence',
    'family', 'component', 'value'], family=true) }})
{{ mdp_annotate('ranked') }}
