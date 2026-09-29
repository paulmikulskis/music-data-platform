{# A lateral join yielding one row per item of a `;`-separated text list, as `alias.value`. A null
   list yields no row; DuckDB yields one empty item for an empty list, so callers keep only
   `trim(alias.value) <> ''`. #}
{% macro mdp_list_elements(list, alias) -%}
  {%- if target.type == 'duckdb' -%}cross join unnest(string_split({{ list }}, ';')) as {{ alias }}(value)
  {%- elif target.type == 'snowflake' -%}cross join lateral (select f.value::string as value from table(flatten(input => split({{ list }}, ';'))) f) {{ alias }}
  {%- else -%}cross join lateral unnest(string_to_array({{ list }}, ';')) as {{ alias }}(value){%- endif -%}
{%- endmacro %}
