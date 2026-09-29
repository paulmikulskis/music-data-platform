"""Reconstruct bounded Backtest inputs through the generated safe copies."""

import json
import re
from datetime import timedelta, timezone
from hashlib import sha256
from uuid import UUID

import psycopg
import sqlglot
from jinja2 import Environment, nodes
from psycopg import sql
from psycopg.rows import dict_row
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from mdp_functions.errors import ServiceError, error_hint

# These helpers compute from their arguments, the model graph or the selected cycle.
# Other helpers can read current state while compiling, before SQL can be inspected.
SAFE_MACROS = {
    "config",
    "ref",
    "source",
    "var",
    "range",
    "mdp_context",
    "mdp_literal",
    "mdp_is_local",
    "mdp_annotate",
    "mdp_input_identity",
    "mdp_revision_filter",
    "mdp_dedupe_order",
    "mdp_source_keys",
    "mdp_source_keys_agg",
}


def refuse():
    hint = error_hint("workbench_history_unavailable")
    return ServiceError(
        "workbench_history_unavailable", hint["summary"] + " " + hint["next_step"]
    )


def input_names(cycle_id, sources):
    """Keep compiled A SQL bound to A even after B or another comparison runs."""
    return {
        name: "history_"
        + UUID(cycle_id).hex
        + "_"
        + sha256(name.encode()).hexdigest()[:16]
        for name in sources
    }


def prepare(project, model):
    """Inline only a checked, reconstructible graph; never run upstream hooks."""
    paths = {path.stem: path for path in (project / "models").rglob("*.sql")}
    visited, sources = set(), set()
    environment = Environment(extensions=["jinja2.ext.do"])

    def visit(name):
        if name == "rights_registry":
            return
        if name in visited:
            return
        if name not in paths:
            raise refuse()
        visited.add(name)
        path = paths[name]
        code = re.sub(r"(?m)^\s*-- depends_on:.*$", "", path.read_text())
        tree = environment.parse(code)
        for attribute in tree.find_all(nodes.Getattr):
            if (
                isinstance(attribute.node, nodes.Call)
                and isinstance(attribute.node.node, nodes.Name)
                and attribute.node.node.name == "mdp_context"
                and attribute.attr
                not in {
                    "cycle_id",
                    "cadence",
                    "scope",
                    "opened_at",
                    "call_week",
                    "timezone",
                    "close_no",
                    "manifest_filter",
                }
            ):
                raise refuse()
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node, nodes.Name):
                macro = call.node.name
                if macro not in SAFE_MACROS:
                    raise refuse()
                if macro in {"ref", "source"}:
                    if not all(isinstance(arg, nodes.Const) for arg in call.args):
                        raise refuse()
                    args = [arg.value for arg in call.args]
                    if macro == "ref" and len(args) == 1:
                        visit(args[0])
                    elif macro == "source" and len(args) == 2 and args[0] == "raw":
                        sources.add(args[1])
                    else:
                        raise refuse()
            elif not (
                isinstance(call.node, nodes.Getattr)
                and call.node.attr == "manifest_filter"
                and isinstance(call.node.node, nodes.Call)
                and isinstance(call.node.node.node, nodes.Name)
                and call.node.node.node.name == "mdp_context"
                and len(call.args) == 2
                and not call.kwargs
            ):
                raise refuse()
        # Compile ancestors as CTEs. Compile executes neither their hooks nor invoke stubs.
        if name != model:
            code += "\n{{ config(materialized='ephemeral', contract={'enforced': false}) }}\n"
        path.write_text(code)

    visit(model)
    return sorted(visited), sorted(sources)


