{# Explicit declared source columns prevent SELECT * from admitting new payload fields.
   Personal output rules come from the same model metadata as the label/grant policy. #}
{% macro mdp_source_projection(source_name, table_name) %}
  {% set ns = namespace(columns=[]) %}
  {% if execute %}
    {% for node in graph.sources.values() if node.source_name == source_name and node.name == table_name %}
      {% for name, column in node.columns.items() %}
        {% set output = model.columns.get(name, {}) %}
        {% set meta = output.get('meta', {}) %}
        {% if meta.get('privacy') == 'personal' and meta.get('representation') == 'omitted' %}
          {% set type = column.data_type or 'text' %}
          {% if type in ['json', 'jsonb'] %}{% set type = 'jsonb' if target.type == 'postgres' else 'json' %}{% endif %}
          {% do ns.columns.append('cast(null as ' ~ type ~ ') as ' ~ adapter.quote(name)) %}
        {% else %}
          {% do ns.columns.append(adapter.quote(name)) %}
        {% endif %}
      {% endfor %}
    {% endfor %}
    {% if not ns.columns %}{{ exceptions.raise_compiler_error('Source projection needs declared columns; add columns to the source declaration and run mdp sources export') }}{% endif %}
  {% endif %}
  {{ return(ns.columns | join(', ') if execute else '*') }}
{% endmacro %}
