{#- design: where a lower-precedence method disagrees with the kept ISRC, from the daily manifest. -#}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}") }}
with generations as ({{ mdp_reference_generations() }}),
reference as ({{ mdp_reference_current('generations', ['isrc', 'url_link', 'recording', 'redirect']) }}),
inputs as ({{ mdp_track_inputs() }}),
exact as ({{ mdp_track_exact('inputs', 'reference') }}),
resolutions as ({{ mdp_enrichment_rows('mb_resolve') }}),
crosswalk as ({{ mdp_enrichment_rows('track_isrc_crosswalk') }})
select * from ({{ mdp_track_identity('inputs', 'exact', 'reference', 'resolutions', 'crosswalk', audit=true) }}) disagreements
