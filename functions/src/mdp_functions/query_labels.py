"""Conservative input labels and a shared, SQL-free query audit record."""

import hashlib
import logging
from datetime import datetime, timezone
from uuid import uuid4

import sqlglot
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlglot import exp
from sqlglot.lineage import to_node
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import build_scope, traverse_scope

from mdp_functions.relation_labels import CATEGORIES, UNKNOWN, combine, relation_label

# Unsupported SQL can contain private literals; never echo parser warnings.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


def inputs(query):
    """CTEs and aliases are not physical tables. Unresolved syntax stays visible."""
    found = set()
    unresolved = False
    try:
        for tree in sqlglot.parse(query, read="postgres"):
            if tree is None:
                continue
            scopes = traverse_scope(tree)
            if not scopes:
                unresolved = True
            for scope in scopes:
                for source in scope.sources.values():
                    if isinstance(source, exp.Table):
                        if not isinstance(source.this, exp.Identifier):
                            unresolved = True
                        else:
                            found.add((source.db, source.name))
            # User functions can hide reads that cannot be recovered from query text.
            if any(isinstance(f, exp.Anonymous) for f in tree.find_all(exp.Func)):
                unresolved = True
    except sqlglot.errors.SqlglotError:
        unresolved = True
    return sorted(found), unresolved


