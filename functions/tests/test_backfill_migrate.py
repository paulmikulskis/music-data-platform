"""Real receipt fences, isolated backfill cursors, and a second warehouse database."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound, url_database
from mdp_functions.api import create_app
from mdp_functions.layers import Ctx, bronze
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY, discover, sync
from psycopg import sql

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def restore_registry():
    discover()
    before = set(REGISTRY)
    yield
    for key in set(REGISTRY) - before:
        del REGISTRY[key]


async def test_backfill_window_and_cycle_isolation(rt, tmp_path):
    @bronze(
        source_key="backfill_probe",
        writes=["raw.backfill_probe"],
        cadence="daily",
        external=False,
    )
    async def source(ctx: Ctx):
        ctx.observed(1)
        ctx.set_cursor(None, {"offset": 1})
        yield {
            "window_start": ctx.window["from"] if ctx.window else "ordinary",
            "value": 7,
        }

    sync(rt.db)
    _, original = await bound(rt, "backfill_probe")
    await rt.execute(original["id"])
    rt.cycles.close(original["cycle_id"])
    before_cycle = rt.db.one(
        "SELECT * FROM control.cycle WHERE id=%s", (original["cycle_id"],)
    )
    before_manifest = rt.db.all(
        "SELECT dump_id FROM control.cycle_manifest(%s) ORDER BY dump_id", (original["cycle_id"],)
    )
    before_cursor = rt.db.all("SELECT * FROM control.cursor WHERE cursor_key='default'")
    app = create_app(rt.settings, rt, recover=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        body = {
            "source_key": "backfill_probe",
            "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"},
        }
        response = await client.post("/v1/backfill", json=body)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        await rt.tasks[run_id]
        result = rt.receipts(run_id)
        assert result["run"]["status"] == "succeeded"
        assert result["run"]["kind"] == "backfill"
        assert result["run"]["work_key"] != original["work_key"]
        assert result["run"]["cycle_id"] != original["cycle_id"]
        with psycopg.connect(rt.settings.warehouse_url) as c:
            value = c.execute(
                "SELECT window_start FROM raw.backfill_probe WHERE _run_id=%s",
                (run_id,),
            ).fetchone()[0]
            assert datetime.fromisoformat(value) == datetime(
                2026, 1, 1, tzinfo=timezone.utc
            )
        assert (
            rt.db.one(
                "SELECT * FROM control.cycle WHERE id=%s", (original["cycle_id"],)
            )
            == before_cycle
        )
        assert (
            rt.db.all(
                "SELECT dump_id FROM control.cycle_manifest(%s) ORDER BY dump_id",
                (original["cycle_id"],),
            )
            == before_manifest
        )
        assert (
            rt.db.all("SELECT * FROM control.cursor WHERE cursor_key='default'")
            == before_cursor
        )
        assert (
            await client.post("/v1/backfill", json={"source_key": "backfill_probe"})
        ).status_code == 422
        assert (
            await client.post(
                "/v1/backfill", json={**body, "cycle_id": str(original["cycle_id"])}
            )
        ).status_code == 422
        replay = await client.post(
            "/v1/backfill",
            json={
                "source_key": "backfill_probe",
                "cycle_id": str(original["cycle_id"]),
            },
        )
        assert replay.status_code == 202, replay.text
        await rt.tasks[replay.json()["run_id"]]
        # Record actual endpoint responses used by control conformance.
        import json
        from pathlib import Path

        if os.environ.get("MDP_RECORD_CONTRACTS") == "1":
            Path(
                "control/packages/contracts/test/fixtures/_v1_backfill_post.json"
            ).write_text(
                json.dumps(
                    {
                        "path": "/v1/backfill",
                        "url": "/v1/backfill",
                        "method": "post",
                        "status": 202,
                        "request": body,
                        "response": response.json(),
                    },
                    indent=2,
                )
                + "\n"
            )


async def test_target_backfill_only_selected(rt):
    _, original = await bound(rt, "fixture_accounts")
    await rt.execute(original["id"])
    target = rt.db.one("SELECT id FROM control.target ORDER BY id LIMIT 1")["id"]
    run = rt.backfill("fixture_accounts", target_ids=[str(target)])
    await rt.execute(run["id"])
    assert rt.receipts(run["id"])["run"]["status"] == "succeeded"
    batches = rt.db.all(
        "SELECT target_ids FROM control.batch WHERE run_id=%s", (run["id"],)
    )
    assert [t for b in batches for t in b["target_ids"]] == [target]


async def test_migrate_exactly_once_and_recovery(rt, databases, monkeypatch):
    _, run = await bound(rt, "billboard_hot100")
    await rt.execute(run["id"])
    name = "mdp_test_migrate_" + uuid4().hex[:12]
    admin = databases["admin_warehouse"]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(url_database(admin, name)) as c:
        c.execute("CREATE SCHEMA raw AUTHORIZATION loader_wh")
        c.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO loader_wh").format(sql.Identifier(name)))
    destination_url = url_database(rt.settings.warehouse_url, name)
    monkeypatch.setenv("MDP_MIGRATION_TEST_URL", destination_url)
    with psycopg.connect(databases["admin_control"]) as c:
        destination = c.execute(
            "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres',%s,'MDP_MIGRATION_TEST_URL',false) RETURNING id",
            (name,),
        ).fetchone()[0]
    try:
        # Crash after durable queuing, then let the normal recovery pass drain it
        # despite the original attempt having completed already.
        from mdp_functions.landing import Landing

        with monkeypatch.context() as m:
            m.setattr(
                Landing,
                "process",
                lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted migration")),
            )
            with pytest.raises(OSError):
                rt.migrate(destination, source_keys=["billboard_hot100"])
        await Recovery(rt).once(resume=False)
        first = rt.migrate(destination, source_keys=["billboard_hot100"])
        assert (
            first["loaded"] > 0
            and first["queued"] == first["pending"] == first["rejected"] == 0
        )
        with psycopg.connect(destination_url) as c:
            before = c.execute("SELECT count(*) FROM raw.chart_entries").fetchone()[0]
            receipts = c.execute(
                "SELECT count(*) FROM raw._load_receipts WHERE committed_at IS NOT NULL"
            ).fetchone()[0]
        assert before > 0
        app = create_app(rt.settings, rt, recover=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + rt.settings.service_token},
        ) as client:
            body = {
                "warehouse_id": str(destination),
                "source_keys": ["billboard_hot100"],
            }
            response = await client.post("/v1/migrate", json=body)
            assert response.status_code == 200, response.text
            assert response.json() == first
            import json
            from pathlib import Path

            if os.environ.get("MDP_RECORD_CONTRACTS") == "1":
                Path(
                    "control/packages/contracts/test/fixtures/_v1_migrate_post.json"
                ).write_text(
                    json.dumps(
                        {
                            "path": "/v1/migrate",
                            "url": "/v1/migrate",
                            "method": "post",
                            "status": 200,
                            "request": body,
                            "response": response.json(),
                        },
                        indent=2,
                    )
                    + "\n"
                )
        with psycopg.connect(destination_url) as c:
            assert (
                c.execute("SELECT count(*) FROM raw.chart_entries").fetchone()[0]
                == before
            )
            assert (
                c.execute(
                    "SELECT count(*) FROM raw._load_receipts WHERE committed_at IS NOT NULL"
                ).fetchone()[0]
                == receipts
            )
        assert all(
            r["op"] == "load" and r["generation"] == 1
            for r in rt.db.all(
                "SELECT op,generation FROM control.load WHERE warehouse_id=%s",
                (destination,),
            )
        )
    finally:
        with psycopg.connect(databases["admin_control"]) as c:
            c.execute("DELETE FROM control.load WHERE warehouse_id=%s", (destination,))
            c.execute("DELETE FROM control.warehouse WHERE id=%s", (destination,))
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
