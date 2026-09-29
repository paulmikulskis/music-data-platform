"""Small live fixtures not already owned by lifecycle.sh."""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ops/ci/lifecycle"))
from harness import Harness, lit


def execute(case, evidence):
    h = Harness(SimpleNamespace(target="pg_local"))
    with (evidence / (case["slug"] + "-queries.txt")).open("w") as h.log:
        h.safety()
        target = case["target"]
        if target == "previews":
            url = os.environ.get("MDP_CONTROL_API_URL", "http://127.0.0.1:8090")

            def call(action, body):
                response = httpx.post(
                    url + "/api/workbench/" + action,
                    json=body,
                    headers={"x-mdp-dev-user": "dev-user"},
                    timeout=30,
                )
                assert response.is_success, (
                    f"workbench {action}: HTTP {response.status_code}"
                )
                return response.json()

            cycle = h.scalar(
                "SELECT id FROM control.cycle WHERE status='closed' AND cadence='hourly' ORDER BY opened_at DESC LIMIT 1"
            )
            assert cycle, "preview needs a closed hourly fixture cycle"
            sessions = [call("createSession", {}) for _ in range(2)]
            schemas = [s["scratchSchema"] for s in sessions]
            assert len(set(schemas)) == 2 and all(s.startswith("wb_") for s in schemas)
            # Submit both before waiting: simultaneous requests, same model, different drafts.
            runs = [
                call(
                    "previewModel",
                    {
                        "sessionId": s["sessionId"],
                        "model": "adversarial_preview",
                        "sql": f"select {i} as marker",
                        "cycleId": cycle,
                    },
                )
                for i, s in enumerate(sessions, 1)
            ]
            deadline = time.monotonic() + 180
            for i, run in enumerate(runs, 1):
                while time.monotonic() < deadline:
                    state = call("status", {"runId": run["runId"]})
                    if state["status"] in ("failed", "cancelled"):
                        raise AssertionError(f"preview {i} {state['status']}")
                    if state["status"] == "succeeded":
                        break
                    time.sleep(1)
                else:
                    raise AssertionError("preview deadline")
                result = call("result", {"runId": run["runId"]})
                assert result["rows"] == [{"marker": i}], (
                    "preview content crossed schemas"
                )
            print("PREVIEW both succeeded in separate wb schemas")
            return
        if target == "grants":
            options = conninfo_to_dict(os.environ["MDP_WORKBENCH_WH_URL"])
            assert options["user"] == "workbench_wh"
            with psycopg.connect(make_conninfo(**options)) as conn:
                try:
                    conn.execute("SELECT * FROM mdp.invoke('fixture_accounts','{}'::jsonb)")
                except psycopg.errors.InsufficientPrivilege:
                    print("GRANTS permission denied for workbench invoke")
                else:
                    raise AssertionError("workbench was allowed to invoke")
            return
        with tempfile.TemporaryDirectory(prefix="mdp-adversarial-live-") as temp:
            project = Path(temp)
            (project / "models").mkdir()
            shutil.copytree(ROOT / "dbt/macros", project / "macros")
            (project / "dbt_project.yml").write_text(
                'name: adversarial\nversion: "1.0.0"\nconfig-version: 2\nprofile: music_data_platform\non-run-start: ["{{ mdp_bind_cycle() }}"]\n'
            )
            name = "adversarial_" + target
            (project / "models" / f"{name}.sql").write_text(
                "{{ config(materialized='table', tags=['scope:global','cadence:hourly']) }}\n"
                + (
                    "{{ mdp_invoke('fixture_accounts') }}\n"
                    if target == "empty"
                    else "select 1 as value\n"
                )
            )
            env = h.identity()
            env["MDP_PG_SCHEMA"] = "adversarial_" + uuid4().hex[:12]

            def untouched_identity():
                identity = lit(env["DBT_CLOUD_RUN_ID"])
                return all(
                    h.scalar(query) == 0
                    for query in (
                        f"SELECT count(*) FROM control.cycle_attempt WHERE dbt_run_id={identity}",
                        f"SELECT count(*) FROM control.cycle WHERE opened_by_dbt_run_id={identity}",
                        "SELECT count(*) FROM control.call_ledger l JOIN control.run_attempt a ON a.run_id=l.run_id "
                        + f"WHERE a.dbt_run_id={identity}",
                    )
                )

            if target == "scope":
                env["DBT_MDP_SCOPE"] = "tenant:" + str(uuid4())
                env["DBT_CLOUD_JOB_ID"] = "core-hourly-global"
                # Register only the real global identity. The claimed tenant must be refused.
                h.register(env | {"DBT_MDP_SCOPE": "global"})
            try:
                args = ["build", "--select", name]
                if target == "empty":
                    args += ["--empty"]
                else:
                    args += [
                        "--vars",
                        json.dumps({"tenant_slug": env["MDP_PG_SCHEMA"]}),
                    ]
                result = h.dbt(env, *args, project=project, ok=False)
                text = result.stdout + result.stderr
                print(text)
                if target == "scope":
                    assert result.returncode != 0 and "scope_mismatch" in text, (
                        "scope spoof was not refused by bind hook"
                    )
                    assert untouched_identity(), "refused bind changed cycles or calls"
                    print("SCOPE scope_mismatch; bind model failed")
                else:
                    assert result.returncode == 0, "empty dbt build failed"
                    assert untouched_identity(), (
                        "empty build created cycle/binding/vendor work"
                    )
                    with psycopg.connect(
                        os.environ["MDP_CONTROL_ADMIN_URL"], dbname="warehouse"
                    ) as conn:
                        n = conn.execute(
                            sql.SQL("SELECT count(*) FROM {}.{}").format(
                                sql.Identifier(env["MDP_PG_SCHEMA"]),
                                sql.Identifier(name),
                            )
                        ).fetchone()[0]
                        assert n == 0, "empty build returned nonempty receipts"
                    print("EMPTY no cycles or vendor calls; empty receipts")
            finally:
                with psycopg.connect(
                    os.environ["MDP_CONTROL_ADMIN_URL"], dbname="warehouse"
                ) as conn:
                    conn.execute(
                        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            sql.Identifier(env["MDP_PG_SCHEMA"])
                        )
                    )
