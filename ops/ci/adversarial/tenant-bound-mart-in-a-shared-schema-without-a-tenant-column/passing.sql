{{ config(materialized='table', meta={'tenant_scoped':true}, tags=['cadence:hourly','scope:global']) }}
select 1 as id, 'fixture' as tenant_id
