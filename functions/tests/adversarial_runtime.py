"""fixtures missing from the existing runtime and lifecycle suites."""

import asyncio
import os
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import uvicorn
from conftest import bound
from mdp_functions.api import create_app
from mdp_functions.cycles import FakeAdminApi
from mdp_functions.layers import bronze
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO
from psycopg.conninfo import conninfo_to_dict
from test_landing import assertion, expire, pending


@asynccontextmanager
async def udf_service(rt, databases):
    sock = socket.socket()
    sock.bind(("0.0.0.0", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(rt.settings, rt, recover=False), log_level="error")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.02)
        env = os.environ | {
            "MDP_SERVICE_URL": f"http://{os.environ.get('MDP_ADVERSARIAL_UDF_HOST', 'host.docker.internal')}:{port}",
            "MDP_SERVICE_TOKEN": rt.settings.service_token,
        }
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "bash",
                str(REPO / "functions/udf/postgres/install.sh"),
                databases["admin_warehouse"],
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "isolated UDF installation failed"
        yield
    finally:
        server.should_exit = True
        await task
        sock.close()


async def model(rt, databases, tmp_path, source, *, runner="core", run_id=None):
    import shutil

    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(
            "CREATE SCHEMA IF NOT EXISTS adversarial AUTHORIZATION dbt_transform"
        )
    project = tmp_path / "dbt"
    (project / "models").mkdir(parents=True)
    shutil.copytree(REPO / "dbt/macros", project / "macros")
    (project / "dbt_project.yml").write_text(
        'name: adversarial\nversion: "1.0.0"\nconfig-version: 2\nprofile: music_data_platform\non-run-start: ["{{ mdp_bind_cycle() }}"]\n'
    )
    (project / "models/adversarial_invoke.sql").write_text(
        "{{ config(materialized='table') }}\n{{ mdp_invoke('" + source + "') }}\n"
    )
    options = conninfo_to_dict(databases["warehouse_url"])
    env = os.environ | {
        "MDP_PG_DB": options["dbname"],
        "MDP_PG_HOST": options["host"],
        "MDP_PG_PORT": options.get("port", "5432"),
        "MDP_PG_SCHEMA": "adversarial",
        "DBT_CLOUD_RUN_ID": run_id or uuid4().hex,
        "DBT_CLOUD_JOB_ID": "local:daily",
        "DBT_MDP_CADENCE": "daily",
        "DBT_MDP_SCOPE": "global",
        "DBT_CLOUD_RUN_REASON_CATEGORY": "scheduled",
        "MDP_RUNNER": runner,
        "DBT_TARGET_PATH": str(tmp_path / "target"),
        "DBT_LOG_PATH": str(tmp_path / "logs"),
    }
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "uv",
            "run",
            "--project",
            str(REPO / "dbt"),
            "dbt",
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(REPO / "dbt/profiles"),
            "--target",
            "pg_local",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    # No full SQL/credentials retained: the caller asserts exact detector output.
    return result


async def test_cloud_identity_model(rt, databases, tmp_path):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.dbt_job SET runner='cloud' WHERE job_id='local:daily'"
        )
        conn.execute("UPDATE control.runner_mode SET runner='cloud'")
    rid = "adversarial:" + uuid4().hex
    rt.cycles.admin = FakeAdminApi(
        {rid: {"id": rid, "job_definition_id": "wrong-job", "in_progress": True}}
    )
    async with udf_service(rt, databases):
        result = await model(
            rt, databases, tmp_path, "lifecycle_daily_probe", runner="cloud", run_id=rid
        )
    assert (
        result.returncode != 0 and "scope_mismatch" in result.stdout + result.stderr
    ), result.stdout[-1600:]
    assert rt.db.one("SELECT count(*) AS n FROM control.cycle_attempt")["n"] == 0
    assert rt.db.one("SELECT count(*) AS n FROM control.call_ledger")["n"] == 0
    print("IDENTITY scope_mismatch; model fails before invocation")


