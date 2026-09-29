-- depends_on: {{ ref('bronze_close__daily') }}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}
-- Daily identity and weekly chart rows read the same daily close.
{{ mdp_billboard_entries(source('raw', 'chart_entries')) }}
