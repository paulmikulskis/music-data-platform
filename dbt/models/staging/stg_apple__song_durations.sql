-- depends_on: {{ ref('bronze_close__daily') }}
-- depends_on: {{ ref('bronze_invoke__apple_song_duration') }}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}
with visible as (
    select *, row_number() over (partition by apple_song_id order by
        {{ mdp_dedupe_order('raw.apple_song_durations') }}) as pick
    from {{ source('raw', 'apple_song_durations') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.apple_song_durations') }}
)
select apple_song_id, duration_ms, status, observed_at,
    _run_id, _dump_id, _landed_seq, _cycle_id, _revision_id, _target_id,
    _request_id, _source_key, _ingested_at, _extra
from visible where pick = 1
