"""Tests for prove udf."""

import asyncio
import json
import os
import subprocess
from uuid import uuid4

import psycopg
import uvicorn
from mdp_functions.api import create_app
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO
from psycopg.types.json import Jsonb


async def test_real_udf_transport_binding_completed_reuse(
    rt: Runtime, databases: dict[str, str]
) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(rt.settings, rt, recover=False),
            host="0.0.0.0",
            port=8081,
            log_level="error",
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        env = {
            **os.environ,
            "MDP_SERVICE_URL": "http://host.docker.internal:8081",
            "MDP_SERVICE_TOKEN": rt.settings.service_token,
        }
        installed = await asyncio.to_thread(
            subprocess.run,
            [
                "bash",
                str(REPO / "functions/udf/postgres/install.sh"),
                databases["admin_warehouse"],
            ],
            env=env,
            capture_output=True,
            check=False,
        )
        assert installed.returncode == 0, "UDF installation failed"

        def invoke_cycle(dbt_id: str, reason: str) -> tuple[str, list[tuple]]:
            with psycopg.connect(databases["admin_warehouse"]) as conn:
                assert conn.execute(
                    "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                ).fetchone()[0]
                conn.execute("SET ROLE dbt_transform")
                binding = conn.execute(
                    "SELECT mdp.bind_cycle('weekly','global',%s,%s,'local:weekly',NULL,'core','[]')",
                    (dbt_id, reason),
                ).fetchone()[0]
                if isinstance(binding, str):
                    binding = json.loads(binding)
                params = Jsonb(
                    {
                        "dbt_run_id": dbt_id,
                        "cadence": "weekly",
                        "model": "model.fixture.weekly",
                        "deadline_s": 30,
                    }
                )
                receipts = []
                for source in ("targets_export", "billboard_hot100", "cycle_close"):
                    rows = conn.execute(
                        "SELECT * FROM mdp.invoke(%s,%s)", (source, params)
                    ).fetchall()
                    assert rows and rows[0][1] == "succeeded"
                    if source == "billboard_hot100":
                        receipts = rows
                return str(binding["cycle_id"]), receipts

        first = await asyncio.to_thread(invoke_cycle, uuid4().hex, "scheduled")
        calls = rt.db.one("SELECT count(*) AS n FROM control.call_ledger")["n"]
        second = await asyncio.to_thread(invoke_cycle, uuid4().hex, "other")
        assert first == second and first[1][0][3] == 3
        assert (
            rt.db.one("SELECT count(*) AS n FROM control.call_ledger")["n"]
            == calls
            == 1
        )
        assert (
            rt.db.one("SELECT status FROM control.cycle WHERE id=%s", (first[0],))[
                "status"
            ]
            == "closed"
        )
        assert (
            rt.db.one(
                "SELECT count(*) AS n FROM control.cycle_manifest(%s)",
                (first[0],),
            )["n"]
            == 1
        )
        assert (
            rt.db.one(
                "SELECT count(*) AS n FROM control.cycle_attempt WHERE runner='core' AND job_id='local:weekly'"
            )["n"]
            == 2
        )
    finally:
        server.should_exit = True
        await task
