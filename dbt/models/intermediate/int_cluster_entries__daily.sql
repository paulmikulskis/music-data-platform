{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with chart_days as (
    select chart, chart_date, lag(chart_date) over (partition by chart order by chart_date) as previous_day
    from (select distinct chart, chart_date from {{ ref('int_cluster_shazam__daily') }}) d
), snapshots as (
    -- A missing or partial observation cannot prove absence. Tied reads prove presence only.
    select platform, playlist_id, variant, stream, snapshot_id, observed_at,
        case when effective_coverage = 'full'
            and count(*) over (partition by platform, playlist_id, variant, stream, observed_at) = 1
            then 1 else 0 end as is_full
    from {{ ref('int_playlist__snapshots') }}
), numbered as (
    select *, sum(is_full) over (partition by platform, playlist_id, variant, stream
        order by observed_at, snapshot_id rows unbounded preceding) as hi
    from snapshots
), presence as (
    select distinct c.cluster_key, s.platform, s.playlist_id, s.variant, s.stream,
        s.snapshot_id, s.observed_at, s.hi, s.hi - s.is_full as lo
    from numbered s join {{ ref('int_playlist__observations') }} o
        using (platform, playlist_id, variant, stream, snapshot_id)
    join {{ ref('int_song_key__daily') }} k
        on k.platform = {{ mdp_song_platform('o.platform') }} and k.platform_track_id = o.platform_track_id
    join {{ ref('int_song_cluster__daily') }} c using (song_key)
), continuity as (
    -- A full snapshot without any member separates two visits to this list.
    select *, lag(hi) over (partition by cluster_key, platform, playlist_id, variant, stream
        order by observed_at, snapshot_id) as previous_hi
    from presence
)
select c.cluster_key as song_key, e.day, e.observed_at, e.platform, e.list_id, e.snapshot_id,
    e.owner_class, e.list_kind, e.event_type, e.confidence, e.followers, e.list_reach_tier, e.market,
    {{ mdp_source_keys(arrays=['e.source_keys', 'c.source_keys']) }} as source_keys, e.locator
from {{ ref('int_song_entries__daily') }} e join {{ ref('int_song_cluster__daily') }} c using (song_key)
left join chart_days d on e.platform = 'shazam' and d.chart = e.list_id and d.chart_date = e.day
left join {{ ref('int_cluster_shazam__daily') }} prior
    on prior.song_key = c.cluster_key and prior.chart = e.list_id and prior.chart_date = d.previous_day
left join continuity p on p.cluster_key = c.cluster_key and p.platform = e.platform
    and p.playlist_id = e.list_id and p.snapshot_id = e.snapshot_id
    and p.variant = {{ mdp_json_text(mdp_json_value('e.locator', 'row_key'), 'variant') }}
    and p.stream = {{ mdp_json_text(mdp_json_value('e.locator', 'row_key'), 'stream') }}
where (e.platform = 'shazam' and prior.song_key is null)
    or (e.platform <> 'shazam' and (p.previous_hi is null or p.lo > p.previous_hi))
