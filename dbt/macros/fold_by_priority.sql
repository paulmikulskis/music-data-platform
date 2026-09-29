{#
  Pick one value per (entity_id, field) across sources using seeds/source_priority.csv.
  This is the reconciliation rule that makes dbt the right tool here: source priority is declared
  in a seed and applied in SQL, not hidden in ingestion code.

  Usage: {{ fold_by_priority(ref('int_artist_candidates'), 'artist', 'name') }}
  Expects the input relation to expose: entity_id, source_key, value
#}
{% macro fold_by_priority(relation, entity, field) %}
with ranked as (
    select
        c.entity_id,
        c.value,
        c.source_key,
        p.rank,
        row_number() over (partition by c.entity_id order by p.rank asc nulls last, c.source_key asc, cast(c.value as text) asc) as rn
    from {{ relation }} c
    left join {{ ref('source_priority') }} p
        on p.source_key = c.source_key
       and p.entity = '{{ entity }}'
       and p.field = '{{ field }}'
)
select entity_id, value, source_key
from ranked
where rn = 1
{% endmacro %}
