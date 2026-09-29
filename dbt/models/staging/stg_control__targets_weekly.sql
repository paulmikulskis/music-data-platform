-- depends_on: {{ ref('bronze_invoke__billboard_hot100') }}
{{ config(tags=['cadence:weekly']) }}
-- depends_on: {{ ref('bronze_export__targets_weekly') }}
with selected as (
    select *, row_number() over (
        partition by platform, platform_account_id order by _revision_id desc
    ) as _rn
    from {{ source('raw', 'targets') }}
    {% if not mdp_is_local() %}
    where cast(_cycle_id as text) = {{ mdp_literal(mdp_context().cycle_id) }}
    {% endif %}
)
select id, platform, platform_account_id, handle, display_name, role,
       target_set_id, resource_kind, canonical_key, params_json, taken_at, _cycle_id, _revision_id
from selected where _rn = 1
