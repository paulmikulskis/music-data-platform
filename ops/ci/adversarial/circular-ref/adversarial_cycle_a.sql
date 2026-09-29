{{ config(materialized='table', tags=['cadence:hourly','scope:global']) }}
select * from {{ ref('adversarial_cycle_b') }}
