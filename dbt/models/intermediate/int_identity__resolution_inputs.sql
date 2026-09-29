{#- design: mb_resolve's inputs, the priority tracks SQL cannot give a recording. input_version
    hashes the track's own fields, reference_version and retry_week, so every unresolved track is looked
    up again once a week, about 1/168 of the set each hourly cycle; priority is outside every version.
    retry_week and first_landed_seq order the paged read oldest first. -#}
{{ config(materialized='table', tags=['cadence:hourly', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}
with versioned as (
    select t.platform, t.platform_track_id, t.title, t.artist_names, t.duration_ms, t.platform_album_id,
        t.fields_hash, x.reference_version, t.first_landed_seq, t._source_keys,
        {{ mdp_identity_hash(['t.platform', 't.platform_track_id']) }} as input_ref
    from {{ ref('int_identity__track_inputs') }} t
    join {{ ref('int_identity__priority_tracks') }} p on p.platform = t.platform and p.platform_track_id = t.platform_track_id
    join {{ ref('int_identity__exact') }} x on x.platform = t.platform and x.platform_track_id = t.platform_track_id
    where x.exact_recording_id is null
), weeks as (
    select v.*, {{ mdp_retry_week('v.input_ref', mdp_identity_clock('select max(last_observed_at) from ' ~ ref('int_identity__track_inputs'))) }} as retry_week
    from versioned v
)
select platform, platform_track_id, title, artist_names, duration_ms, platform_album_id, fields_hash,
    reference_version, retry_week, first_landed_seq, _source_keys, input_ref,
    {{ mdp_identity_hash(['fields_hash', 'reference_version', 'retry_week']) }} as input_version
from weeks
