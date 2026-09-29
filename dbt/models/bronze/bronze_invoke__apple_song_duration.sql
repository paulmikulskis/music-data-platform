{{ config(materialized='table', tags=['bronze', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('apple_song_duration') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
{{ mdp_invoke('apple_song_duration', target_set='track') }}
