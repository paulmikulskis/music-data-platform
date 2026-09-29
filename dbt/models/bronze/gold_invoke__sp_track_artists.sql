{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('sp_track_artists') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_spotify__track_artist_inputs') }}
{{ mdp_invoke('sp_track_artists', input_relation=ref('int_spotify__track_artist_inputs')) }}
