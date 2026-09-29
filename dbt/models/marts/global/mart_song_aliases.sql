{{ config(tags=['cadence:daily']) }}
with aliases as (
    select *, source_keys as _source_keys from {{ ref('int_song_aliases__daily') }}
)
{{ mdp_annotate('aliases') }}
