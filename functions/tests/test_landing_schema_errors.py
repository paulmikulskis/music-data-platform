"""Permanent schema failures release claims; temporary database failures can retry."""

import time
from uuid import uuid4

import psycopg
import pytest
from manifest_validation_fixture import retained
from mdp_functions.dumps import json_bytes
from test_landing import pending


async def test_dependent_view_type_change_fails_promptly(rt):
    load = await pending(rt)
    key, manifest = retained(rt, load)
    table = "schema_failure_" + uuid4().hex
    view = table + "_view"
    with psycopg.connect(rt.settings.warehouse_url) as conn:
        conn.execute(
            psycopg.sql.SQL("CREATE TABLE raw.{} (title text)").format(
                psycopg.sql.Identifier(table)
            )
        )
        conn.execute(
            psycopg.sql.SQL("CREATE VIEW raw.{} AS SELECT title FROM raw.{}").format(
                psycopg.sql.Identifier(view), psycopg.sql.Identifier(table)
            )
        )
    manifest["target_table"] = "raw." + table
    manifest["columns"]["title"] = "bigint"
    rt.db.execute(
        "UPDATE control.load SET target_table=%s WHERE id=%s",
        (manifest["target_table"], load["id"]),
    )
    rt.store.put(key, json_bytes(manifest))
    loader = rt.landing(load["warehouse_id"])
    loader.timeout_s = 60
    started = time.monotonic()
    assert loader.process(load["id"])
    assert rt.db.one(
        "SELECT status,claim_token,claim_expires_at FROM control.load WHERE id=%s",
        (load["id"],),
    ) == {"status": "rejected", "claim_token": None, "claim_expires_at": None}
    batch = rt.db.one("SELECT * FROM control.batch WHERE run_id=%s", (load["run_id"],))
    rt.complete_batch(batch, "succeeded")
    rt.settle(load["run_id"])
    assert time.monotonic() - started < 5
    result = rt.receipts(load["run_id"])["run"]
    assert (result["status"], result["error_class"]) == ("failed", "schema_breaking")
    assert "cannot alter type of a column used by a view" in result["error_message"]
    assert "/runbooks/schema-breaking" in result["error_message"]
    assert not loader.process(load["id"])
    loader.drain(load["run_id"])
    assert rt.db.one(
        "SELECT count(*) AS n FROM control.dead_letter WHERE run_id=%s", (load["run_id"],)
    )["n"] == 1
    assert not any(str(r["dump_id"]) == str(load["dump_id"]) for r in rt.warehouse.receipts())


@pytest.mark.parametrize(
    "error,permanent",
    [
        (psycopg.errors.DependentObjectsStillExist, True),
        (psycopg.errors.CannotCoerce, True),
        (psycopg.errors.WrongObjectType, True),
        (psycopg.errors.LockNotAvailable, False),
        (psycopg.errors.DeadlockDetected, False),
        (psycopg.errors.QueryCanceled, False),
        (psycopg.errors.ConnectionFailure, False),
    ],
)
async def test_schema_errors_are_terminal_but_database_interruptions_can_retry(
    rt, monkeypatch, error, permanent
):
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def refused(*args):
        raise error("Fixture database refusal")

    monkeypatch.setattr(loader.warehouse, "ensure", refused)
    if permanent:
        assert loader.process(load["id"])
        assert rt.db.one(
            "SELECT error_class FROM control.run WHERE id=%s", (load["run_id"],)
        )["error_class"] == "schema_breaking"
    else:
        with pytest.raises(error):
            loader.process(load["id"])
    assert rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
        "status"
    ] == ("rejected" if permanent else "claimed")
