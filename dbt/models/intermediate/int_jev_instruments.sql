{{ config(materialized='view', tags=['silver', 'cadence:daily', 'scope:global']) }}
-- Public instrument facts. Expected labels stay outside vendor state.
select cast(state_id as text) as state_id, cast(instrument as text) as instrument,
    cast('["jev_instruments"]' as text) as _source_keys,
    {{ mdp_input_identity(['state_id'], ['instrument']) }}
from {{ ref('jev_instruments') }}
