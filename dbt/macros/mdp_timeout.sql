{% macro mdp_timeout_seconds(source_key) %}
  {% set seconds = 900 %}
  {% if execute and target.name != 'workbench' %}
    {% set relation = adapter.get_relation(database=target.database, schema='raw', identifier='streamlines') %}
    {% if relation is not none %}
      {% set result = run_query('select timeout_s from ' ~ relation ~ ' where source_key=' ~ mdp_literal(source_key)) %}
      {% if result.rows | length and result.rows[0][0] is not none %}
        {% set seconds = result.rows[0][0] | int %}
      {% endif %}
    {% endif %}
  {% endif %}
  {{ return(seconds) }}
{% endmacro %}

{% macro mdp_timeout(source_key) %}
  {{ return((mdp_timeout_seconds(source_key) | string) ~ 's') }}
{% endmacro %}

{% macro mdp_statement_timeout(source_key) %}
  {# The service sets the session timeout; an inert invoke must not widen it. #}
  {% if target.type == 'postgres' and target.name != 'workbench' %}
    {{ return('set local statement_timeout = ' ~ mdp_literal(mdp_timeout(source_key))) }}
  {% endif %}
  {{ return('') }}
{% endmacro %}