def inputs(url, schema, cycle_id, sources, timeout_s=30):
    """Copy and reconcile frozen inputs as the restricted session role."""
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET LOCAL statement_timeout={}").format(
                sql.Literal(timeout_s * 1000)
            )
        )
        try:
            cycle = conn.execute(
                "SELECT * FROM explore_raw.cycles WHERE id=%s", (cycle_id,)
            ).fetchone()
            if (
                not cycle
                or cycle["status"] != "closed"
                or cycle["scope"] != "global"
                or cycle["manifest_mode"] not in {"list", "stamp"}
                or not cycle["closed_at"]
                or (cycle["manifest_mode"] == "stamp" and cycle["close_no"] is None)
            ):
                raise refuse()
            for name, view in sources.items():
                relation = sql.Identifier("explore_raw", name)
                expected_dumps = None
                columns = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='explore_raw' AND table_name=%s",
                    (name,),
                ).fetchall()
                if name == "targets":
                    if cycle["manifest_mode"] == "stamp":
                        predicate = sql.SQL(
                            "_cycle_id IN (SELECT id FROM explore_raw.cycles "
                            "WHERE scope='global' AND close_no <= {})"
                        ).format(sql.Literal(cycle["close_no"]))
                    else:
                        predicate = sql.SQL(
                            "_cycle_id IN (SELECT id FROM explore_raw.cycles "
                            "WHERE scope='global' AND closed_at <= {})"
                        ).format(sql.Literal(cycle["closed_at"]))
                    # The raw copy hides target values. The readable history preserves the
                    # permitted values for every revision it contains; prove it is complete.
                    membership = sql.SQL(
                        "SELECT _cycle_id,_revision_id,count(*) FROM {} WHERE {} "
                        "GROUP BY _cycle_id,_revision_id ORDER BY _cycle_id,_revision_id"
                    )
                    expected = conn.execute(
                        membership.format(relation, predicate)
                    ).fetchall()
                    if expected:
                        relation = sql.Identifier(
                            "explore_staging", "stg_control__target_history"
                        )
                        if (
                            conn.execute(
                                membership.format(relation, predicate)
                            ).fetchall()
                            != expected
                        ):
                            raise refuse()
                elif any(column["column_name"] == "_dump_id" for column in columns):
                    membership = sql.SQL(
                        "SELECT dump_id FROM explore_raw.cycle_inputs "
                        "WHERE cycle_id={} UNION "
                        "SELECT dump_id FROM explore_raw.dump_stamps "
                        "WHERE {}='stamp' AND scope='global' AND close_no <= {} "
                        "AND target_table={}"
                    ).format(
                        sql.Literal(cycle_id),
                        sql.Literal(cycle["manifest_mode"]),
                        sql.Literal(cycle["close_no"]),
                        sql.Literal("raw." + name),
                    )
                    receipts = conn.execute(
                        sql.SQL(
                            "SELECT member.dump_id,receipt.target_table,receipt.rows,"
                            "receipt.rows_deduped,receipt.committed_at FROM ({}) member "
                            "LEFT JOIN explore_raw._load_receipts receipt USING (dump_id)"
                        ).format(membership)
                    ).fetchall()
                    expected_dumps = {}
                    for receipt in receipts:
                        # A missing receipt cannot prove which table owns the dump.
                        # Deduplicated rows cannot reconstruct the original dump either.
                        if (
                            receipt["target_table"] is None
                            or receipt["committed_at"] is None
                            or receipt["rows"] is None
                            or receipt["rows"] < 0
                            or receipt["rows_deduped"] != 0
                        ):
                            raise refuse()
                        if receipt["target_table"] == "raw." + name:
                            if receipt["dump_id"] in expected_dumps:
                                raise refuse()
                            expected_dumps[receipt["dump_id"]] = receipt["rows"]
                    predicate = sql.SQL("_dump_id = ANY({}::uuid[])").format(
                        sql.Literal(list(expected_dumps))
                    )
                else:
                    raise refuse()
                # Copy before checking counts. Retention or replay after this transaction
                # cannot change the inputs used by the later compile and materialization.
                existing = conn.execute(
                    "SELECT relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relname=%s",
                    (schema, view),
                ).fetchone()
                if existing:
                    kind = {"r": "TABLE", "v": "VIEW"}.get(existing["relkind"])
                    if kind is None:
                        raise refuse()
                    conn.execute(
                        sql.SQL("DROP {} {}").format(
                            sql.SQL(kind), sql.Identifier(schema, view)
                        )
                    )
                conn.execute(
                    sql.SQL("CREATE TABLE {} AS SELECT * FROM {} WHERE {}").format(
                        sql.Identifier(schema, view),
                        relation,
                        predicate,
                    )
                )
                if expected_dumps is not None:
                    retained = {
                        row["_dump_id"]: row["rows"]
                        for row in conn.execute(
                            sql.SQL(
                                "SELECT _dump_id,count(*) AS rows FROM {} GROUP BY _dump_id"
                            ).format(sql.Identifier(schema, view))
                        )
                    }
                    if any(
                        retained.get(dump, 0) != count
                        for dump, count in expected_dumps.items()
                    ):
                        raise refuse()
        except psycopg.Error as exc:
            raise refuse() from exc
    opened = cycle["opened_at"].astimezone(timezone.utc)
    return {
        "cycle_id": str(cycle["id"]),
        "cadence": cycle["cadence"],
        "scope": cycle["scope"],
        "manifest_mode": cycle["manifest_mode"],
        "close_no": cycle["close_no"],
        "opened_at": opened.replace(tzinfo=None).isoformat(),
        "timezone": "UTC",
        "call_week": (opened.date() - timedelta(days=opened.weekday() + 7)).isoformat(),
    }


