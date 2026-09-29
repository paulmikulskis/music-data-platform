{{ config(materialized='table', tags=['gold', 'invoke', 'cadence:daily', 'scope:global'],
          pre_hook="{{ mdp_statement_timeout('jev_instrument_family') }}") }}
-- depends_on: {{ ref('bronze_export__targets_daily') }}
-- depends_on: {{ ref('int_jev_instruments') }}
{{ mdp_invoke('jev_instrument_family', input_relation=ref('int_jev_instruments')) }}
