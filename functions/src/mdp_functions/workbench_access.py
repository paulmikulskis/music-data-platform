"""Current session permissions and already-built Workbench inputs."""

import json
import re

from psycopg import sql

from mdp_functions.analyst_errors import failure
from mdp_functions.errors import error_hint

STAFF_SCHEMAS = ("explore_marts", "explore_intermediate", "explore_staging", "catalog")


def grant_staff_inputs(conn, role):
    """Reconcile existing sessions too; never refresh the analyst's own grants here."""
    # Membership tracks revocations in PostgreSQL itself, including while dbt runs.
    # Remove the independent ACLs left by older sessions before inheriting analyst_ro.
    relations = conn.execute("""
        SELECT DISTINCT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) acl
        WHERE acl.grantee=(SELECT oid FROM pg_roles WHERE rolname=%s)
          AND c.relowner<>acl.grantee AND c.relkind IN ('r','v','m','p')
    """, (role,)).fetchall()
    for schema, table in relations:
        conn.execute(sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
            sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    conn.execute(sql.SQL("GRANT analyst_ro TO {} WITH INHERIT TRUE, SET FALSE").format(
        sql.Identifier(role)))


def check_staff_query(conn, query, scratch):
    """Check the current analyst ACL for every physical input, including CTE inputs.

    This keeps drafts on global inputs so they can become models. The session role, which inherits
    analyst_ro, is the privacy boundary: SQL functions such as table_to_xml can reach any relation
    analyst_ro reads (team-shared analyst sandboxes included), and nothing else.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope

    from mdp_functions.errors import ServiceError

    for scope in traverse_scope(sqlglot.parse_one(query, read="postgres")):
        for source in scope.sources.values():
            if not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier):
                continue
            schema = source.db
            if not schema:
                # Match the session search path, including already-built draft tables.
                schema = scratch if conn.execute(
                    "SELECT to_regclass(%s)", (sql.Identifier(scratch, source.name).as_string(conn),)
                ).fetchone()[0] else "explore_marts"
            if schema == scratch:
                continue
            if schema in {"raw", "explore_raw"}:
                raise ServiceError("raw_read_denied", failure("raw_read_denied")["message"], 403)
            if schema.startswith(("tenant_", "explore_tenant_")):
                raise ServiceError("tenant_read_denied", failure("tenant_read_denied")["message"], 403)
            relation = sql.Identifier(schema, source.name).as_string(conn)
            readable = schema in STAFF_SCHEMAS and conn.execute("""
                SELECT has_schema_privilege('analyst_ro',n.oid,'USAGE')
                  AND has_table_privilege('analyst_ro',c.oid,'SELECT')
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.oid=to_regclass(%s)
            """, (relation,)).fetchone()
            if readable is None:
                from mdp_functions.analyst_errors import (
                    ephemeral_next_step,
                    local_build_command,
                )

                step = ephemeral_next_step(source.name, schema)
                if step:
                    raise ServiceError(
                        "model_ephemeral", failure("model_ephemeral", next_step=step)["message"]
                    )
                raise ServiceError(
                    "model_not_built",
                    failure(
                        "model_not_built",
                        next_step=f"Run {local_build_command(source.name)}, then retry the query.",
                    )["message"],
                )
            if not readable or not readable[0]:
                raise ServiceError(
                    "workbench_permission_denied",
                    f"Staff cannot read {schema}.{source.name}. Choose a permitted global input in /explorer, or ask an operator to restore analyst access.",
                    403,
                )


# Only the selected draft is built. Resolve refs to stored, permitted inputs.
# An ephemeral ref offers a stored descendant instead of compiling raw-reading ancestors.
INPUT_REFS = """
{% macro workbench_stored_descendants(node_id, visited=[]) %}
  {% set choices = [] %}
  {% for candidate in graph.nodes.values() | sort(attribute='unique_id') %}
    {% if candidate.resource_type == 'model' and node_id in candidate.depends_on.nodes
          and candidate.unique_id not in visited and 'scope:tenant' not in candidate.tags %}
      {% if candidate.unique_id != model.unique_id and candidate.config.schema in ['marts', 'intermediate']
            and candidate.config.materialized in ['table', 'view', 'incremental'] %}
        {% do choices.append(candidate) %}
      {% elif candidate.config.materialized == 'ephemeral' %}
        {% do choices.extend(workbench_stored_descendants(candidate.unique_id, visited + [node_id])) %}
      {% endif %}
    {% endif %}
  {% endfor %}
  {{ return(choices) }}
{% endmacro %}
{% macro workbench_ephemeral_refusal(node) %}
  {% set choices = workbench_stored_descendants(node.unique_id) %}
  {% set selected = namespace(node=none) %}
  {% for candidate in choices %}
    {% if selected.node is none %}
      {% set relation = api.Relation.create(database=target.database, schema='explore_' ~ candidate.config.schema, identifier=candidate.alias) %}
      {% set readable = run_query("select has_schema_privilege(current_user,n.oid,'USAGE') and has_table_privilege(current_user,c.oid,'SELECT') from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.oid=to_regclass(" ~ mdp_literal(relation | string) ~ ")") %}
      {% if readable.rows | length and readable.rows[0][0] %}{% set selected.node = candidate %}{% endif %}
    {% endif %}
  {% endfor %}
  {% if selected.node is none and choices %}{% set selected.node = choices[0] %}{% endif %}
  {% if selected.node is not none %}
    {% set step = 'Use SELECT * FROM ' ~ selected.node.config.schema ~ '.' ~ selected.node.alias ~ ' in a draft, then choose Preview; if it is not built, open /explorer for its build command.' %}
  {% else %}
    {% set step = __EPHEMERAL_NEXT_STEP__ %}
  {% endif %}
  {{ exceptions.raise_compiler_error('model_ephemeral: ' ~ node.name ~ ': ' ~ __EPHEMERAL_SUMMARY__ ~ ' ' ~ step) }}
{% endmacro %}
{% macro workbench_input(relation) %}
  {% set allowed = __STAFF_SCHEMAS__ %}
  {% if __STAFF_ONLY__ and (relation.schema.startswith('tenant_') or relation.schema.startswith('explore_tenant_')) %}
    {{ exceptions.raise_compiler_error(__TENANT_REFUSAL__) }}
  {% endif %}
  {% set permitted_schema = relation.schema in allowed or (not __STAFF_ONLY__ and relation.schema.startswith('explore_tenant_')) %}
  {% if execute and permitted_schema %}
    {% set present = run_query("select to_regclass(" ~ mdp_literal(relation | string) ~ ")") %}
    {% if present.rows[0][0] is none %}
      {{ exceptions.raise_compiler_error('model_not_built: ' ~ relation.identifier ~ ' is not built here. Run source ops/local/env.sh && uv run --project dbt dbt build --target pg_local --vars "{dry_run: true}" --select +' ~ relation.identifier ~ ' --indirect-selection cautious; then retry Preview.') }}
    {% endif %}
  {% endif %}
  {% set readable = false %}
  {% if permitted_schema %}
    {% set check = run_query("select has_schema_privilege(current_user, n.oid, 'USAGE') and has_table_privilege(current_user, c.oid, 'SELECT') from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.oid=to_regclass(" ~ mdp_literal(relation | string) ~ ")") %}
    {% set readable = check.rows | length > 0 and check.rows[0][0] %}
  {% endif %}
  {% if not readable %}
    {% set inputs = run_query("select regexp_replace(n.nspname, '^explore_', '') || '.' || c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname in ('explore_marts','explore_intermediate','explore_staging') and has_schema_privilege(current_user,n.oid,'USAGE') and has_table_privilege(current_user,c.oid,'SELECT') order by (c.relname=" ~ mdp_literal(model.alias) ~ ") desc, n.nspname,c.relname limit 1") %}
    {% set step = 'Use ' ~ inputs.rows[0][0] ~ ' in a SELECT draft, or ask an operator to restore analyst access.' if inputs.rows | length else 'Ask an operator to restore analyst access, then choose an input in /explorer.' %}
    {{ exceptions.raise_compiler_error('workbench_permission_denied: This session cannot read ' ~ relation.schema ~ '.' ~ relation.identifier ~ '. ' ~ step) }}
  {% endif %}
  {{ return(relation) }}
{% endmacro %}
{% macro ref() %}
  {% if not execute %}{{ return(builtins.ref(*varargs, **kwargs)) }}{% endif %}
  {% set name = varargs[-1] %}
  {% set package = varargs[0] if varargs | length > 1 else project_name %}
  {% set matches = graph.nodes.values() | selectattr('package_name', 'equalto', package) | selectattr('name', 'equalto', name) | list %}
  {% if matches | length != 1 %}{{ exceptions.raise_compiler_error('Select a checked-in model from Workbench.') }}{% endif %}
  {% set node = matches[0] %}
  {% if 'scope:tenant' in node.tags and __STAFF_ONLY__ %}
    {{ exceptions.raise_compiler_error(__TENANT_REFUSAL__) }}
  {% endif %}
  {% if node.config.materialized == 'ephemeral' %}
    {{ workbench_ephemeral_refusal(node) }}
  {% endif %}
  {% set schema = node.config.schema or '' %}
  {% if 'scope:tenant' in node.tags %}
    {% if not var('tenant_slug', none) %}
      {{ exceptions.raise_compiler_error('workbench_permission_denied: This ref needs a tenant schema. Open /explorer and use the permitted relation name in a SELECT draft.') }}
    {% endif %}
    {% set schema = 'tenant_' ~ var('tenant_slug') ~ '_' ~ schema %}
  {% endif %}
  {% if schema == 'reference' and node.name == 'rights_registry' %}
    {% set relation = api.Relation.create(database=target.database, schema='catalog', identifier='learning_rights') %}
  {% else %}
    {% set relation = api.Relation.create(database=target.database, schema='explore_' ~ schema, identifier=node.alias) %}
  {% endif %}
  {{ return(workbench_input(relation)) }}
{% endmacro %}
{% macro source(source_name, table_name) %}
  {% set relation = builtins.source(source_name, table_name) %}
  {% if not execute %}{{ return(relation) }}{% endif %}
  {{ return(workbench_input(relation)) }}
{% endmacro %}
""".replace(
    "__TENANT_REFUSAL__",
    json.dumps("tenant_read_denied: " + failure("tenant_read_denied")["message"]),
).replace(
    "__EPHEMERAL_SUMMARY__", json.dumps(error_hint("model_ephemeral")["summary"]),
).replace(
    "__EPHEMERAL_NEXT_STEP__", json.dumps(error_hint("model_ephemeral")["next_step"]),
)


def input_refs(staff):
    """Route refs through the same safe copies as direct SQL for this session."""
    schemas = STAFF_SCHEMAS if staff else (*STAFF_SCHEMAS, "explore_raw", "explore_reference")
    return INPUT_REFS.replace("__STAFF_SCHEMAS__", json.dumps(schemas)).replace(
        "__STAFF_ONLY__", "true" if staff else "false"
    )


def staff_sandboxes(conn, owner):
    from mdp_functions.sandbox import status

    if not owner or not re.fullmatch(r"analyst_[a-z][a-z0-9_]{0,47}", owner):
        return []
    return [row for row in status(conn, "sandbox_" + owner.removeprefix("analyst_"))
            if row["owner_role"] == owner and not row["is_explorer"]]
