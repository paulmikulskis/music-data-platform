{# A keyed one-way pseudonym for a personal identifier (fan and listener handles, design).
   The key is mdp.pseudonym_key, created once by the warehouse grants and readable only by
   dbt_transform, so it never appears in compiled SQL. Local and inert builds use a fixed key. #}
{% macro mdp_pseudonym(expr) -%}
{# Workbench sources point at explore_raw, whose declared identifiers are already pseudonyms. #}
{%- if target.name == 'workbench' -%}
  {{ return(expr) }}
{%- endif -%}
{%- set key = "'mdp-local-pseudonym'" if mdp_is_local() else "(select key from mdp.pseudonym_key)" -%}
{%- if target.type == 'postgres' -%}
case when {{ expr }} is not null then encode(sha256(convert_to({{ key }} || ':' || {{ expr }}, 'UTF8')), 'hex') end
{%- else -%}
case when {{ expr }} is not null then sha256({{ key }} || ':' || {{ expr }}) end
{%- endif -%}
{%- endmacro %}
