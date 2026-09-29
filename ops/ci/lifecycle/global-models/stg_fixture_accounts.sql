-- Synthetic runtime accounting input; installed only in the lifecycle harness overlay.
-- depends_on: {{ ref('bronze_invoke__fixture_accounts') }}
-- depends_on: {{ ref('bronze_close__hourly') }}
{{ config(materialized='table', schema='staging', tags=['cadence:hourly', 'scope:global']) }}
select * from {{ source('raw', 'account_snapshots') }}
where {{ mdp_context().manifest_filter('_dump_id', 'raw.account_snapshots') }}
