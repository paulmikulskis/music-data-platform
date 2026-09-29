{% macro mdp_identity_hash(columns) -%}
{% if target.type == 'postgres' %}
md5(jsonb_build_array({{ columns | join(', ') }})::text)
{% else %}
md5(cast(to_json(list_value({% for column in columns %}struct_pack(type := typeof({{ column }}), value := cast({{ column }} as varchar)){% if not loop.last %}, {% endif %}{% endfor %})) as varchar))
{% endif %}
{%- endmacro %}

{% macro mdp_input_identity(key_cols, version_cols) -%}
{{ mdp_identity_hash(key_cols) }} as input_ref,
{{ mdp_identity_hash(version_cols) }} as input_version
{%- endmacro %}
