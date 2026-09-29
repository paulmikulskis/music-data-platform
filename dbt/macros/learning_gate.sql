{# Filter a relation to rows whose source permits derivative learning. Default-deny. Tenant material is refused
   at compile time: a tenant schema, a scope:tenant model, and a raw table a tenant-bound key writes (its
   declared columns carry tenant_id). A fit reads the tenant-bound CC0 sources only through a global projection
   with no tenant key. #}
{% macro learning_gate(relation) %}
{%- if execute -%}
  {%- set gate = namespace(tenant=relation.schema is string and relation.schema.startswith('tenant_')) -%}
  {%- for node in graph.nodes.values() if node.resource_type == 'model' and node.alias == relation.identifier
        and 'scope:tenant' in node.tags -%}
    {%- set gate.tenant = true -%}
  {%- endfor -%}
  {%- for source in graph.sources.values() if source.identifier == relation.identifier
        and source.schema == relation.schema and 'tenant_id' in source.columns -%}
    {%- set gate.tenant = true -%}
  {%- endfor -%}
  {%- if gate.tenant -%}
    {{ exceptions.raise_compiler_error('learning_gate: ' ~ relation.identifier ~ ' is tenant material; read the global projection') }}
  {%- endif -%}
{%- endif -%}
select r.*
from {{ relation }} r
where coalesce(r.learning_eligible, false) = true
{% endmacro %}
