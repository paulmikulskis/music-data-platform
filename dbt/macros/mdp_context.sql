{% macro mdp_literal(value) -%}
  {%- if value is none -%}null{%- else -%}'{{ (value | string).replace("'", "''") }}'{%- endif -%}
{%- endmacro %}

{% macro mdp_is_local() %}
  {% if target.name == 'workbench' and var('history_cycle', none) %}{{ return(false) }}{% endif %}
  {{ return(target.name in ('dev', 'ci', 'workbench') or flags.EMPTY | default(false) or var('dry_run', false)) }}
{% endmacro %}

{% macro mdp_cadence() %}
  {% set cadence = namespace(value=env_var('DBT_MDP_CADENCE', 'hourly')) %}
  {% for tag in model.config.tags | default([]) %}
    {% if tag.startswith('cadence:') %}{% set cadence.value = tag[8:] %}{% endif %}
  {% endfor %}
  {{ return(cadence.value) }}
{% endmacro %}

{% macro mdp_context(invoke=false) %}
  {% set context = {'cycle_id': var('cycle_id', 'local'), 'cadence': mdp_cadence(),
                    'scope': env_var('DBT_MDP_SCOPE', 'global'), 'manifest_mode': none,
                    'close_no': none, 'global_close_no': none, 'global_inputs': none,
                    'tenant_close_nos': {},
                    'timezone': var('timezone', 'UTC'), 'call_week': var('call_week', none),
                    'opened_at': var('cycle_opened_at', none),
                    'revision': mdp_revision, 'manifest_filter': mdp_manifest_filter} %}
  {% if target.name == 'workbench' and var('history_cycle', none) %}
    {% do context.update(var('history_cycle')) %}
    {{ return(context) }}
  {% endif %}
  {% if execute and not mdp_is_local() %}
    {% set run_id = env_var('DBT_CLOUD_RUN_ID', '') %}
    {# Read the catalog directly: dbt marks a schema cached while its relations are still being
       added, so a concurrent get_relation() can report a real cycle table as missing. #}
    {% set cycle_tables = run_query("select count(*) from information_schema.tables where lower(table_schema) = 'raw' and lower(table_name) in ('cycle_attempts', 'cycles')") %}
    {% if cycle_tables.rows[0][0] != 2 %}
      {{ exceptions.raise_compiler_error('no_cycle_binding: dbt run ' ~ run_id ~ '; run the full cadence with bash ops/run.sh <hourly|daily|weekly>, which binds the cycle automatically') }}
    {% endif %}
    {% set binding = run_query(mdp_cycle_query() ~ " join raw.cycle_attempts a on c.id=a.cycle_id where a.dbt_run_id=" ~ mdp_literal(run_id)) %}
    {% if binding.rows | length != 1 %}
      {{ exceptions.raise_compiler_error('no_cycle_binding: dbt run ' ~ run_id ~ '; run the full cadence with bash ops/run.sh <hourly|daily|weekly>, which binds the cycle automatically') }}
    {% endif %}
    {% do context.update(mdp_cycle_row(binding.rows[0])) %}
    {# Invokes (export, invoke and close stubs) never read the manifest in dbt, and phase-1 ones run
       before close; every other read of a stamp-mode cycle needs its close_no. #}
    {% if not invoke %}{% do mdp_require_closed(context) %}{% endif %}
  {% endif %}
  {{ return(context) }}
{% endmacro %}

{% macro mdp_require_closed(cycle) %}
  {# A stamp-mode read needs close_no; without it every filter would admit nothing. #}
  {% if cycle.manifest_mode == 'stamp' and cycle.close_no is none %}
    {{ exceptions.raise_compiler_error('cycle_not_closed: cycle ' ~ cycle.cycle_id ~ ' has no close_no; check bronze_close and the mirror catch-up') }}
  {% endif %}
{% endmacro %}

{% macro mdp_cycle_query() %}
  {# timezone: a tenant cycle's, frozen at bind (UTC otherwise); call_week: the Monday of the ISO week that
     ended before the cycle's local Monday in that timezone (the weekly call record, 21.3); opened_at: the
     cycle's clock, as naive UTC, which grading reads so a Replay grades as its cycle did. #}
  {{ return("select cast(c.id as text), c.cadence, c.scope, c.manifest_mode, c.close_no, c.global_close_no, cast(c.global_inputs as text), cast(c.tenant_close_nos as text), coalesce(c.timezone, 'UTC'), cast(" ~ mdp_local_week("timezone('UTC', c.opened_at)", "coalesce(c.timezone, 'UTC')", tz_sql=true) ~ " - 7 as text), cast(timezone('UTC', c.opened_at) as text) from raw.cycles c") }}
{% endmacro %}

{% macro mdp_cycle_row(row) %}
  {# global_inputs: the tenant cycle's list frozen at close; none for a row mirrored before it. #}
  {{ return({'cycle_id': row[0], 'cadence': row[1], 'scope': row[2], 'manifest_mode': row[3],
             'close_no': row[4], 'global_close_no': row[5],
             'global_inputs': fromjson(row[6]) if row[6] is not none else none,
             'tenant_close_nos': fromjson(row[7]) if (row | length) > 7 and row[7] is not none else {},
             'timezone': row[8] if (row | length) > 8 else 'UTC',
             'call_week': row[9] if (row | length) > 9 else none,
             'opened_at': row[10] if (row | length) > 10 else none}) }}
{% endmacro %}

{% macro mdp_manifest_sql(cycle, table, derived_rows=true) %}
  {# D6. list: the cycle's own rows. stamp: this table's stamps of the cycle's scope through
     close_no; for a declared global table in a tenant cycle, also its global stamps through
     global_close_no (one table may carry both scopes); plus the cycle's derived rows, unless the
     reader takes stamped rows only (rows landed before the cycle closed, which a Replay reads the same). #}
  {% set derived = 'select dump_id from raw.cycle_inputs where cycle_id = ' ~ mdp_literal(cycle.cycle_id) %}
  {% if cycle.manifest_mode != 'stamp' %}{{ return(derived) }}{% endif %}
  {% do mdp_require_closed(cycle) %}
  {% set scopes = ['(scope = ' ~ mdp_literal(cycle.scope) ~ ' and close_no <= ' ~ cycle.close_no ~ ')'] %}
  {% if cycle.scope != 'global' %}
    {# The global inputs frozen at close, so a Replay reads what the cycle built; the generated list
       only for a cycle mirrored before the list was. #}
    {% set declared = cycle.global_inputs if cycle.global_inputs is not none else mdp_global_inputs(cycle.cadence) %}
    {% if table in declared %}
      {% do scopes.append("(scope = 'global' and close_no <= " ~ (cycle.global_close_no if cycle.global_close_no is not none else -1) ~ ')') %}
    {% elif table in mdp_global_tables() %}
      {{ exceptions.raise_compiler_error('undeclared_global_input: ' ~ table ~ ' is not in mdp_global_inputs(' ~ cycle.cadence ~ '); rerun mdp sources export') }}
    {% endif %}
  {% endif %}
  {{ return('select dump_id from raw.dump_stamps where target_table = ' ~ mdp_literal(table)
    ~ ' and (' ~ scopes | join(' or ') ~ ')' ~ (' union all ' ~ derived if derived_rows else '')) }}
{% endmacro %}

{% macro mdp_manifest_filter(sql_column, table=none, derived_rows=true) %}
  {# Every target, parse included: a local build must fail the same call a stamp cycle would. #}
  {% if not table %}
    {{ exceptions.raise_compiler_error('manifest_table_required: pass the raw table to mdp_manifest_filter') }}
  {% endif %}
  {% if not execute %}{{ return('true') }}{% endif %}
  {# Preview reads served copies; Backtest sources are already frozen safe input views. #}
  {% if target.name == 'workbench' %}{{ return('true') }}{% endif %}
  {% if mdp_is_local() or not execute %}{{ return('true') }}{% endif %}
  {{ return(sql_column ~ ' in (' ~ mdp_manifest_sql(mdp_context(), table, derived_rows) ~ ')') }}
{% endmacro %}

{% macro mdp_revision_filter(sql_column) %}
  {# Backtest's target input view already applies the selected cycle's revision bound. #}
  {% if target.name == 'workbench' %}{{ return('true') }}{% endif %}
  {% if not execute or mdp_is_local() %}{{ return('true') }}{% endif %}
  {{ return(mdp_revision_sql(mdp_context(), sql_column)) }}
{% endmacro %}

{% macro mdp_revision_sql(cycle, sql_column) %}
  {# a revision counts when its own cycle's close_no is at most the bound close_no; a tenant
     cycle reads global revisions through global_close_no. List-mode cycles compare close times. #}
  {% if cycle.manifest_mode == 'stamp' %}
    {% do mdp_require_closed(cycle) %}
    {% set limits = ['(scope = ' ~ mdp_literal(cycle.scope) ~ ' and close_no <= ' ~ cycle.close_no ~ ')'] %}
    {% if cycle.scope != 'global' %}
      {% do limits.append("(scope = 'global' and close_no <= " ~ (cycle.global_close_no if cycle.global_close_no is not none else -1) ~ ')') %}
    {% endif %}
    {{ return(sql_column ~ ' in (select id from raw.cycles where ' ~ limits | join(' or ') ~ ')') }}
  {% endif %}
  {{ return(sql_column ~ ' in (select id from raw.cycles where scope = ' ~ mdp_literal(cycle.scope)
    ~ ' and closed_at <= (select closed_at from raw.cycles where id = ' ~ mdp_literal(cycle.cycle_id) ~ '))') }}
{% endmacro %}

{% macro mdp_revision(target_set_id) %}
  {# SQL expression; frozen revision lookup by cycle and target-set identity. #}
  {% set context = mdp_context() %}
  {{ return('(select distinct _revision_id from raw.targets where cast(target_set_id as text) = ' ~ mdp_literal(target_set_id) ~ ' and cast(_cycle_id as text) = ' ~ mdp_literal(context.cycle_id) ~ ')') }}
{% endmacro %}
