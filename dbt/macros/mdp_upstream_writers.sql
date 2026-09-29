{# Conservative provenance floor: joins, filters, rankings and gaps cannot remove a reachable writer.
   Row-carried keys remain additive (including providers, imported seeds and unknown historical keys). #}
{% macro mdp_upstream_writers(node_id) %}
  {% if not execute %}{{ return([]) }}{% endif %}
  {% set walk = namespace(queue=[node_id], seen=[], keys=[]) %}
  {% for step in range(20000) if walk.queue %}
    {% set current = walk.queue.pop() %}
    {% if current not in walk.seen %}
      {% do walk.seen.append(current) %}
      {% if current in graph.sources %}
        {% set walk.keys = walk.keys + (graph.sources[current].meta.get('writers') or []) %}
      {% elif current in graph.nodes %}
        {% set walk.queue = walk.queue + (graph.nodes[current].get('depends_on', {}).get('nodes') or []) %}
      {% endif %}
    {% endif %}
  {% endfor %}
  {% if walk.queue %}{{ exceptions.raise_compiler_error('upstream writer walk did not finish: ' ~ node_id ~ '; inspect the model dependencies with dbt ls and reduce the dependency chain') }}{% endif %}
  {{ return(walk.keys | unique | sort | list) }}
{% endmacro %}
