"""Tests for enrichment runtime."""

import socket
import threading
import time
from uuid import uuid4

import psycopg
import pytest
import uvicorn
from conftest import bound
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx, silver
from mdp_functions.llm import params_hash, step_version
from mdp_functions.registry import REGISTRY, discover, sync
from mdp_functions.workbench import diff_rows, validate_sql
from psycopg.types.json import Jsonb


@pytest.fixture(autouse=True)
def registry():
    discover()


@pytest.fixture
def proxy():
    from stubs.litellm_stub import app, spend

    spend.clear()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    thread.join(5)
    sock.close()


def inputs(databases, count=6):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS marts")
        conn.execute("DROP TABLE IF EXISTS marts.mart_enrichment_fixture")
        conn.execute(
            "CREATE TABLE marts.mart_enrichment_fixture(platform_account_id text,followers bigint,input_ref text,input_version text,source_key text,_cycle_id text)"
        )
        for n in range(count):
            conn.execute(
                "INSERT INTO marts.mart_enrichment_fixture VALUES (%s,%s,md5(%s),md5('v1'),'fixture_accounts',%s)",
                (f"fixture-{n}", 1000 + n, str(n), str(uuid4())),
            )
        conn.execute("GRANT USAGE ON SCHEMA marts TO service_read")
        conn.execute("GRANT SELECT ON marts.mart_enrichment_fixture TO service_read")


def configure(rt, databases, version=1, cap=None):
    params = {"temperature": 0, "max_tokens": 8, "max_cost_cents": 25}
    ph = params_hash(params)
    cv = step_version("fixture-classifier", version, ph)
    with psycopg.connect(databases["admin_control"]) as conn:
        prompt = conn.execute(
            "INSERT INTO control.prompt(name,version,body) VALUES (%s,%s,'Classify fixtures') RETURNING id",
            ("fixture-" + uuid4().hex, version),
        ).fetchone()[0]
        step = conn.execute(
            "INSERT INTO control.llm_step(source_key,model,prompt_id,prompt_version,params,params_hash,step_version,litellm_key_alias) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_key,step_version) DO UPDATE SET prompt_id=EXCLUDED.prompt_id RETURNING id",
            (
                "fixture_enrichment",
                "fixture-classifier",
                prompt,
                version,
                Jsonb(params),
                ph,
                cv,
                "fixture",
            ),
        ).fetchone()[0]
        if cap:
            conn.execute(
                "INSERT INTO control.budget(scope,scope_id,period,cap_cents,soft_pct,hard_action) VALUES ('llm_step',%s,'daily',%s,80,'pause')",
                (step, cap),
            )
    sync(rt.db)
    return cv


async def test_silver_socket_is_failed(rt, databases):
    inputs(databases, 1)

    @silver(
        source_key="enrichment_socket_probe",
        reads=["marts.mart_enrichment_fixture"],
        writes=["raw.socket_probe"],
        cadence="daily",
    )
    async def probe(ctx, rows):
        assert not hasattr(ctx, "http")
        socket.socket()
        yield rows[0]

    try:
        sync(rt.db)
        _, run = await bound(rt, "enrichment_socket_probe")
        await rt.execute(run["id"])
        row = rt.db.one(
            "SELECT status,error_class FROM control.run WHERE id=%s", (run["id"],)
        )
        assert row == {"status": "failed", "error_class": "egress_blocked"}
        print("EGRESS status=failed error_class=egress_blocked")
    finally:
        REGISTRY.pop("enrichment_socket_probe")


