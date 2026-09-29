{% macro mdp_tenant_cycle_filter(sql_column) %}
  {% if not execute or mdp_is_local() %}{{ return('true') }}{% endif %}
  {{ return(sql_column ~ ' in (select id from raw.cycles where scope = ' ~ mdp_literal(mdp_context().scope) ~ ')') }}
{% endmacro %}

{% macro mdp_closed_tenant_cycles(sql_column) %}
  {% if not execute or mdp_is_local() %}{{ return('true') }}{% endif %}
  {{ return(sql_column ~ " in (select id from raw.cycles where scope like 'tenant:%' and status = 'closed')") }}
{% endmacro %}

{% macro mdp_median(expr) -%}
percentile_cont(0.5) within group (order by {{ expr }})
{%- endmacro %}

{% macro mdp_regex_extract(expr, pattern) -%}
  {%- if target.type == 'duckdb' -%}nullif(regexp_extract({{ expr }}, {{ mdp_literal(pattern) }}, 1), '')
  {%- elif target.type == 'snowflake' -%}regexp_substr({{ expr }}, {{ mdp_literal(pattern) }}, 1, 1, 'e')
  {%- else -%}substring({{ expr }} from {{ mdp_literal(pattern) }}){%- endif -%}
{%- endmacro %}

{% macro mdp_ref_if_built(model_name, columns) -%}
  {%- set relation = ref(model_name) -%}
  {%- if execute and load_relation(relation) is none -%}
(select {% for name, type in columns %}cast(null as {{ type }}) as {{ name }}{{ ', ' if not loop.last }}{% endfor %} where false)
  {%- else -%}{{ relation }}{%- endif -%}
{%- endmacro %}
