#!/usr/bin/env python3
"""Render the query inventory from reviewed column policies and optional live grants."""
import argparse
import json
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url-env", help="Warehouse URL variable for checking showcase_wh grants")
    args = parser.parse_args()
    labels = json.loads((ROOT / "functions/src/mdp_functions/relation_labels.json").read_text())
    catalog = json.loads((ROOT / "dbt/target/catalog.json").read_text())
    queries = json.loads((ROOT / "ops/showcase/queries.json").read_text())
    print("# Showcase query inventory\n")
    print("Read served marts through the data SDK. Direct SQL uses the explorer boundary below.")
    print("Every listed column is checked against the generated privacy policy. Grants also depend on the physical table having no undeclared columns.")
    print("Tenant stars mean the authenticated tenant slug, never user-supplied SQL. Use the house reader key for tenant SDK requests.\n")
    print("| Room | Relation | Columns | Explorer access | Local grant |\n|---|---|---|---|---|")
    conn = psycopg.connect(os.environ[args.url_env]) if args.url_env else None
    try:
        if conn:
            conn.execute("SET TRANSACTION READ ONLY")
        for query in queries:
            relation = query["relation"]
            if relation == "catalog.learning_rights":
                allowed = {"source_key", "learning_eligible", "resale_permitted"}
                if not set(query["columns"]).issubset(allowed):
                    raise ValueError("Rights exposes three columns. Read docs/analyst-access.md.")
                actual = "Not checked"
                if conn:
                    permitted = conn.execute(
                        "SELECT has_table_privilege('showcase_wh',%s,'SELECT')", (relation,)
                    ).fetchone()[0]
                    assert permitted, "Rights is unavailable. Run ops/local/init.sh on the local stack."
                    actual = "Allowed"
                columns = ", ".join(f"`{column}`" for column in query["columns"])
                print(f"| {query['room']} | `{relation}` | {columns} | Analyst and explorer SELECT | {actual} |")
                continue
            canonical_relation = relation.removeprefix("explore_")
            policy = labels[canonical_relation]["shared_privacy"]
            missing = [c for c in query["columns"] if policy.get(c) != "non_personal"]
            if missing:
                raise ValueError(f"Review the explorer copy for {relation}: {','.join(missing)}")
            model = labels[canonical_relation].get("model")
            columns = catalog["nodes"].get(model, {}).get("columns", {})
            canonical = bool(columns) and all(policy.get(c) == "non_personal" for c in columns)
            access = "Canonical SELECT" if canonical else "Use `explore_" + relation + "`; canonical SELECT denied"
            if relation.startswith("explore_"):
                access = "Explorer projection SELECT"
            actual = "Not checked"
            if conn and "*" not in relation:
                exists = conn.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0]
                actual = "Not built locally" if not exists else ("Allowed" if conn.execute("SELECT has_table_privilege('showcase_wh',%s,'SELECT')", (relation,)).fetchone()[0] else "Denied; use explorer copy")
                if exists and not canonical and not relation.startswith("explore_"):
                    projected = "explore_" + relation
                    assert conn.execute("SELECT has_table_privilege('showcase_wh',%s,'SELECT')", (projected,)).fetchone()[0], projected
                    actual += "; explorer copy allowed"
            print(f"| {query['room']} | `{relation}` | " + ", ".join(f"`{c}`" for c in query["columns"]) + f" | {access} | {actual} |")
    finally:
        if conn:
            conn.close()
    print("\nFor metadata and planned relations, read [the access notes](../../../docs/analyst-access.md#query-everything). Recheck with `uv run --project functions python ops/showcase/query-inventory.py --url-env MDP_WAREHOUSE_URL`.")


if __name__ == "__main__":
    main()