async def test_schema_breaking(rt, databases, tmp_path):
    @bronze(
        source_key="adversarial_shape",
        writes=["raw.adversarial_shape"],
        cadence="daily",
        keep_payload=True,
        key=["value"],
    )
    async def source(ctx):
        ctx.payloads.append({"value": 1})
        ctx.observed(1)
        yield {"value": 1}

    try:
        # Force a real ALTER TYPE failure at landing, after dump publication.
        # The unlanded sentinel is test DDL input, never a platform output row.
        with psycopg.connect(rt.settings.warehouse_url) as conn:
            conn.execute(
                "CREATE TABLE raw.adversarial_shape(value text, _dump_id uuid)"
            )
            conn.execute(
                "INSERT INTO raw.adversarial_shape(value) VALUES ('invalid integer')"
            )
        sync(rt.db, rt.warehouse)
        async with udf_service(rt, databases):
            result = await model(rt, databases, tmp_path, "adversarial_shape")
        assert (
            result.returncode != 0
            and "schema_breaking" in result.stdout + result.stderr
        ), result.stdout[-1600:]
        run = rt.db.one(
            "SELECT id,error_class FROM control.run WHERE error_class='schema_breaking'"
        )
        assert run
        assert rt.db.one(
            "SELECT payload_ref FROM control.dead_letter WHERE run_id=%s", (run["id"],)
        )["payload_ref"]
        assert rt.db.one(
            "SELECT runbook_slug FROM control.alert WHERE run_id=%s AND class='schema_breaking'",
            (run["id"],),
        )
        load = rt.db.one(
            "SELECT l.status,l.dump_id FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND d.kind='output'",
            (run["id"],),
        )
        assert load and load["status"] == "rejected"
        assert all(row["dump_id"] != load["dump_id"] for row in rt.warehouse.receipts())
        print("SCHEMA dump dead-lettered; alert; invoke model failed")
    finally:
        with psycopg.connect(rt.settings.warehouse_url) as conn:
            conn.execute("DELETE FROM raw.adversarial_shape WHERE _dump_id IS NULL")
        REGISTRY.pop("adversarial_shape", None)


async def test_deactivation_frozen_revision(rt, databases):
    _, first = await bound(rt, "fixture_accounts")
    target = rt.db.one("SELECT id FROM control.target ORDER BY id LIMIT 1")["id"]
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.target SET deactivated_at=now() WHERE id=%s", (target,)
        )
    await rt.execute(first["id"])
    assert rt.receipts(first["id"])["run"]["status"] == "succeeded"
    _, second = await bound(rt, "fixture_accounts")
    await rt.execute(second["id"])
    assert rt.receipts(second["id"])["run"]["status"] == "succeeded"
    with psycopg.connect(rt.settings.warehouse_url) as conn:
        for run, expected in ((first, 1), (second, 0)):
            assert (
                conn.execute(
                    "SELECT count(*) FROM raw.account_snapshots WHERE _run_id=%s AND _target_id=%s",
                    (str(run["id"]), str(target)),
                ).fetchone()[0]
                == expected
            )
    assert first["revision_id"] != second["revision_id"]
    print("DEACTIVATION current revision retained; next revision excludes target")


async def test_restart_pending_loads_and_repairs(rt, databases, tmp_path):
    first = await pending(rt)
    # Publication finished; release its worker permit through the real worker exit.
    # The warehouse load remains pending, which is the intended restart input.
    batch = rt.db.one("SELECT * FROM control.batch WHERE run_id=%s", (first["run_id"],))
    rt.complete_batch(batch, "succeeded")
    second = await pending(rt)
    loader = rt.landing(second["warehouse_id"])
    claim = loader.claim(second["id"])
    assert claim
    loader.repair(second["dump_id"], second["warehouse_id"], second["target_table"])
    expire(rt, second)
    settings = rt.settings
    await rt.close()
    started = time.monotonic()
    restarted = Runtime(settings)
    restarted.initialize()
    try:
        await Recovery(restarted).once(resume=False)
        assert time.monotonic() - started < 60
        for load in (first, second):
            assertion(restarted, load, tmp_path, "restart-" + str(load["id"]))
        print(
            "RECOVERY pending load and queued repair drained within 60s; one row set each"
        )
    finally:
        await restarted.close()
