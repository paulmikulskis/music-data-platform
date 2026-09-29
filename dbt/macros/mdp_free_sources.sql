{# The bound cycle's UTC day, from its clock (mdp_cycle_clock): the version a daily free-source input
   carries, so every cycle's inputs are its own and a Replay of that cycle reads the same ones. A local build
   reads var cycle_opened_at, else the current time. #}
{% macro mdp_cycle_day() -%}
cast({{ mdp_cycle_clock() }} as date)
{%- endmacro %}

{# The Monday of the bound cycle's ISO week: the version of a lookup the daily job makes once a week. #}
{% macro mdp_cycle_week() -%}
{{ mdp_local_week(mdp_cycle_clock(), mdp_context().timezone) }}
{%- endmacro %}
