{#- design: the crosswalk's inputs, the priority Spotify tracks with no ISRC from the platform or the
    landed MusicBrainz rows, versioned by their own fields and reference_version only, so a public search
    repeats only when those change. retry_week and first_landed_seq order the paged read oldest first. -#}
{{ config(materialized='table', tags=['cadence:hourly', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}
with versioned as (
    select t.platform, t.platform_track_id, t.title, t.artist_names, t.duration_ms, t.platform_album_id,
        t.album_title, t.fields_hash, x.reference_version, t.first_landed_seq, t._source_keys,
        {{ mdp_identity_hash(['t.platform', 't.platform_track_id']) }} as input_ref
    from {{ ref('int_identity__track_inputs') }} t
    join {{ ref('int_identity__priority_tracks') }} p on p.platform = t.platform and p.platform_track_id = t.platform_track_id
    join {{ ref('int_identity__exact') }} x on x.platform = t.platform and x.platform_track_id = t.platform_track_id
    where t.platform = 'spotify' and x.exact_isrc is null
)
select v.*,
    {{ mdp_retry_week('v.input_ref', mdp_identity_clock('select max(last_observed_at) from ' ~ ref('int_identity__track_inputs'))) }} as retry_week,
    {{ mdp_identity_hash(['v.fields_hash', 'v.reference_version']) }} as input_version
from versioned v
