{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- set custom = custom_schema_name | trim if custom_schema_name else target.schema -%}
  {%- if target.name in ('dev', 'ci') -%}
    {%- if custom_schema_name is none -%}
      {{ target.schema }}
    {%- else -%}
      {{ target.schema }}_{{ custom }}
    {%- endif -%}
  {%- elif target.name == 'workbench' -%}
    {{ var('wb_schema') }}
  {%- else -%}
    {%- set scope = env_var('DBT_MDP_SCOPE', 'global') -%}
    {%- if scope == 'global' or (scope.startswith('tenant:') and 'scope:global' in node.config.tags) -%}
      {{ custom }}
    {%- elif scope.startswith('tenant:') -%}
      {%- set tenant_slug = var('tenant_slug', none) -%}
      {%- if not tenant_slug -%}
        {{ exceptions.raise_compiler_error("Missing required var 'tenant_slug' for tenant scope; pass --vars '{tenant_slug: <slug>}'") }}
      {%- endif -%}
      tenant_{{ tenant_slug }}_{{ custom }}
    {%- else -%}
      {{ exceptions.raise_compiler_error("Unsupported DBT_MDP_SCOPE: " ~ scope ~ "; use global or tenant:<id>") }}
    {%- endif -%}
  {%- endif -%}
{%- endmacro %}
