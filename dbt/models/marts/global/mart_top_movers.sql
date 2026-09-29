{{ config(tags=['cadence:daily']) }}
with qualified as (
    select * from {{ ref('int_song_movement__daily') }}
    where {{ mdp_song_positive_families() }} >= 2
), positives as (
select day, song_key, movement_list, 'playlist_adds' as component, cast(playlist_adds as double precision) as value, cast({{ mdp_movement_parameter('playlist_adds_weight') }} as double precision) as weight, playlist_window_days as window_days from qualified where playlist_adds > 0
union all
select day, song_key, movement_list, 'follower_exposure_gain' as component, cast(follower_exposure_gain as double precision) as value, cast({{ mdp_movement_parameter('follower_exposure_gain_weight') }} as double precision) as weight, playlist_window_days from qualified where follower_exposure_gain > 0
union all
select day, song_key, movement_list, 'shazam_spread_gain' as component, cast(shazam_spread_gain as double precision) as value, cast({{ mdp_movement_parameter('shazam_spread_gain_weight') }} as double precision) as weight, shazam_window_days from qualified where shazam_spread_gain > 0
union all
select day, song_key, movement_list, 'stream_rate_gain' as component, cast(stream_rate_gain as double precision) as value, cast({{ mdp_movement_parameter('stream_rate_gain_weight') }} as double precision) as weight, stream_window_days from qualified where stream_rate_gain > 0
), percentiles as (
    -- Each movement list scores against its own songs only.
    select *, cume_dist() over (partition by day, movement_list, component order by value) as percentile
    from positives
), scores as (
    select day, song_key, movement_list, sum(weight * percentile) as momentum_score,
        {{ mdp_json_agg(mdp_json_object([('component', 'component'), ('value', 'value'), ('weight', 'weight'), ('percentile', 'percentile'), ('window_days', 'window_days')]), 'component') }} as score_parts
    from percentiles group by 1, 2, 3
), evidence as (
    select q.day, q.song_key,
        {{ mdp_json_agg(mdp_cluster_evidence('e.locator', 'c'), 'e.component, e.day, e.locator') }} as evidence
    from qualified q join {{ ref('int_song_cluster__daily') }} c on c.cluster_key = q.song_key
    join {{ ref('int_song_evidence__daily') }} e
        on e.song_key = c.song_key and e.day between least(q.shazam_baseline_day, q.day - cast(2 * {{ mdp_movement_parameter('window_max_days') }} - 1 as integer)) and q.day
    where (e.component = 'playlist_adds' and q.playlist_adds > 0 and e.day > q.day - q.playlist_window_days)
       or (e.component = 'follower_exposure_gain' and q.follower_exposure_gain > 0 and e.day > q.day - q.playlist_window_days
           and (q.movement_list is distinct from 'new_entries' or e.list_kind is distinct from 'chart'))
       or (e.component = 'shazam_spread_gain' and q.shazam_spread_gain > 0 and e.day >= q.shazam_baseline_day)
       or (e.component = 'stream_rate_gain' and q.stream_rate_gain > 0 and e.day > q.day - 2 * q.stream_window_days)
    group by 1, 2
), rights as (
    select q.day, q.song_key, {{ mdp_source_keys_agg('k.value') }} as source_keys
    from qualified q join {{ ref('int_cluster_day__daily') }} s
        on s.song_key = q.song_key and s.day between least(q.shazam_baseline_day, q.day - cast(2 * {{ mdp_movement_parameter('window_max_days') }} - 1 as integer)) and q.day
    {{ mdp_json_elements('s.source_keys', 'k') }} group by 1, 2
), ranked as (
    -- Places lists rank new chart markets first; new_entries puts emerging artists first among equal scores.
    -- Exact ties end on a fixed hash of day and song, not key order.
    select cast(row_number() over (partition by q.day, q.movement_list order by
            case when q.movement_list in ('established_entries', 'catalog_entries') then q.chart_spread_gain else 0 end desc,
            s.momentum_score desc, {{ mdp_song_stage_order('q') }}, q.list_reach_tier asc, q.market_count desc, q.last_entered_at desc nulls last,
            md5(cast(q.day as text) || q.song_key)) as bigint) as rank,
        q.day, q.song_key, q.cluster_key, q.member_song_keys, q.title_text, q.artist_text, q.window_days, q.chart_spread_gain, q.list_reach_tier, q.market_count, q.last_entered_at,
        q.movement_list, q.age_class, q.age_basis, q.artist_stage, q.artist_stage_basis, cast(s.momentum_score as double precision) as momentum_score,
        s.score_parts,
        cast({{ mdp_json_object([('playlists', 'q.playlists_observed = 1'), ('shazam', 'q.shazam_observed = 1'), ('streams', 'q.streams_observed = 1')]) }} as text) as coverage,
        cast('Rising: ' || concat_ws(', ',
            case when q.playlist_adds > 0 then 'playlist entries over ' || {{ mdp_song_days('q.playlist_window_days') }} end,
            case when q.follower_exposure_gain > 0 then 'follower exposure over ' || {{ mdp_song_days('q.playlist_window_days') }} end,
            case when q.shazam_spread_gain > 0 then 'Shazam spread over ' || {{ mdp_song_days('q.shazam_window_days') }} end,
            case when q.stream_rate_gain > 0 then 'stream rate over ' || {{ mdp_song_days('q.stream_window_days') }} end) || '; open evidence.' as text) as reason_rule,
        cast(coalesce(e.evidence, '[]') as text) as evidence, cast({{ mdp_literal(invocation_id) }} as text) as ranking_build,
        r.source_keys as _source_keys
    from qualified q join scores s using (day, song_key) left join evidence e using (day, song_key)
    left join rights r using (day, song_key)
)
{{ mdp_annotate('ranked') }}
