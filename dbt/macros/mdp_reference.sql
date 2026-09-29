{# design: MusicBrainz reference rows from landed dump generations of the scoped spine. Every reader
   computes current state from its own cycle manifest: the newest generation whose mb_spine run
   completion is manifest-visible and reconciles, with the rows mb_resolve answers touched in it, plus
   tombstones for the keys of the previous reconciled generation that neither carries. The hourly chain
   materializes it once (int_reference__current); daily, weekly and tenant readers inline
   mdp_reference_rows(). #}

{% macro mdp_reference_tables() %}
  {# Each landed raw.mb_<table>: its content columns and types. The content hash excludes the channel,
     generation and sequence columns, so a reload of unchanged rows keeps every reference_version. #}
  {{ return({
    'url_link': {'entity_type': 'text', 'link_id': 'bigint', 'entity_id': 'bigint', 'url_id': 'bigint', 'url': 'text',
                 'link_type': 'text', 'ended': 'boolean', 'url_platform': 'text', 'url_kind': 'text', 'url_platform_id': 'text'},
    'isrc': {'isrc_id': 'bigint', 'isrc': 'text', 'recording_id': 'bigint'},
    'recording': {'recording_id': 'bigint', 'recording_gid': 'text', 'name': 'text', 'artist_credit_id': 'bigint',
                  'length_ms': 'bigint', 'video': 'boolean'},
    'track': {'track_id': 'bigint', 'track_gid': 'text', 'recording_id': 'bigint', 'medium_id': 'bigint', 'position': 'bigint',
              'number': 'text', 'name': 'text', 'artist_credit_id': 'bigint', 'length_ms': 'bigint'},
    'medium': {'medium_id': 'bigint', 'release_id': 'bigint', 'position': 'bigint', 'format_id': 'bigint', 'track_count': 'bigint'},
    'release': {'release_id': 'bigint', 'release_gid': 'text', 'name': 'text', 'artist_credit_id': 'bigint',
                'release_group_id': 'bigint', 'barcode': 'text', 'status_id': 'bigint'},
    'release_group': {'release_group_id': 'bigint', 'release_group_gid': 'text', 'name': 'text', 'artist_credit_id': 'bigint',
                      'type_id': 'bigint'},
    'artist_credit': {'artist_credit_id': 'bigint', 'name': 'text', 'artist_count': 'bigint'},
    'artist_credit_name': {'artist_credit_id': 'bigint', 'position': 'bigint', 'artist_id': 'bigint', 'name': 'text',
                           'join_phrase': 'text'},
    'artist': {'artist_id': 'bigint', 'artist_gid': 'text', 'name': 'text', 'sort_name': 'text', 'type_id': 'bigint'},
    'redirect': {'entity_type': 'text', 'gid': 'text', 'new_id': 'bigint'},
    'l_artist_label': {'link_id': 'bigint', 'artist_id': 'bigint', 'label_id': 'bigint', 'link_type': 'text',
                       'begin_year': 'bigint', 'begin_month': 'bigint', 'begin_day': 'bigint', 'end_year': 'bigint',
                       'end_month': 'bigint', 'end_day': 'bigint', 'ended': 'boolean'},
    'label': {'label_id': 'bigint', 'label_gid': 'text', 'name': 'text', 'type_id': 'bigint', 'label_code': 'bigint',
              'begin_year': 'bigint', 'end_year': 'bigint', 'ended': 'boolean'},
    'l_label_label': {'link_id': 'bigint', 'label_id0': 'bigint', 'label_id1': 'bigint', 'link_type': 'text',
                      'begin_year': 'bigint', 'begin_month': 'bigint', 'begin_day': 'bigint', 'end_year': 'bigint',
                      'end_month': 'bigint', 'end_day': 'bigint', 'ended': 'boolean'},
    'artist_ipi': {'artist_id': 'bigint', 'ipi': 'text'},
    'artist_isni': {'artist_id': 'bigint', 'isni': 'text'},
  }) }}
{% endmacro %}

{% macro mdp_reference_extension_tables() %}
  {# Catalog extension: landed with a generation only; no mb_resolve answer carries these rows. #}
  {{ return(['l_artist_label', 'label', 'l_label_label', 'artist_ipi', 'artist_isni']) }}
{% endmacro %}

{% macro mdp_reference_columns() %}
  {# The union of every table's content columns, for the one relation that carries all of them. #}
  {% set columns = {} %}
  {% for table, cols in mdp_reference_tables().items() %}
    {% for name, type in cols.items() %}{% do columns.setdefault(name, type) %}{% endfor %}
  {% endfor %}
  {{ return(columns) }}
{% endmacro %}

{% macro mdp_json_get(expr, path) -%}
  {#- Text at a JSON key path; keys may contain dots. -#}
  {%- if target.type == 'postgres' -%}
    (cast({{ expr }} as jsonb) #>> array[{% for key in path %}{{ mdp_literal(key) }}{% if not loop.last %}, {% endif %}{% endfor %}])
  {%- else -%}
    json_extract_string(cast({{ expr }} as json), '${% for key in path %}."{{ key }}"{% endfor %}')
  {%- endif -%}
{%- endmacro %}

{% macro mdp_completion_dumps(source_key) -%}
  {#- One row per dump a run completion lists: run, completion row, table, dump id, row count. -#}
  {%- if target.type == 'postgres' -%}
    select c.run_id, c._dump_id as completion_dump_id, c._landed_seq, c.recorded,
        t.key as target_table, d.key as dump_id, cast(d.value as bigint) as row_count
    from {{ source('raw', '_run_completion') }} c
    cross join lateral jsonb_each(cast(c.outputs as jsonb)) t
    cross join lateral jsonb_each_text(t.value) d
    where c.source_key = {{ mdp_literal(source_key) }}
  {%- else -%}
    select c.run_id, c._dump_id as completion_dump_id, c._landed_seq, c.recorded, t.target_table, d.dump_id,
        cast(json_extract_string(cast(c.outputs as json), '$."' || t.target_table || '"."' || d.dump_id || '"') as bigint) as row_count
    from {{ source('raw', '_run_completion') }} c,
        unnest(json_keys(cast(c.outputs as json))) as t(target_table),
        unnest(json_keys(json_extract(cast(c.outputs as json), '$."' || t.target_table || '"'))) as d(dump_id)
    where c.source_key = {{ mdp_literal(source_key) }}
  {%- endif -%}
{%- endmacro %}

{% macro mdp_reference_receipted() -%}
  {#- The mb_spine generations whose completion reconciles in this cycle's manifest: the run's newest
      visible completion row and every dump it lists are visible, and each table's listed rows equal the
      rows the run read from the mirror. Per-dump counts are landing receipts, so a visible dump holds
      its listed rows. generation_rank 1 is the newest. -#}
  {%- set ctx = mdp_context() -%}
  {%- set tables = ['raw.mb_generation'] -%}
  {%- for name in mdp_reference_tables() %}{% do tables.append('raw.mb_' ~ name) %}{% endfor -%}
  select generation, run_id, row_number() over (order by generation desc) as generation_rank
  from (
    select cast(c.run_id as text) as run_id, c._dump_id as completion_dump_id,
        {{ mdp_json_get('c.recorded', ['generation']) }} as generation, c.recorded,
        row_number() over (partition by c.run_id order by c._landed_seq desc, c._dump_id desc) as _rn
    from {{ source('raw', '_run_completion') }} c
    where c.source_key = 'mb_spine' and {{ ctx.manifest_filter('c._dump_id', 'raw._run_completion') }}
  ) c
  where c._rn = 1 and c.generation is not null
    and not exists (
      select 1 from (select l.target_table, cast(l.dump_id as uuid) as _dump_id from ({{ mdp_completion_dumps('mb_spine') }}) l
                     where l.completion_dump_id = c.completion_dump_id) r
      where not ({% for table in tables %}(r.target_table = '{{ table }}' and {{ mdp_spine_visible(table) }}){% if not loop.last %}
        or {% endif %}{% endfor %}))
    {% for table in tables %}
    and coalesce((select sum(l.row_count) from ({{ mdp_completion_dumps('mb_spine') }}) l
                  where l.completion_dump_id = c.completion_dump_id and l.target_table = '{{ table }}'), 0)
      = {% if table == 'raw.mb_generation' %}1{% else %}coalesce(cast({{ mdp_json_get('c.recorded', ['mirror_counts', table]) }} as bigint), 0){% endif %}
    {% endfor %}
{%- endmacro %}

{% macro mdp_reference_generations() -%}
  {#- The reconciled generations this cycle's manifest names (mdp_reference_receipted()), after the guard:
      a build whose newest two name a generation retention has deleted fails with
      reference_generation_incomplete rather than resolving on what remains. -#}
  {%- do mdp_reference_guard() -%}
  {{ mdp_reference_receipted() }}
{%- endmacro %}

{% macro mdp_reference_guard() -%}
  {#- Compile-time check: every generation the current state reads (the newest two receipted) still has its
      raw.mb_generation row. Retention deletes a generation's rows with that row, so a Replay of a cycle
      that read a pruned generation stops here until a repair re-lands its dumps. -#}
  {%- if execute -%}
    {%- set pruned -%}
      select r.generation from ({{ mdp_reference_receipted() }}) r
      where r.generation_rank <= 2 and not exists (
        select 1 from {{ source('raw', 'mb_generation') }} g
        where g.mb_generation = r.generation and cast(g._run_id as text) = r.run_id)
      order by 1
    {%- endset -%}
    {%- set rows = run_query(pruned).rows -%}
    {%- if rows | length -%}
      {{ exceptions.raise_compiler_error('reference_generation_incomplete: this cycle\'s manifest reads MusicBrainz generation '
          ~ rows[0][0] ~ ', whose raw.mb_* rows retention deleted; re-land its dumps before this build') }}
    {%- endif -%}
  {%- endif -%}
{%- endmacro %}

{% macro mdp_reference_rows(mb_table, generations=none) -%}
  {#- The current rows of one landed table for this cycle's manifest: the newest reconciled generation's
      rows (latest landing per key), then the rows mb_resolve answers touched in that generation and landed
      in their closure, then tombstones for the keys of the previous reconciled generation that neither
      carries, with their last content. Columns: mb_table, mb_key, the table's content columns,
      content_md5, tombstoned, row_generation, ref_generation, _run_id, _dump_id, _landed_seq.
      `generations` names a relation of mdp_reference_generations() rows already computed. -#}
  {%- set cols = mdp_reference_tables()[mb_table] -%}
  {%- set gens = generations or '(' ~ mdp_reference_generations() ~ ')' -%}
  select '{{ mb_table }}' as mb_table, mb_key, {% for c in cols %}{{ c }}, {% endfor %}
      {{ mdp_identity_hash(cols.keys() | list) }} as content_md5,
      generation_rank = 2 as tombstoned, mb_generation as row_generation,
      (select generation from {{ gens }} g1 where g1.generation_rank = 1) as ref_generation, _run_id, _dump_id, _landed_seq
  from (
    select l.*, row_number() over (partition by l.mb_key order by l.generation_rank, l._from_resolve, l._landed_seq desc, l._dump_id desc) as _rn
    from (
      select r.mb_key, {% for c in cols %}r.{{ c }}, {% endfor %}r.mb_generation, r._run_id, r._dump_id, r._landed_seq,
          g.generation_rank, 0 as _from_resolve
      from {{ source('raw', 'mb_' ~ mb_table) }} r
      join {{ gens }} g on g.generation = r.mb_generation and g.run_id = cast(r._run_id as text) and g.generation_rank <= 2
      where {{ mdp_spine_visible('raw.mb_' ~ mb_table) }}
      {%- if mb_table not in mdp_reference_extension_tables() %}
      union all
      select c.mb_key, {% for c in cols %}c.{{ c }}, {% endfor %}c.mb_generation, c._run_id, c._dump_id, c._landed_seq, 1, 1
      from ({{ mdp_resolve_closure(gens) }}) c
      where c.mb_table = '{{ mb_table }}'
      {%- endif %}
    ) l
  ) ranked
  -- A key the newest generation or its resolutions carry is live; one only the previous generation carried is a tombstone.
  where _rn = 1
{%- endmacro %}

{% macro mdp_resolve_closure(generations) -%}
  {#- The spine rows manifest-visible mb_resolve answers landed in raw.mb_resolve_closure (the raw.mb_*
      row shape with mb_table), for answers read from the newest reconciled generation. -#}
  {%- set ctx = mdp_context() -%}
  select r.*
  from {{ source('raw', 'mb_resolve_closure') }} r
  where {{ ctx.manifest_filter('r._dump_id', 'raw.mb_resolve_closure') }}
    and r.mb_generation = (select generation from {{ generations }} g where g.generation_rank = 1)
{%- endmacro %}

{% macro mdp_reference_current(generations=none, tables=none) -%}
  {#- Every landed table's current rows in one relation (or only `tables`), each table's columns in place
      and the rest null. -#}
  {%- set wide = mdp_reference_columns() -%}
  {%- set selected = [] -%}
  {%- for mb_table, cols in mdp_reference_tables().items() if tables is none or mb_table in tables %}{% do selected.append((mb_table, cols)) %}{% endfor -%}
  {%- for mb_table, cols in selected %}
  select mb_table, mb_key, {% for c, t in wide.items() %}{% if c in cols %}{{ c }}{% else %}cast(null as {{ t }}){% endif %} as {{ c }}, {% endfor %}
      content_md5, tombstoned, row_generation, ref_generation, _run_id, _dump_id, _landed_seq
  from ({{ mdp_reference_rows(mb_table, generations) }}) {{ mb_table }}_rows
  {% if not loop.last %}union all{% endif %}
  {%- endfor %}
{%- endmacro %}

{% macro mdp_spine_visible(table) -%}
  {#- The manifest filter of one landed spine table over `r._dump_id`, each call naming its raw table. -#}
  {%- if table == 'raw.mb_generation' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_generation') }}
  {%- elif table == 'raw.mb_url_link' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_url_link') }}
  {%- elif table == 'raw.mb_isrc' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_isrc') }}
  {%- elif table == 'raw.mb_recording' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_recording') }}
  {%- elif table == 'raw.mb_track' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_track') }}
  {%- elif table == 'raw.mb_medium' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_medium') }}
  {%- elif table == 'raw.mb_release' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_release') }}
  {%- elif table == 'raw.mb_release_group' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_release_group') }}
  {%- elif table == 'raw.mb_artist_credit' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_artist_credit') }}
  {%- elif table == 'raw.mb_artist_credit_name' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_artist_credit_name') }}
  {%- elif table == 'raw.mb_artist' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_artist') }}
  {%- elif table == 'raw.mb_redirect' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_redirect') }}
  {%- elif table == 'raw.mb_l_artist_label' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_l_artist_label') }}
  {%- elif table == 'raw.mb_label' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_label') }}
  {%- elif table == 'raw.mb_l_label_label' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_l_label_label') }}
  {%- elif table == 'raw.mb_artist_ipi' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_artist_ipi') }}
  {%- elif table == 'raw.mb_artist_isni' %}{{ mdp_context().manifest_filter('r._dump_id', 'raw.mb_artist_isni') }}
  {%- else %}{{ exceptions.raise_compiler_error('mdp_spine_visible: unknown spine table ' ~ table ~ '; choose a table from SPINE in mdp_functions/musicbrainz.py') }}
  {%- endif -%}
{%- endmacro %}

{% macro mdp_reference_hourly_tables() %}
  {#- The reference tables int_reference__current holds: those the hourly identity chain joins. -#}
  {{ return(['isrc', 'url_link', 'recording', 'redirect']) }}
{% endmacro %}

{% macro mdp_indexes(specs) -%}
  {#- Post-hook: the relation's indexes, each spec [label, columns, predicate or '']. A relation dbt
      rebuilt (the first build, a table, a full refresh) gets them under fresh names, since the backup
      it replaces still holds the old ones until the transaction ends; an existing one keeps its own. -#}
  {%- if target.type == 'postgres' -%}
  do $$
  declare
    specs text[][] := array[{% for label, columns, predicate in specs %}[{{ mdp_literal(label) }}, {{ mdp_literal(columns) }}, {{ mdp_literal(predicate) }}]{% if not loop.last %}, {% endif %}{% endfor %}];
    spec text[];
  begin
    foreach spec slice 1 in array specs loop
      if not exists (select 1 from pg_indexes where schemaname = '{{ this.schema }}' and tablename = '{{ this.identifier }}'
                     and indexname ~ ('^{{ this.identifier }}_' || spec[1] || '_[0-9a-f]{8}$')) then
        execute format('create index %I on {{ this }} (%s)', '{{ this.identifier }}' || '_' || spec[1] || '_'
          || substr(md5(random()::text), 1, 8), spec[2]) || coalesce(' where ' || nullif(spec[3], ''), '');
      end if;
    end loop;
  end $$
  {%- else -%}
  select 1
  {%- endif -%}
{%- endmacro %}

{% macro mdp_reference_indexes() -%}
  {#- Partial indexes for the keyed reads of int_reference__current, one per table and key, so no
      index carries the other tables' rows. -#}
  {{ return(mdp_indexes([
      ['isrc', 'isrc', "mb_table = 'isrc'"], ['isrc_rec', 'recording_id', "mb_table = 'isrc'"],
      ['url', 'url_platform, url_platform_id', "mb_table = 'url_link'"], ['rec_id', 'recording_id', "mb_table = 'recording'"],
      ['rec_gid', 'recording_gid', "mb_table = 'recording'"], ['redirect', 'gid', "mb_table = 'redirect'"]])) }}
{%- endmacro %}
