{% macro mdp_scope_guard() %}
  {# Hooks compile after selection, unlike schema naming (which parses every model). #}
  {% if execute and flags.WHICH in ('run', 'build') %}
    {% set scope = env_var('DBT_MDP_SCOPE', 'global') %}
    {% for id in selected_resources %}
      {% set node = graph.nodes.get(id) %}
      {% if node and node.resource_type == 'model' and 'scope:tenant' in node.config.tags %}
        {% if not scope.startswith('tenant:') or not scope[7:] | trim %}
          {{ exceptions.raise_compiler_error(
            'tenant_scope_required: ' ~ node.name ~ ' contains tenant data. Run '
            ~ 'DBT_MDP_SCOPE=tenant:<tenant-id> uv run --project dbt dbt build '
            ~ '--target ' ~ target.name ~ ' --select ' ~ node.name
            ~ " --vars '{tenant_slug: <slug>}' (add dry_run: true for local fixtures). "
            ~ 'For global data, select tag:scope:global.') }}
        {% endif %}
      {% endif %}
    {% endfor %}
  {% endif %}
  {{ return('') }}
{% endmacro %}
