{# Refuse a selected tenant model before it can land behind the global analyst door. #}
{% macro mdp_explore_scope() %}
  {% if execute and target.type == 'postgres' and target.name != 'workbench'
        and 'scope:tenant' in model.config.tags and not this.schema.startswith('tenant_') %}
    {{ exceptions.raise_compiler_error('Tenant data needs a tenant schema. Set DBT_MDP_SCOPE=tenant:<id> and --vars "{tenant_slug: <slug>}".') }}
  {% endif %}
  {{ return('select 1') }}
{% endmacro %}
