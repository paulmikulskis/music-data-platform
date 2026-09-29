{% macro mdp_empty_receipts() %}
select cast(null as text) as run_id, cast(null as text) as status,
       cast(null as text) as coverage, cast(null as bigint) as rows_written,
       cast(null as bigint) as rows_rejected, cast(null as text) as dump_id,
       cast(null as bigint) as landed_seq, cast(null as text) as trace_url,
       cast(null as text) as message where false
{% endmacro %}

{% macro mdp_invoke(source_key, target_set=none, input_relation=none, target_kinds=none) %}
  {% if mdp_is_local() or not execute %}
    {% set fixture = adapter.get_relation(database=target.database, schema='raw', identifier='_fixture_receipts') if execute else none %}
    {% if fixture is not none %}
select run_id, status, coverage, rows_written, rows_rejected, dump_id, landed_seq, trace_url, message
from {{ fixture }} where source_key = {{ mdp_literal(source_key) }}
    {% else %}{{ mdp_empty_receipts() }}{% endif %}
  {% else %}
    {% set context = mdp_context(invoke=true) %}
    {% set deadline = mdp_timeout_seconds(source_key) - 30 %}
    {% if deadline <= 0 %}{{ exceptions.raise_compiler_error('invoke_timeout: set timeout_s above the 30-second safety margin for ' ~ source_key) }}{% endif %}
select * from mdp.invoke({{ mdp_literal(source_key) }}, jsonb_build_object(
    'cadence', {{ mdp_literal(context.cadence) }},
    'target_set', {{ mdp_literal(target_set) }},
    'dbt_run_id', {{ mdp_literal(env_var('DBT_CLOUD_RUN_ID')) }},
    'invocation_id', {{ mdp_literal(invocation_id) }},
    'model', {{ mdp_literal(model.unique_id) }},
    'input_relation', {{ mdp_literal(input_relation) }},
    {%- if target_kinds is not none %}
    'target_kinds', {{ mdp_literal(tojson(target_kinds)) }}::jsonb,
    {%- endif %}
    'deadline_s', {{ deadline }}))
  {% endif %}
{% endmacro %}
