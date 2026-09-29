"""Refuse a declared personal shared column unless every value path masks or omits it."""

import argparse
import json
import os
import subprocess
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage

ROOT = Path(__file__).resolve().parents[2]


def declarations(root=ROOT):
    from mdp_functions.shared_privacy import policies

    return {
        (model, column): mode
        for model, columns in policies(root).items()
        for column, mode in columns.items()
        if mode in {"pseudonym", "omitted"}
    }


def protected(expression):
    """Check value expressions, not null tests or branch conditions."""
    if isinstance(expression, exp.Alias):
        return protected(expression.this)
    if isinstance(expression, (exp.SHA2, exp.SHA, exp.MD5)):
        # mdp_pseudonym emits SHA256; older unkeyed MD5 is not a privacy boundary.
        return (
            isinstance(expression, exp.SHA2)
            and "mdp-local-pseudonym" in expression.sql()
        )
    if isinstance(expression, exp.Trim):
        return protected(expression.this)
    if isinstance(expression, exp.Case):
        values = [branch.args["true"] for branch in expression.args.get("ifs", [])]
        if expression.args.get("default") is not None:
            values.append(expression.args["default"])
        return all(protected(value) for value in values)
    if isinstance(expression, (exp.Column, exp.Literal)):
        return False
    children = list(expression.iter_expressions())
    return all(protected(child) for child in children)


def check(manifest, columns):
    models = {
        node["name"]: node
        for node in manifest["nodes"].values()
        if node.get("resource_type") == "model"
    }

    def safe(model, column, seen):
        if (model, column) in seen or model not in models:
            return False
        node = models[model]
        code = node.get("compiled_code")
        if not code:
            return False
        try:
            tree = lineage(column, code, dialect="duckdb")
        except (sqlglot.errors.SqlglotError, ValueError):
            return False

        def walk(branch):
            if isinstance(branch.expression, exp.Table):
                return safe(
                    branch.expression.name,
                    branch.name.rsplit(".", 1)[-1],
                    seen | {(model, column)},
                )
            if protected(branch.expression) and not isinstance(
                branch.expression, (exp.Select, exp.Star)
            ):
                return True
            return bool(branch.downstream) and all(
                walk(child) for child in branch.downstream
            )

        return walk(tree)

    return [
        f"{model}.{column}"
        for (model, column), mode in columns.items()
        if mode not in {"pseudonym", "omitted"} or not safe(model, column, set())
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("DBT_TARGET_PATH", ROOT / "dbt/target"))
        / "manifest.json",
    )
    parser.add_argument("--compile", action="store_true")
    parser.add_argument(
        "--database",
        action="store_true",
        help="Scan every staff-readable Postgres relation and column",
    )
    args = parser.parse_args()
    if args.database:
        import psycopg
        from mdp_functions.shared_privacy import scan_shared

        with psycopg.connect(os.environ["MDP_WAREHOUSE_ADMIN_URL"]) as conn:
            relations, columns = scan_shared(conn)
        print(
            f"PASS every shared relation: {relations} relations, {columns} column checks"
        )
        return
    columns = declarations()
    if args.compile:
        subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(ROOT / "dbt"),
                "dbt",
                "compile",
                "--project-dir",
                str(ROOT / "dbt"),
                "--profiles-dir",
                os.environ.get("DBT_PROFILES_DIR", str(ROOT / "dbt/profiles")),
                "--target",
                "ci",
                "--select",
                *sorted({model for model, _ in columns}),
            ],
            check=True,
        )
    failures = check(json.loads(args.manifest.read_text()), columns)
    if failures:
        raise SystemExit(
            "Personal columns need mdp_pseudonym or null: " + ", ".join(failures)
        )
    print(
        f"PASS {len(columns)} personal shared columns contain only pseudonyms or null"
    )


if __name__ == "__main__":
    main()