async def test_gold_cap_snapshot_and_costsync(rt, databases, proxy):
    from mdp_functions.costsync import reconcile

    inputs(databases)
    cv = configure(rt, databases, cap=100)
    rt.settings.litellm_base_url = proxy
    rt.settings.litellm_keys = {"fixture": uuid4().hex}
    rt.settings.litellm_admin_key = uuid4().hex
    _, run = await bound(rt, "fixture_enrichment")
    await rt.execute(run["id"])
    row = rt.db.one("SELECT * FROM control.run WHERE id=%s", (run["id"],))
    assert row["status"] == "partial", row["error_message"]
    assert row["error_class"] == "cost_cap_hit"
    assert row["cost_cents"] == 100
    assert row["rows_written"] == 4
    assert row["rows_rejected"] == 2
    assert row["input_dump_id"] and row["config_version"] != cv
    assert row["resolved_config"]["llm_step"]["step_version"] == cv
    with rt.db.transaction() as conn:
        conn.execute("UPDATE control.cost_ledger SET occurred_at='2025-01-02T03:04:05Z' WHERE origin='estimate'")
    result = await reconcile(rt)
    assert rt.db.one("SELECT bool_and(occurred_at='2025-01-02T03:04:05Z'::timestamptz) AS kept FROM control.cost_ledger WHERE origin='litellm'")["kept"]
    # Repair an existing replacement too, as an older reconciliation may have changed its day.
    with rt.db.transaction() as conn:
        conn.execute("UPDATE control.cost_ledger SET occurred_at=now() WHERE origin='litellm'")
    assert result["reconciled"] == 4
    assert (
        rt.db.one(
            "SELECT sum(cost_cents) AS n FROM control.cost_ledger WHERE is_current"
        )["n"]
        == 100
    )
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.cost_ledger WHERE origin='estimate' AND is_current"
        )["n"]
        == 0
    )
    await reconcile(rt)
    assert rt.db.one("SELECT bool_and(occurred_at='2025-01-02T03:04:05Z'::timestamptz) AS kept FROM control.cost_ledger WHERE origin='litellm'")["kept"]
    assert (
        rt.db.one(
            "SELECT sum(cost_cents) AS n FROM control.cost_ledger WHERE is_current"
        )["n"]
        == 100
    )
    print(
        "CAP status=partial cost_cents=100 rows_written=4 rows_rejected=2; costsync idempotent"
    )


async def test_rerun_by_config_version_is_a_manual_key_with_its_own_snapshot(rt, databases, proxy):
    inputs(databases, 1)
    first = configure(rt, databases, version=11)
    rt.settings.litellm_base_url = proxy
    rt.settings.litellm_keys = {"fixture": uuid4().hex}
    binding, run = await bound(rt, "fixture_enrichment")
    await rt.execute(run["id"])
    rt.cycles.close(run["cycle_id"])
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(
            "UPDATE marts.mart_enrichment_fixture SET input_version=md5('changed-after-first-run')"
        )
    second = configure(rt, databases, version=12)
    # A later daily cycle is bound but not closed; the caller names its run.
    later = await rt.cycles.bind_cycle("daily", "global", "daily:" + uuid4().hex, "scheduled", "local:daily", runner="core")
    again = rt.admit("fixture_enrichment", dbt_run_id=later["dbt_run_id"], config_version=second)
    await rt.execute(again["id"])
    assert first != second
    assert again["work_key"].startswith("manual:") and not run["work_key"].startswith("manual:")
    # It binds the newest closed cycle, whose build the input relation holds, not the caller's cycle.
    assert again["cycle_id"] == run["cycle_id"]
    # The same rerun request resolves to the same manual run.
    assert rt.admit("fixture_enrichment", dbt_run_id=later["dbt_run_id"], config_version=second)["id"] == again["id"]
    # A full retry after the rerun still resolves to the scheduled run's frozen configuration.
    assert rt.admit("fixture_enrichment", dbt_run_id=binding)["id"] == run["id"]
    frozen = {
        r["id"]: r for r in rt.db.all(
            "SELECT id,status,input_dump_id,resolved_config->'llm_step'->>'step_version' AS step FROM control.run WHERE id=ANY(%s::uuid[])",
            ([run["id"], again["id"]],),
        )
    }
    assert {r["status"] for r in frozen.values()} == {"succeeded"}
    assert (frozen[run["id"]]["step"], frozen[again["id"]]["step"]) == (first, second)
    # The rerun froze its own configuration and took its own snapshot of the current relation.
    assert frozen[run["id"]]["input_dump_id"] != frozen[again["id"]]["input_dump_id"]
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        versions = dict(conn.execute(
            "SELECT _run_id::text,input_version FROM raw.fixture_enrichment WHERE _run_id=ANY(%s::uuid[])",
            ([run["id"], again["id"]],),
        ).fetchall())
    assert versions[str(again["id"])] != versions[str(run["id"])]
    # Its dumps are never derived rows of the closed cycle; the next close stamps them.
    rerun_dumps = [r["id"] for r in rt.db.all("SELECT id FROM control.dump WHERE run_id=%s AND kind='output'", (again["id"],))]
    assert rt.db.one("SELECT count(*) AS n FROM control.cycle_input WHERE dump_id=ANY(%s::uuid[])", (rerun_dumps,))["n"] == 0
    closed = rt.cycles.close(later["cycle_id"])
    assert {r["close_no"] for r in rt.db.all("SELECT close_no FROM control.dump WHERE id=ANY(%s::uuid[])", (rerun_dumps,))} == {closed["close_no"]}


