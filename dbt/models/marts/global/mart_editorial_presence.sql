{{ config(tags=['cadence:daily']) }}

with presence as (
    select *, source_keys as _source_keys
    from {{ ref('mart_playlist_events') }}
    where owner_class in ('editorial','dsp_algorithmic','chart')
        and event_type in ('baseline','entry_unknown','entered_head')
)
{{ mdp_annotate('presence') }}
