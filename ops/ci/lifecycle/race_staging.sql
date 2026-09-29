{{ config(materialized='table', schema='staging', tags=['cadence:hourly', 'scope:global']) }}
select * from raw.lifecycle_daily_probe
where {{ mdp_context().manifest_filter('_dump_id', 'raw.lifecycle_daily_probe') }}
