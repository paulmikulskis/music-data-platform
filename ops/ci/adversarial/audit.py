"""Read-only, per-table/per-dump key, receipt and row-count reconciliation."""

import os
from collections import defaultdict

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def audit(control_url=None, warehouse_url=None):
    from mdp_functions.registry import discover

    catalog = discover()
    keys = {
        table: manifest.key
        for manifest in catalog.values()
        for table in manifest.writes
    }
    # Runtime-written evidence tables carry their own identities.
    keys["raw._enrichment_completion"] = ["_source_key", "scope", "input_ref", "input_version", "step", "config_version"]
    keys["raw._run_completion"] = ["run_id"]
    silver_keys = {
        table
        for manifest in catalog.values()
        if manifest.layer == "silver" and manifest.key
        for table in manifest.writes
    }
    errors = []
    control_url = control_url or os.environ["MDP_CONTROL_URL"]
    warehouse_url = warehouse_url or os.environ["MDP_WAREHOUSE_URL"]
    with (
        psycopg.connect(control_url, row_factory=dict_row) as control,
        psycopg.connect(warehouse_url, row_factory=dict_row) as warehouse,
    ):
        control.execute("SET TRANSACTION READ ONLY")
        warehouse.execute("SET TRANSACTION READ ONLY")
        loads = control.execute(
            "SELECT d.id AS dump_id,d.run_id,d.kind,l.target_table,l.status,l.generation,l.warehouse_id "
            "FROM control.dump d LEFT JOIN control.load l ON l.dump_id=d.id WHERE d.kind IN ('output','rejected') ORDER BY l.target_table,d.id"
        ).fetchall()
        receipts = warehouse.execute(
            "SELECT dump_id,warehouse_id,target_table,generation,rows,committed_at FROM raw._load_receipts"
        ).fetchall()
        indexed = defaultdict(list)
        for receipt in receipts:
            indexed[
                (receipt["dump_id"], receipt["warehouse_id"], receipt["target_table"])
            ].append(receipt)
        tables = {row["target_table"] for row in loads if row["target_table"]}
        for table in sorted(tables):
            columns = [
                r["column_name"]
                for r in warehouse.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                    table.split(".", 1),
                )
            ]
            if not columns:
                if any(
                    r["target_table"] == table and r["status"] == "loaded"
                    for r in loads
                ):
                    errors.append(table + ": loaded table missing")
                continue
            key = keys.get(table)
            if not key:
                # Runtime unit fixtures may deliberately omit business keys. The live
                # acceptance audit cannot invent a declaration for production outputs.
                if (
                    control_url == os.environ.get("MDP_CONTROL_URL")
                    and table != "raw._rejected"
                ):
                    errors.append(
                        table
                        + ": no declared key; distinct-key acceptance cannot be certified"
                    )
                key = [c for c in columns if not c.startswith("_")]
            # Physical uniqueness is per dump for every layer; replays may keep a second copy.
            scoped = list(dict.fromkeys(["_dump_id", *key]))
            if not key or any(c not in columns for c in scoped):
                errors.append(table + ": missing declared key columns")
                continue
            relation = sql.Identifier(*table.split("."))
            if table in silver_keys:
                missing = warehouse.execute(
                    sql.SQL("SELECT count(*) AS n FROM {} WHERE {}").format(
                        relation,
                        sql.SQL(" OR ").join(
                            sql.SQL("{} IS NULL").format(sql.Identifier(c))
                            for c in scoped
                        ),
                    )
                ).fetchone()["n"]
                if missing:
                    errors.append(table + ": null declared identity values")
            fields = sql.SQL(",").join(sql.Identifier(c) for c in scoped)
            query = sql.SQL(
                "SELECT count(*) AS rows,count(DISTINCT ROW({})) AS distinct_keys FROM {}"
            ).format(fields, relation)
            counts = warehouse.execute(query).fetchone()
            print("SQL " + query.as_string(warehouse))
            print(
                f"AUDIT table={table} key={','.join(scoped)} rows={counts['rows']} distinct={counts['distinct_keys']}"
            )
            if counts["rows"] != counts["distinct_keys"]:
                errors.append(
                    table
                    + (
                        ": duplicate declared keys"
                        if keys.get(table)
                        else ": duplicate full-record diagnostic (no declared key)"
                    )
                )
            counts_by_dump = {
                str(r["dump_id"]): r["rows"]
                for r in warehouse.execute(
                    sql.SQL(
                        "SELECT _dump_id AS dump_id,count(*) AS rows FROM {} GROUP BY _dump_id"
                    ).format(relation)
                )
            }
            known = {str(r["dump_id"]) for r in loads if r["target_table"] == table}
            if set(counts_by_dump) - known:
                errors.append(table + ": rows without registered dump/load")
            for row in [r for r in loads if r["target_table"] == table]:
                found = indexed.get((row["dump_id"], row["warehouse_id"], table), [])
                committed = [r for r in found if r["committed_at"] is not None]
                actual = counts_by_dump.get(str(row["dump_id"]), 0)
                if row["status"] == "rejected":
                    letter = control.execute(
                        "SELECT count(*) AS n FROM control.dead_letter WHERE run_id=%s",
                        (row["run_id"],),
                    ).fetchone()["n"]
                    if actual or committed or not letter:
                        errors.append(
                            table + ": rejected dump has rows/receipt or no dead letter"
                        )
                    continue
                if row["status"] != "loaded" or len(committed) != 1:
                    errors.append(table + ": dump lacks exactly one committed receipt")
                    continue
                receipt = committed[0]
                if (
                    receipt["rows"] != actual
                    or receipt["generation"] != row["generation"]
                ):
                    errors.append(
                        table
                        + ": receipt row count/generation differs from loaded rows"
                    )
        for row in loads:
            if row["target_table"] is None:
                errors.append("loadable dump without registered load")
        known = {(r["dump_id"], r["warehouse_id"], r["target_table"]) for r in loads}
        if any(k not in known for k in indexed):
            errors.append("warehouse receipt without a registered output/rejected load")
        print(f"AUDIT dumps={len(loads)} receipts={len(receipts)} tables={len(tables)}")
    for error in errors:
        print("AUDIT FAIL " + error)
    if errors:
        raise AssertionError(
            f"{len(errors)} duplicate/missing-row or receipt discrepancies"
        )
    print(
        "AUDIT PASS no duplicate/missing rows; every landed dump has exactly one committed receipt; rejected dumps retained without rows"
    )
