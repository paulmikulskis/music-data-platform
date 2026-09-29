-- depends_on: {{ ref('gold_invoke__mb_resolve') }}
-- depends_on: {{ ref('gold_invoke__track_isrc_crosswalk') }}
{#- design: one ISRC and one MusicBrainz recording per (platform, platform_track_id) from the hourly
    manifest, by mdp_track_identity(), the macro the daily and weekly copies share. -#}
{{ config(materialized='table', tags=['cadence:hourly', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}
{{ mdp_track_identity(ref('int_identity__track_inputs'), ref('int_identity__exact'), ref('int_reference__current'),
                      ref('stg_enrich__mb_resolve'), ref('stg_enrich__track_isrc_crosswalk')) }}
