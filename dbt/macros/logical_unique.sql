{# Staging dedupes after the manifest filter; one row per logical key must remain. #}
{% test logical_unique(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
