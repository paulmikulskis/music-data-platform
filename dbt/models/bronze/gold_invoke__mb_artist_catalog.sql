{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('mb_artist_catalog') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_artist_catalog__inputs') }}
{{ mdp_invoke('mb_artist_catalog', input_relation=ref('int_artist_catalog__inputs')) }}
