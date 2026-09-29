{# The Monday of the local ISO week of a UTC timestamp (a mart's naive UTC `timestamp`) in a timezone:
   the week a crossing's evidence falls in. Postgres and DuckDB (ICU) share timezone(). #}
{% macro mdp_local_week(ts, tz, tz_sql=false) -%}
cast(date_trunc('week', timezone({{ tz if tz_sql else mdp_literal(tz) }}, timezone('UTC', {{ ts }}))) as date)
{%- endmacro %}

{# The bound tenant cycle's call week (mdp_context().call_week): a local build without a call_week
   var reads the week before the current one. #}
{% macro mdp_call_week() -%}
  {%- set week = mdp_context().call_week -%}
  {%- if week -%}cast({{ mdp_literal(week) }} as date)
  {%- else -%}({{ mdp_local_week(mdp_cycle_clock(), mdp_context().timezone) }} - 7){%- endif -%}
{%- endmacro %}

{# The bound cycle's clock as a naive UTC timestamp (mdp_context().opened_at); a local build reads var
   cycle_opened_at, else the current time. #}
{% macro mdp_cycle_clock() -%}
  {%- set opened = mdp_context().opened_at -%}
  {%- if opened -%}cast({{ mdp_literal(opened) }} as timestamp)
  {%- else -%}cast(timezone('UTC', current_timestamp) as timestamp){%- endif -%}
{%- endmacro %}

{# A published calendar date has no event time. Preserve that date at local midnight before bucketing. #}
{% macro mdp_calendar_week(day, tz) -%}
{{ mdp_local_week("timezone('UTC', timezone(" ~ mdp_literal(tz) ~ ", cast(" ~ day ~ " as timestamp)))", tz) }}
{%- endmacro %}