def materialize(url, schema, alias, query, sources, timeout_s=30):
    """Refuse any surviving read of current tables before executing the draft."""
    allowed = {(schema, view) for view in sources.values()}
    allowed.add(("catalog", "learning_rights"))
    try:
        tree = sqlglot.parse_one(query, read="postgres")
        # Opaque SQL routines can hide reads such as table_to_xml('current_table').
        # The JSON helpers below only reshape values already present in the query.
        for function in tree.find_all(exp.Anonymous):
            if function.name.lower() not in {
                "jsonb_array_elements_text",
                "jsonb_build_array",
                "jsonb_typeof",
            }:
                raise refuse()
        if any(isinstance(dot.expression, exp.Func) for dot in tree.find_all(exp.Dot)):
            raise refuse()
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if (
                    isinstance(source, exp.Table)
                    and (source.db, source.name) not in allowed
                ):
                    raise refuse()
    except sqlglot.errors.SqlglotError as exc:
        raise refuse() from exc
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET LOCAL statement_timeout={}").format(
                sql.Literal(timeout_s * 1000)
            )
        )
        relation = sql.Identifier(schema, alias)
        existing = conn.execute(
            "SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relname=%s",
            (schema, alias),
        ).fetchone()
        if existing:
            kind = {"r": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW"}.get(
                existing[0]
            )
            if kind is None:
                raise refuse()
            conn.execute(sql.SQL("DROP {} {}").format(sql.SQL(kind), relation))
        conn.execute(sql.SQL("CREATE TABLE {} AS ").format(relation) + sql.SQL(query))


def macros():
    message = json.dumps("workbench_history_unavailable: " + str(refuse()))
    return """
{% macro ref() %}
  {% if not execute %}{{ return(builtins.ref(*varargs, **kwargs)) }}{% endif %}
  {% set name = varargs[-1] %}
  {% if name == 'rights_registry' %}
    {{ return(workbench_input(api.Relation.create(database=target.database, schema='catalog', identifier='learning_rights'))) }}
  {% endif %}
  {% if name not in var('history_models') %}{{ exceptions.raise_compiler_error(__REFUSAL__) }}{% endif %}
  {{ return(builtins.ref(*varargs, **kwargs)) }}
{% endmacro %}
{% macro source(source_name, table_name) %}
  {% if not execute %}{{ return(builtins.source(source_name, table_name)) }}{% endif %}
  {% if source_name != 'raw' or table_name not in var('history_sources') %}
    {{ exceptions.raise_compiler_error(__REFUSAL__) }}
  {% endif %}
  {{ return(api.Relation.create(database=target.database, schema=var('wb_schema'), identifier=var('history_sources')[table_name])) }}
{% endmacro %}
""".replace("__REFUSAL__", message)
