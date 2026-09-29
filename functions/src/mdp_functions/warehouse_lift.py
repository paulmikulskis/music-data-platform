"""Turn a sandbox view into a reviewable dbt model and enforced contract."""

import argparse
import json
import re
import subprocess
from pathlib import Path

import psycopg
import sqlglot
import yaml
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from mdp_functions.sandbox_policy import POLICY
from mdp_functions.sandbox_policy import name as sandbox_name
from mdp_functions.warehouse_snapshot import analyst_connection


def rewrite(query, manifest):
    mapping = {}
    for node in manifest.get("nodes", {}).values():
        if node["resource_type"] not in {"model", "seed"}:
            continue
        schema = node["config"].get("schema") or (
            "reference" if node["resource_type"] == "seed" else node["schema"]
        )
        mapping[(schema, node.get("alias", node["name"]))] = (
            f"ref('{node['name']}')",
            node,
        )
    for node in manifest.get("sources", {}).values():
        mapping[(node["schema"], node.get("identifier", node["name"]))] = (
            f"source('{node['source_name']}', '{node['name']}')",
            node,
        )
    tree = sqlglot.parse_one(query, read="postgres")
    if not isinstance(tree, exp.Query):
        raise ValueError("The view must contain a SELECT")  # noqa: TRY004
    substitutions = {}
    cadences = set()
    for scope in traverse_scope(tree):
        for table in scope.sources.values():
            if not isinstance(table, exp.Table):
                continue
            key = (table.db, table.name)
            if table.db.startswith(POLICY["prefix"]):
                raise ValueError(
                    "Inline sandbox dependencies into this view before lifting it"
                )
            if key not in mapping:
                raise ValueError(
                    f"Cannot resolve {table.db}.{table.name}; use a declared staging or mart relation"
                )
            expression, node = mapping[key]
            tags = node.get("config", {}).get("tags", [])
            if "scope:tenant" in tags or table.db.startswith("tenant_"):
                raise ValueError(
                    "Tenant inputs need a tenant model; this command creates a global model"
                )
            cadences.update(t.split(":")[1] for t in tags if t.startswith("cadence:"))
            token = f"__mdp_lift_{len(substitutions)}__"
            substitutions[token] = "{{ " + expression + " }}"
            # Preserve the original relation name as its alias: pg_get_viewdef
            # qualifies selected columns with that name, even without an AS.
            if not table.alias:
                table.set("alias", exp.TableAlias(this=exp.to_identifier(table.name)))
            table.set("this", exp.to_identifier(token))
            table.set("db", None)
            table.set("catalog", None)
    rendered = tree.sql(dialect="postgres", pretty=True)
    for token, expression in substitutions.items():
        rendered = rendered.replace(token, expression)
    if re.search(r"\b" + re.escape(POLICY["prefix"]), rendered, re.IGNORECASE):
        raise ValueError("Remove sandbox functions and dependencies before lifting")
    return rendered, next(iter(cadences)) if len(cadences) == 1 else "daily"


