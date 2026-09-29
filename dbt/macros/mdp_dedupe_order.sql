{# The staging dedupe order of a function-written raw table: the latest _landed_seq wins, or the
   earliest when the declaring function sets dedupe='earliest' (a write-once record, such as the weekly
   calls of 21.3), so a later physical copy from a resumed older run never replaces the first. `mdp sources
   export` writes the order into the generated sources YAML (meta.dedupe); no staging model chooses its own. #}
{% macro mdp_dedupe_order(table) %}
  {%- set name = table.split('.')[-1] -%}
  {%- set found = namespace(order='latest') -%}
  {%- if execute -%}
    {%- for node in graph.sources.values() if node.source_name == 'raw' and node.name == name -%}
      {%- set found.order = node.meta.get('dedupe', 'latest') -%}
    {%- endfor -%}
  {%- endif -%}
  {%- if found.order not in ('latest', 'earliest') -%}
    {{ exceptions.raise_compiler_error('invalid meta.dedupe for raw.' ~ name ~ ': ' ~ found.order ~ '; use latest or earliest in the source dedupe declaration, then run mdp sources export') }}
  {%- endif -%}
  {{- '_landed_seq asc, _dump_id asc' if found.order == 'earliest' else '_landed_seq desc, _dump_id desc' -}}
{% endmacro %}
