{# Unnamed index: PostgreSQL chooses a fresh name while dbt's previous table still exists. #}
{% macro mdp_search_index() -%}
{% if target.type == 'postgres' %}
create index on {{ this }} using gin (lower(display_text) gin_trgm_ops)
{% else %}select 1{% endif %}
{%- endmacro %}

{# Keep all credited source artist ids, with their aligned printed names when supplied. #}
{% macro mdp_search_artists() %}
select {{ mdp_song_platform('t.platform') }} as platform, t.platform_track_id,
{% if target.type == 'duckdb' %}
    a.value as artist_id, json_extract_string(t.artist_names, '$[' || cast(a.ordinality - 1 as text) || ']') as artist_name
from {{ ref('int_identity__track_inputs_daily') }} t
cross join unnest(cast(json_extract_string(t.platform_artist_ids, '$[*]') as varchar[])) with ordinality as a(value, ordinality)
{% else %}
    a.value as artist_id, cast(t.artist_names as jsonb) ->> cast(a.ordinality - 1 as integer) as artist_name
from {{ ref('int_identity__track_inputs_daily') }} t
cross join lateral jsonb_array_elements_text(cast(t.platform_artist_ids as jsonb)) with ordinality as a(value, ordinality)
{% endif %}
{% endmacro %}
