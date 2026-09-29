{# Portable JSON access for mirrored json/jsonb columns. #}
{% macro mdp_json_text(column, key) -%}
  {%- if target.type == 'duckdb' -%}json_extract_string({{ column }}, '$.{{ key }}')
  {%- elif target.type == 'snowflake' -%}parse_json({{ column }}):{{ key }}::string
  {%- else -%}(cast({{ column }} as jsonb) ->> '{{ key }}'){%- endif -%}
{%- endmacro %}

{% macro mdp_json_value(column, key) -%}
  {%- if target.type == 'duckdb' -%}json_extract({{ column }}, '$.{{ key }}')
  {%- elif target.type == 'snowflake' -%}parse_json({{ column }}):{{ key }}
  {%- else -%}(cast({{ column }} as jsonb) -> '{{ key }}'){%- endif -%}
{%- endmacro %}

{# A lateral join yielding one row per string element of a JSON array, as `alias.value`. #}
{% macro mdp_json_elements(array, alias) -%}
  {%- if target.type == 'duckdb' -%}cross join unnest(cast(json_extract_string({{ array }}, '$[*]') as varchar[])) as {{ alias }}(value)
  {%- elif target.type == 'snowflake' -%}cross join lateral (select f.value::string as value from table(flatten(input => parse_json({{ array }}))) f) {{ alias }}
  {%- else -%}cross join lateral jsonb_array_elements_text(case when jsonb_typeof(cast({{ array }} as jsonb)) = 'array' then cast({{ array }} as jsonb) end) as {{ alias }}(value){%- endif -%}
{%- endmacro %}

{# JSON array text of an aggregate's values in order ('[]' for none); `expr` may start with distinct. DuckDB's
   json_group_array is a macro, which takes no ORDER BY or DISTINCT, so it aggregates a list. #}
{% macro mdp_json_agg(expr, order_by) -%}
  {%- if target.type == 'duckdb' -%}cast(coalesce(to_json(list({{ expr }} order by {{ order_by }})), '[]') as varchar)
  {%- elif target.type == 'snowflake' -%}to_json(array_agg({{ expr }}) within group (order by {{ order_by }}))
  {%- else -%}cast(coalesce(jsonb_agg({{ expr }} order by {{ order_by }}), '[]'::jsonb) as text){%- endif -%}
{%- endmacro %}

{# A JSON object of (key, expression) pairs, for mdp_json_agg. #}
{% macro mdp_json_object(pairs) -%}
  {%- set args = [] -%}
  {%- for key, expr in pairs -%}{%- do args.append(mdp_literal(key) ~ ', ' ~ expr) -%}{%- endfor -%}
  {%- if target.type == 'duckdb' -%}json_object({{ args | join(', ') }})
  {%- elif target.type == 'snowflake' -%}object_construct_keep_null({{ args | join(', ') }})
  {%- else -%}jsonb_build_object({{ args | join(', ') }}){%- endif -%}
{%- endmacro %}

{# The sorted, distinct source keys of a group as JSON array text ('["a","b"]', '[]' for none), the form
   mdp_annotate reads. #}
{% macro mdp_source_keys_agg(column) -%}
cast(coalesce('[' || string_agg(distinct '"' || {{ column }} || '"', ',' order by '"' || {{ column }} || '"') || ']', '[]') as text)
{%- endmacro %}

{# A naive UTC timestamp as ISO 8601 text (YYYY-MM-DDTHH:MM:SS), the same on every adapter, for a JSON value. #}
{% macro mdp_timestamp_text(expr) -%}
  {%- if target.type == 'duckdb' -%}strftime(cast({{ expr }} as timestamp), '%Y-%m-%dT%H:%M:%S')
  {%- elif target.type == 'snowflake' -%}to_char(cast({{ expr }} as timestamp_ntz), 'YYYY-MM-DD"T"HH24:MI:SS')
  {%- else -%}to_char(cast({{ expr }} as timestamp), 'YYYY-MM-DD"T"HH24:MI:SS'){%- endif -%}
{%- endmacro %}

{# Combine the actual keys and input-key arrays carried by joined rows; null inputs contribute nothing. #}
{% macro mdp_source_keys(keys=[], arrays=[]) -%}
(select {{ mdp_source_keys_agg('mdp_key') }}
 from (
   {% for key in keys %}
   {% if not loop.first %}union all{% endif %} select cast({{ key }} as text) as mdp_key
   {% endfor %}
   {% for array in arrays %}
   {% if keys or not loop.first %}union all{% endif %}
   select mdp_element.value as mdp_key
   from (select {{ array }} as mdp_array) mdp_input
   {{ mdp_json_elements('mdp_input.mdp_array', 'mdp_element') }}
   {% endfor %}
 ) mdp_carried_keys)
{%- endmacro %}
