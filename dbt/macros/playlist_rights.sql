{# Rights columns for a playlist mart row joined to int_playlist__rights on its snapshot. #}
{% macro playlist_rights(rights) %}
    coalesce({{ rights }}.learning_eligible, false) as learning_eligible,
    coalesce({{ rights }}.resale_permitted, false) as resale_permitted,
    coalesce({{ rights }}.source_keys, '[]') as source_keys
{% endmacro %}