async def test_paused_gold_needs_no_llm_step_or_proxy(rt, databases):
    """No llm_step row and no LiteLLM: enabled refuses, disabled admits as paused."""
    inputs(databases, 1)
    assert not rt.db.one("SELECT count(*) AS n FROM control.llm_step")["n"]
    assert not rt.settings.litellm_base_url
    with pytest.raises(ServiceError) as refused:
        await bound(rt, "fixture_enrichment")
    assert refused.value.error_class == "llm_step_missing"
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.streamline SET enabled=false WHERE source_key='fixture_enrichment'"
        )
    _, run = await bound(rt, "fixture_enrichment")
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])
    # The shape mdp.invoke accepts, so the dependent staging and mart still build.
    assert result["run"]["status"] == "paused"
    assert result["run"]["error_class"] == "paused"
    assert [(r["status"], r["coverage"]) for r in result["receipts"]] == [
        ("paused", "empty")
    ]
    assert rt.db.one(
        "SELECT (SELECT count(*) FROM control.run_attempt WHERE run_id=%s) AS attempts,"
        "(SELECT count(*) FROM control.batch WHERE run_id=%s) AS batches",
        (run["id"], run["id"]),
    ) == {"attempts": 0, "batches": 0}


def test_undeclared_read_and_workbench_isolation():
    manifest = REGISTRY["mb_resolve"]
    ctx = Ctx(manifest, {"id": uuid4(), "cycle_id": uuid4()})
    with pytest.raises(ServiceError, match="declared reads"):
        ctx.read("raw.secret")
    for query in (
        "select * from wb_other.table",
        'select * from "WB_OTHER".table',
        "select 1; delete from raw.a",
        "with a as (delete from raw.a returning *) select * from a",
    ):
        with pytest.raises(ServiceError):
            validate_sql(query, "wb_own")
    assert diff_rows([{"id": 1, "v": "a"}], [{"id": 1, "v": "b"}], ["id"])[2] == [
        {"before": {"id": 1, "v": "a"}, "after": {"id": 1, "v": "b"}}
    ]


@pytest.mark.parametrize(
    "action,expected_status,expected_cost",
    [("warn", "succeeded", 50), ("degrade", "partial", 25)],
)
async def test_gold_hard_actions(
    rt, databases, proxy, action, expected_status, expected_cost
):
    inputs(databases, 2)
    original = configure(rt, databases, version=20, cap=25)
    params = {
        "temperature": 0,
        "max_tokens": 8,
        "max_cost_cents": 50,
        "fallback_max_cost_cents": 25,
    }
    ph = params_hash(params)
    cv = step_version("fixture-classifier", 20, ph)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.llm_step SET params=%s,params_hash=%s,step_version=%s,fallback_model=%s WHERE step_version=%s",
            (Jsonb(params), ph, cv, "fixture-fallback", original),
        )
        conn.execute("UPDATE control.budget SET hard_action=%s", (action,))
    rt.settings.litellm_base_url = proxy
    rt.settings.litellm_keys = {"fixture": uuid4().hex}
    _, run = await bound(rt, "fixture_enrichment")
    await rt.execute(run["id"])
    result = rt.db.one(
        "SELECT status,cost_cents FROM control.run WHERE id=%s", (run["id"],)
    )
    assert result == {"status": expected_status, "cost_cents": expected_cost}
