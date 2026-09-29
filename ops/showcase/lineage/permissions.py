"""Execute reviewed projections using the actual product and Workbench roles."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import psycopg
from generate import ROOT, digest, read, require
from psycopg import sql


def projection_sql(relation, projection, *, check_fields=False):
    # All identifier inputs have already passed the generator review.
    columns = (
        sorted(set(projection["columns"] + projection["order"] + projection["filters"]))
        if check_fields
        else projection["columns"]
    )
    query = sql.SQL("SELECT {} FROM {} a {} ORDER BY {} LIMIT 5").format(
        sql.SQL(", ").join(sql.Identifier("a", c) for c in columns),
        sql.Identifier(*relation.split(".")),
        sql.SQL(""),
        sql.SQL(", ").join(
            sql.SQL("{} DESC").format(sql.Identifier("a", c))
            for c in projection["order"]
        ),
    )
    return query


def validate(lineage, urls):
    results = []
    for role, url in urls.items():
        with psycopg.connect(url) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '5s'")
            conn.execute("SET LOCAL lock_timeout = '1s'")
            require(
                conn.execute("SELECT current_user").fetchone()[0] == role,
                "Wrong projection test role",
            )
            for relation, projection in lineage["peeks"].items():
                if role == "workbench_wh" and not projection["workbench"]:
                    continue
                conn.execute(
                    projection_sql(relation, projection, check_fields=True)
                ).fetchall()
                results.append({"relation": relation, "role": role, "readable": True})
    return {
        "schema_version": 1,
        "lineage_hash": digest(lineage),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checks": sorted(results, key=lambda r: (r["role"], r["relation"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = validate(
        read(ROOT / "control/apps/showcase/lib/lineage.generated.json"),
        {
            "showcase_wh": os.environ["MDP_SHOWCASE_QUERY_TEST_URL"],
            "workbench_wh": os.environ["MDP_WORKBENCH_EXAMPLE_TEST_URL"],
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print("Reviewed projections pass as both roles. Open ops/showcase/README.md.")


if __name__ == "__main__":
    main()
