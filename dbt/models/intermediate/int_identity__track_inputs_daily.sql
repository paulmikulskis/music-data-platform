-- depends_on: {{ ref('bronze_close__daily') }}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'],
    pre_hook='{{ mdp_hash_join_plan() }}', post_hook='{{ mdp_analyze() }}') }}
{{ mdp_track_inputs() }}