def lift(conn, root, name, relation, manifest):
    if not re.fullmatch(r"mart_[a-z][a-z0-9_]*", name):
        raise ValueError(
            "Use a mart_ name with lowercase letters, digits and underscores"
        )
    schema, separator, view_name = relation.partition(".")
    if (
        not separator
        or not schema.startswith(POLICY["prefix"])
        or not re.fullmatch(POLICY["object_pattern"], view_name)
    ):
        raise ValueError(
            "Use sandbox_<handle>.<view>; list your objects with mdp warehouse sandbox status."
        )
    sandbox_name(schema.removeprefix(POLICY["prefix"]))
    folder = root / "dbt/models/marts/global"
    paths = [folder / f"{name}.{extension}" for extension in ("yml", "sql")]
    if any(path.exists() for path in paths):
        raise ValueError(
            f"Mart already exists at {paths[0]}. Open that model, or choose another mart name."
        )
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL search_path=pg_catalog")
        view = conn.execute(
            "SELECT oid,pg_get_viewdef(oid,true) AS sql FROM pg_class "
            "WHERE oid=to_regclass(%s) AND relkind='v' AND has_table_privilege(oid,'SELECT')",
            (relation,),
        ).fetchone()
        if not view:
            raise ValueError("Choose a readable sandbox view")
        columns = conn.execute(
            "SELECT attname AS name,format_type(atttypid,atttypmod) AS data_type FROM pg_attribute "
            "WHERE attrelid=%s AND attnum>0 AND NOT attisdropped ORDER BY attnum",
            (view["oid"],),
        ).fetchall()
    query, cadence = rewrite(view["sql"], manifest)
    if any(not re.fullmatch(r"[a-z_][a-z0-9_]*", c["name"]) for c in columns):
        raise ValueError("Give view columns lowercase SQL names before lifting")
    names = {c["name"] for c in columns}
    contract_columns = [
        dict(c)
        for c in columns
        if c["name"] not in {"_source_keys", "_cycle_id", "_built_by"}
    ]
    for column, kind in (
        ("learning_eligible", "boolean"),
        ("resale_permitted", "boolean"),
        ("source_keys", "text"),
        ("_cycle_id", "text"),
        ("_built_by", "text"),
    ):
        if column not in {c["name"] for c in contract_columns}:
            contract_columns.append({"name": column, "data_type": kind})
    for column in contract_columns:
        if column["data_type"].endswith("[]") or not re.match(
            r"^(text|character|character varying|varchar|bpchar|uuid|json|boolean|smallint|integer|bigint|numeric|decimal|real|double precision|timestamp|date)$|^(numeric|decimal|character varying|character)\(",
            column["data_type"]
            .replace(" without time zone", "")
            .replace(" with time zone", ""),
        ):
            raise ValueError(
                f"Cast {column['name']} to a portable text, JSON, number or date type before lifting"
            )
        column["description"] = "TODO: describe " + column["name"] + "."
        if column["data_type"].startswith(("character", "bpchar")):
            column["data_type"] = "text"
    selected = [
        f"input.{c['name']}"
        for c in columns
        if c["name"] not in {"_cycle_id", "_built_by"}
    ]
    if "_source_keys" not in names:
        keys = (
            "cast(input.source_keys as text)"
            if "source_keys" in names
            else "{{ mdp_source_keys(keys=['input.source_key']) }}"
            if "source_key" in names
            else "cast('[]' as text)"
        )
        selected.append(keys + " as _source_keys")
    for flag in ("learning_eligible", "resale_permitted"):
        if flag not in names:
            selected.append(f"cast(false as boolean) as {flag}")
    selected.extend(
        [
            "cast({{ mdp_literal(mdp_context().cycle_id) }} as text) as _cycle_id",
            "cast({{ mdp_literal(invocation_id) }} as text) as _built_by",
        ]
    )
    contract = {
        "version": 2,
        "models": [
            {
                "name": name,
                "description": "TODO: describe the result and choose its row key before serving it.",
                "config": {"contract": {"enforced": True}},
                "columns": contract_columns,
            }
        ],
    }
    model = "{{ config(materialized='table', tags=['cadence:" + cadence + "']) }}\n\n"
    model += (
        "-- Fill the descriptions and declare meta.grain before adding an API route.\n"
    )
    model += "-- Missing input rights stay false; upstream writers still join the source keys.\n"
    model += (
        f"with input as (\n{query.rstrip(';')}\n), annotated as (\n    select\n        "
    )
    model += ",\n        ".join(selected) + "\n    from input\n)\n"
    model += "{{ mdp_annotate('annotated', learning_inputs=['learning_eligible'], resale_inputs=['resale_permitted']) }}\n"
    # The enforced contract is always present before its SQL model.
    paths[0].write_text(yaml.safe_dump(contract, sort_keys=False))
    paths[1].write_text(model)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--from", dest="relation", required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    project = args.root / "dbt"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(project),
            "dbt",
            "parse",
            "--quiet",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project / "profiles"),
            "--target",
            "ci",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("dbt parse failed; run bash ops/ci/lint-dbt.sh for details")
    manifest = json.loads((project / "target/manifest.json").read_text())
    with analyst_connection() as conn:
        lift(conn, args.root, args.name, args.relation, manifest)
    for command in (
        ["uv", "run", "--project", "functions", "mdp", "sources", "export"],
        [
            "uv",
            "run",
            "--project",
            "dbt",
            "dbt",
            "parse",
            "--quiet",
            "--project-dir",
            "dbt",
            "--profiles-dir",
            "dbt/profiles",
            "--target",
            "ci",
        ],
        ["uv", "run", "--project", "functions", "python", "ops/label-catalog.py"],
        ["uv", "run", "--project", "functions", "python", "ops/analyst-doc.py"],
        [
            "uv",
            "run",
            "--project",
            "functions",
            "python",
            "ops/ci/generate_sandbox_policy.py",
        ],
        [
            "pnpm",
            "--dir",
            "control",
            "--filter",
            "@mdp/data-sdk",
            "generate",
            "--contract-only",
        ],
    ):
        generated = subprocess.run(
            command, cwd=args.root, capture_output=True, check=False
        )
        if generated.returncode:
            raise ValueError(
                "Generation failed. Run bash ops/ready.sh to find the stale catalog or SDK file, then run its named generator."
            )
    print(
        f"Created {args.name}. Fill the descriptions and choose the row key.\nNext: bash ops/ready.sh\nThen: gh pr create"
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, psycopg.Error, sqlglot.errors.SqlglotError) as error:
        raise SystemExit(
            str(error)
            if isinstance(error, ValueError)
            else f"Lift failed ({type(error).__name__}): {error}\nCheck the view with your individual login, run mdp warehouse sandbox status, then rerun the lift."
        ) from None
