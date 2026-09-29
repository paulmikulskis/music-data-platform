{# Post-hook for a table others join heavily: fresh statistics on Postgres, so the
   planner sees its real size instead of a one-row guess. #}
{% macro mdp_analyze() -%}
  {%- if target.type == 'postgres' -%}analyze {{ this }}{%- else -%}select 1{%- endif -%}
{%- endmacro %}
