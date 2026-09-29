{% macro playlist_utc(column) -%}
  {% if target.type == 'snowflake' %}
    convert_timezone('UTC', {{ column }})
  {% else %}
    ({{ column }} at time zone 'UTC')
  {% endif %}
{%- endmacro %}