def describe(
    conn, query, search_path=("marts", "intermediate", "staging"), input_queries=None
):
    relations, unresolved = inputs(query)
    labels, tenants, visited = [], set(), set()
    tenant_names = {}
    unresolved_inputs = {"query expression"} if unresolved else set()
    column_schemas = {}
    relation_values = {}
    resolved_names = {}

    def missing(name):
        nonlocal unresolved
        unresolved = True
        unresolved_inputs.add(name)

    def visit(schema, name):
        nonlocal unresolved
        if (schema, name) in visited:
            return
        visited.add((schema, name))
        if not schema:
            matches = conn.execute(
                "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relname=%s AND n.nspname=ANY(%s) ORDER BY array_position(%s,n.nspname::text) LIMIT 1",
                (name, list(search_path), list(search_path)),
            ).fetchall()
            if not matches:
                labels.append(dict(UNKNOWN))
                missing(name)
                return
            resolved_names[name] = matches[0][0]
            schema = matches[0][0]
        if input_queries and (schema, name) in input_queries:
            upstream, unknown = inputs(input_queries[(schema, name)])
            if unknown:
                missing(f"{schema}.{name}")
            for parent_schema, parent_name in upstream:
                visit(parent_schema, parent_name)
            return
        label = relation_label(schema, name)
        row = conn.execute(
            "SELECT c.oid,obj_description(c.oid,'pg_class'),c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s",
            (schema, name),
        ).fetchone()
        if not row:
            missing(f"{schema}.{name}")
        labels.append(label)
        if label["tenant"] not in {"global", "tenant", "unknown"}:
            tenants.add(label["tenant"])
        if label["tenant"] == "unknown":
            missing(f"{schema}.{name}")
        if not row:
            return
        columns = conn.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid=%s AND attnum>0 AND NOT attisdropped",
            (row[0],),
        ).fetchall()
        column_schemas.setdefault(schema, {})[name] = dict.fromkeys(
            (c[0] for c in columns), "UNKNOWN"
        )
        relation_values[(schema, name)] = label
        # A declared dbt projection owns its scope; its raw writers can serve other scopes.
        managed_model = str(label.get("model", "")).startswith("model.")
        has_tenant = conn.execute(
            "SELECT 1 FROM pg_attribute WHERE attrelid=%s AND attname='tenant_id' AND NOT attisdropped",
            (row[0],),
        ).fetchone()
        if has_tenant and row[2] != "v":
            # Input scope, not a sample of output rows: COUNT(*) and LIMIT cannot hide a mix.
            input_tenants = [
                r[0]
                for r in conn.execute(
                    sql.SQL(
                        "SELECT DISTINCT tenant_id::text FROM {}.{} WHERE tenant_id IS NOT NULL LIMIT 101"
                    ).format(sql.Identifier(schema), sql.Identifier(name))
                )
            ]
            tenants.update(input_tenants)
            if len(input_tenants) == 1 and label["tenant"] not in {
                "global",
                "tenant",
                "unknown",
            }:
                tenant_names[input_tenants[0]] = label["tenant"]
            if len(tenants) > 100:
                missing(f"{schema}.{name}")
        if row[2] in ("v", "m") and not managed_model:
            definition = conn.execute(
                "SELECT pg_get_viewdef(%s, true)", (row[0],)
            ).fetchone()[0]
            view_inputs, unknown = inputs(definition)
            if unknown or (bool(has_tenant) and not view_inputs):
                missing(f"{schema}.{name}")
            for upstream_schema, upstream_name in conn.execute(
                "SELECT DISTINCT n.nspname,c.relname FROM pg_rewrite r JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid JOIN pg_class c ON c.oid=d.refobjid AND d.refclassid='pg_class'::regclass JOIN pg_namespace n ON n.oid=c.relnamespace WHERE r.ev_class=%s AND c.oid<>%s AND n.nspname NOT IN ('pg_catalog','mdp')",
                (row[0], row[0]),
            ):
                visit(upstream_schema, upstream_name)

    for schema, name in relations:
        visit(schema, name)
    tenants = {tenant_names.get(t, t) for t in tenants}
    for schema, name in visited:
        label = relation_values.get((schema, name), {})
        if label.get("tenant") == "tenant" and not tenants:
            missing(f"{schema}.{name}")
    result = combine(labels)
    # Rights and tenant scope use every input. Category follows selected column lineage.
    try:
        tree = sqlglot.parse_one(query, read="postgres")
        for query_scope in traverse_scope(tree):
            for source in query_scope.sources.values():
                if (
                    isinstance(source, exp.Table)
                    and not source.db
                    and source.name in resolved_names
                ):
                    source.set("db", exp.to_identifier(resolved_names[source.name]))
        tree = qualify(tree, dialect="postgres", schema=column_schemas)
        scope = build_scope(tree)
        if scope is None:
            raise sqlglot.errors.SqlglotError("Query has no selectable scope")
        categories = []
        # Output names can repeat. Resolve each expression by position so none is skipped.
        for index, _ in enumerate(tree.selects):
            for node in to_node(index, scope, "postgres").walk():
                if node.downstream:
                    continue
                if isinstance(node.expression, exp.Table):
                    table = node.expression
                    label = relation_values.get((table.db, table.name), UNKNOWN)
                    column = sqlglot.parse_one(node.name, into=exp.Column).name
                    policy = label.get(
                        "privacy"
                        if table.db.removeprefix("explore_") == "raw"
                        else "shared_privacy",
                        {},
                    )
                    mode = policy.get(column)
                    hidden = (
                        table.db.startswith("explore_")
                        and mode != "pseudonym"
                        and mode != "non_personal"
                    )
                    categories.append(
                        label.get("non_personal_category", label["category"])
                        if mode == "non_personal" or hidden
                        else label["category"]
                    )
                elif node.expression.find(exp.Column):
                    missing("selected columns")
        if categories and not unresolved:
            result["category"] = max(categories, key=CATEGORIES.index)
    except (sqlglot.errors.SqlglotError, ValueError, TypeError):
        if not unresolved:
            missing("selected columns")
    result.update(
        tenants=sorted(tenants),
        cross_tenant=len(tenants) > 1,
        unresolved=unresolved,
        unresolved_inputs=sorted(unresolved_inputs),
        scope_basis="input tenants observed during labeling; filters can narrow the actual result",
    )
    return result


def record(
    conn, actor, query, labels, channel="workbench", event_id=None, occurred_at=None
):
    conn.execute(
        "INSERT INTO catalog.query_audit(event_id,actor,occurred_at,channel,query_hash,tenants,labels,cross_tenant,unresolved) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (
            event_id or str(uuid4()),
            actor,
            occurred_at or datetime.now(timezone.utc),
            channel,
            hashlib.sha256(query.encode()).hexdigest(),
            Jsonb(labels["tenants"]),
            Jsonb(labels),
            labels["cross_tenant"],
            labels["unresolved"],
        ),
    )
