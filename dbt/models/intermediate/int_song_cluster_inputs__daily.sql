{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
with crosswalk as ({{ mdp_enrichment_rows('track_isrc_crosswalk') }}), picked as (
    select x.*, row_number() over (partition by x.platform, x.platform_track_id order by
        x.retry_week desc nulls last, c.close_no desc nulls last, x._landed_seq desc, x._dump_id desc) as pick
    from crosswalk x join {{ ref('int_identity__track_inputs_daily') }} t
        on x.platform = t.platform and x.platform_track_id = t.platform_track_id and x.fields_hash = t.fields_hash
    left join {{ source('raw', 'cycles') }} c on c.id = x._cycle_id
)
select k.*, coalesce(t.duration_ms, d.duration_ms) as duration_ms,
    case when t.duration_ms is null and d.duration_ms > 0 then d._source_key end as duration_source_key,
    {{ mdp_fold_name('coalesce(t.title, k.title_text)') }} as folded_title,
    {{ mdp_fold_name('coalesce(' ~ mdp_song_first_artist('t.artist_names') ~ ', k.artist_text)') }} as folded_artist,
    case when k.platform = 'spotify' and k.song_key = 'spotify:' || k.platform_track_id
        and x.status = 'resolved' and x.confidence >= 1.0 then x.isrc end as crosswalk_isrc,
    case when x.status = 'resolved' then x._source_key end as crosswalk_source_key
from {{ ref('int_song_key__daily') }} k
left join {{ ref('int_identity__track_inputs_daily') }} t
    on {{ mdp_song_platform('t.platform') }} = k.platform and t.platform_track_id = k.platform_track_id
left join picked x on x.platform = k.platform and x.platform_track_id = k.platform_track_id and x.pick = 1
left join {{ ref('stg_apple__song_durations') }} d
    on k.platform = 'apple' and d.apple_song_id = k.platform_track_id and d.status = 'found'
