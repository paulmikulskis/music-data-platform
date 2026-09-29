{{ config(materialized='table', tags=['cadence:hourly','scope:global']) }}
select 1 as id
