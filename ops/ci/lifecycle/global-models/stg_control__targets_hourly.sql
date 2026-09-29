-- Synthetic frozen membership read for lifecycle replay checks.
{{ config(materialized='table', schema='staging', tags=['cadence:hourly','scope:global']) }}
select * from {{ source('raw','targets') }}
where _cycle_id = {{ mdp_literal(mdp_context().cycle_id) }}::uuid
