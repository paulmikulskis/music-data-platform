{# Rights annotation for a served mart. Selects the mart's contract columns from `rows`
   and attaches learning_eligible, resale_permitted, and source_keys. Both flags AND the registry
   rows of every contributing source key (a key missing from the registry counts as false) with
   the row's own input flags. `source_keys` is a sorted JSON array text such as '["a","b"]'.
   The complete upstream writer set is a conservative floor, even on an unmatched join or gap.
   Annotation never filters: ineligible rows are served with their flags. #}
{% macro mdp_annotate(rows, source_keys='_source_keys', learning_inputs=[], resale_inputs=[]) %}
  {%- set rights = ['learning_eligible', 'resale_permitted', 'source_keys'] -%}
  {%- set columns = [] -%}
  {%- if execute -%}
    {%- for name in model.columns if name not in rights -%}{%- do columns.append(name) -%}{%- endfor -%}
    {%- for name in rights if name not in model.columns -%}
      {{ exceptions.raise_compiler_error('mdp_annotate: ' ~ model.name ~ ' must declare ' ~ name ~ ' in its contract') }}
    {%- endfor -%}
  {%- endif -%}
  {%- set writers = mdp_upstream_writers(model.unique_id) -%}
  {%- set keys = 'cast(coalesce(mdp_row.' ~ source_keys ~ ", '[]') as text)" -%}
  {#- tenant material never trains. A tenant-scoped relation is learn false whatever its sources; a fit
      reads the tenant-bound CC0 sources only through a global projection with no tenant key. -#}
  {%- set tenant = execute and 'scope:tenant' in (model.config.tags or []) -%}
select {% for name in columns %}mdp_row.{{ name }}, {% endfor %}
    cast({% if tenant %}false{% else %}coalesce(mdp_flags.known and mdp_flags.learning = 1, false){% for flag in learning_inputs %} and coalesce(mdp_row.{{ flag }}, false){% endfor %}{% endif %} as boolean) as learning_eligible,
    cast(coalesce(mdp_flags.known and mdp_flags.resale = 1, false){% for flag in resale_inputs %} and coalesce(mdp_row.{{ flag }}, false){% endfor %} as boolean) as resale_permitted,
    cast(mdp_flags.keys as text) as source_keys
from {{ rows }} mdp_row
left join (
    select mdp_keys.input_keys, mdp_keys.keys,
        count(mdp_registry.source_key) * 2 = length(mdp_keys.keys) - length(replace(mdp_keys.keys, '"', '')) as known,
        min(case when mdp_registry.learning_eligible then 1 else 0 end) as learning,
        min(case when mdp_registry.resale_permitted then 1 else 0 end) as resale
    from (
        select mdp_inputs.input_keys,
            {{ mdp_source_keys(arrays=['mdp_inputs.input_keys', 'mdp_lineage._mdp_lineage_writers']) }} as keys
        from (select distinct cast(coalesce({{ source_keys }}, '[]') as text) as input_keys from {{ rows }}) mdp_inputs
        cross join (select {{ mdp_literal(tojson(writers)) }} as _mdp_lineage_writers) mdp_lineage
    ) mdp_keys
    left join {{ ref('rights_registry') }} mdp_registry
        on position('"' || mdp_registry.source_key || '"' in mdp_keys.keys) > 0
    group by mdp_keys.input_keys, mdp_keys.keys
) mdp_flags on mdp_flags.input_keys = {{ keys }}
{% endmacro %}


{# Build stamp for a served mart or a model with meta.record_build: a post-hook inside the materialization transaction, so the
   row and the new contents commit together. Skipped on dev, ci and workbench targets and for models without a stamp declaration; any other swap, including an empty or dry-run build, stamps so open cursors go stale.
   On Postgres it also indexes the grain in cursor order, so a page never sorts the whole mart. #}
{% macro mdp_record_build() %}
  {%- set meta = model.config.get('meta') or {} -%}
  {%- if not execute or target.name in ('dev', 'ci', 'workbench') or not (meta.get('grain') or meta.get('record_build')) -%}{{ return('') }}{%- endif -%}
  {%- set cycle = mdp_context().cycle_id -%}
  {%- set cycles = adapter.get_relation(database=target.database, schema='raw', identifier='cycles') -%}
  {%- set stamped = cycles is not none and 'close_no' in (adapter.get_columns_in_relation(cycles) | map(attribute='name') | list) -%}
  {%- set close_no = '(select max(close_no) from raw.cycles where cast(id as text) = ' ~ mdp_literal(cycle) ~ ')' if stamped else 'cast(null as bigint)' -%}
  {%- set relation = this.schema ~ '.' ~ this.identifier -%}
  {%- if target.type == 'snowflake' -%}
merge into marts._build b
using (select {{ mdp_literal(relation) }} as relation, {{ mdp_literal(cycle) }} as cycle_id, {{ close_no }} as close_no, current_timestamp() as built_at) s
on b.relation = s.relation
when matched then update set cycle_id = s.cycle_id, close_no = s.close_no, built_at = s.built_at
when not matched then insert (relation, cycle_id, close_no, built_at) values (s.relation, s.cycle_id, s.close_no, s.built_at)
  {%- else -%}
    {%- set columns = [] -%}
    {%- for name in (meta.get('grain') or []) -%}
      {%- set type = (model.columns[name].data_type or '') | lower -%}
      {%- do columns.append(adapter.quote(name) ~ (' collate "C"' if type in ('text', 'varchar') else '')) -%}
    {%- endfor -%}
{% if columns %}create index if not exists {{ adapter.quote(this.identifier[:40] ~ '_grain_' ~ local_md5(invocation_id ~ this)[:10]) }} on {{ this }} ({{ columns | join(', ') }});{% endif %}
insert into marts._build (relation, cycle_id, close_no, built_at)
values ({{ mdp_literal(relation) }}, {{ mdp_literal(cycle) }}, {{ close_no }}, current_timestamp)
on conflict (relation) do update set cycle_id = excluded.cycle_id, close_no = excluded.close_no, built_at = excluded.built_at
  {%- endif -%}
{% endmacro %}


{# on-run-start: create the build table outside model transactions, so parallel post-hooks only upsert. #}
{% macro mdp_build_table() %}
  {%- if not execute or target.name in ('dev', 'ci', 'workbench') or flags.WHICH not in ('run', 'build') -%}{{ return('') }}{%- endif -%}
  {{ return('create schema if not exists marts; create table if not exists marts._build (relation text primary key, cycle_id text not null, close_no bigint, built_at timestamp with time zone not null)') }}
{% endmacro %}
