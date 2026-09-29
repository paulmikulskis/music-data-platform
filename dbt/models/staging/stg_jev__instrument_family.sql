-- depends_on: {{ ref('gold_invoke__jev_instrument_family') }}
{{ config(materialized='view', tags=['gold', 'cadence:daily', 'scope:global']) }}
with visible as (
    select {{ mdp_source_projection('raw', 'jev_instrument_family') }} from {{ source('raw', 'jev_instrument_family') }}
    where {{ mdp_context().manifest_filter('_dump_id', 'raw.jev_instrument_family') }}
), ranked as (
    select *, row_number() over (
        partition by _source_key, scope, input_ref, input_version, step, config_version, question
        order by _landed_seq desc, _dump_id desc
    ) as _identity_rank from visible
)
select * from ranked where _identity_rank = 1
