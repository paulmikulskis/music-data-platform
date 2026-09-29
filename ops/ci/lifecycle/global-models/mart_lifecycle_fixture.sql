-- Synthetic metric used to verify retry, history and cycle isolation.
{{ config(materialized='table', schema='marts', tags=['cadence:hourly', 'scope:global']) }}
with history as (
 select *, followers - lag(followers) over (
  partition by platform, platform_account_id order by snapshot_at, _landed_seq
 ) as followers_delta,
 row_number() over (partition by platform, platform_account_id order by snapshot_at desc, _landed_seq desc) as latest
 from {{ ref('stg_fixture_accounts') }}
)
select platform, platform_account_id, handle, snapshot_at, followers, followers_delta,
 {{ mdp_literal(mdp_context().cycle_id) }}::text as _cycle_id
from history where latest=1
{% if var('lifecycle_fail_transform', false) %}
and cast('lifecycle_fail_transform' as integer) = 1
{% endif %}
