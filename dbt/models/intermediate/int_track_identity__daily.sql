{#- design: the daily copy of track identity. Every input is this daily cycle's own manifest: its
    reference rows, track inputs and the manifest-filtered mb_resolve and crosswalk rows, through the
    same mdp_track_identity() the hourly int_track_identity uses. An hourly resolution committed after
    the daily close is outside this manifest, and a replay reads the same one. -#}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}",
          post_hook="{{ mdp_analyze() }}") }}
with generations as ({{ mdp_reference_generations() }}),
reference as ({{ mdp_reference_current('generations', ['isrc', 'url_link', 'recording', 'redirect']) }}),
inputs as (select * from {{ ref('int_identity__track_inputs_daily') }}),
exact as ({{ mdp_track_exact('inputs', 'reference') }}),
resolutions as ({{ mdp_enrichment_rows('mb_resolve') }}),
crosswalk as ({{ mdp_enrichment_rows('track_isrc_crosswalk') }})
select * from ({{ mdp_track_identity('inputs', 'exact', 'reference', 'resolutions', 'crosswalk') }}) identity_rows
