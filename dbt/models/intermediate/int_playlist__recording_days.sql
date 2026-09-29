{{ config(materialized='ephemeral', tags=['cadence:daily', 'scope:global']) }}
select d.day, d.platform, d.playlist_id, d.variant, d.stream, d.owner_class, d.occurrence_key, d.interval_id,
    d.platform_track_id, d.best_position, i.mb_recording_gid, d.followers, d.source_keys
from {{ ref('int_playlist__track_days') }} d
join {{ ref('int_track_identity__daily') }} i
    on i.platform = d.platform and i.platform_track_id = d.platform_track_id
where i.mb_recording_gid is not null
