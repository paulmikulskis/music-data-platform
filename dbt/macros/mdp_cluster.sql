{# Preserve the source row locator while adding provisional grouping evidence. #}
{% macro mdp_cluster_evidence(locator, alias) -%}
{% set details = mdp_json_object([
    ('cluster_key', alias ~ '.cluster_key'),
    ('member_song_keys', 'cast(' ~ alias ~ '.member_song_keys as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')'),
    ('cluster_methods', 'cast(' ~ alias ~ '.cluster_methods as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')'),
    ('cluster_confidence', alias ~ '.cluster_confidence')]) %}
{% if target.type == 'duckdb' %}json_merge_patch(cast({{ locator }} as json), {{ details }})
{% else %}(cast({{ locator }} as jsonb) || {{ details }}){% endif %}
{%- endmacro %}
