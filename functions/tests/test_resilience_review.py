"""Regression cases for advisory checks and truthful delivery coverage."""

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.layers import Targets, bronze
from mdp_functions.registry import REGISTRY, sync
from pydantic import BaseModel


@pytest.mark.parametrize("case,batch_size,successes,total", [
    ("healthy", 10, 10, 10), ("stale", 1, 9, 10), ("stale", 10, 9, 10),
    ("schema", 10, 9, 10), ("mixed", 10, 10, 10),
    ("skipped", 12, 9, 10),
])
async def test_target_coverage_after_validation(rt, databases, case, batch_size, successes, total):
    class Row(BaseModel):
        value: int

    key = "test_review_coverage"

    @bronze(source_key=key, writes=["raw.review_coverage"], targets=Targets("account"),
            schema=Row, hosts=["review.invalid"], min_target_coverage=.9)
    async def collect(ctx, targets):
        for target in targets:
            n = int(target["platform_account_id"])
            if n >= 10:
                continue
            await ctx.http.get(f"https://review.invalid/{n}")
            ctx.observed(1)
            yield {"value": "invalid" if n == 0 and case in {"schema", "mixed"} else n}
            if n == 0 and case == "mixed":
                ctx.observed(1)
                yield {"value": 0}

    try:
        sync(rt.db)
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("DELETE FROM control.target")
            conn.execute("""INSERT INTO control.target(id,target_set_id,platform,platform_account_id,resolution_status,activated_at)
                SELECT ('00000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,id,'fixture',n::text,'resolved',now()
                FROM control.target_set CROSS JOIN generate_series(0,%s) n""", (11 if case == "skipped" else 9,))
            conn.execute("UPDATE control.streamline SET batch_size=%s WHERE source_key=%s", (batch_size, key))
            conn.execute("INSERT INTO control.host_health(host,host_rps) VALUES ('review.invalid',10000) ON CONFLICT(host) DO UPDATE SET blocked_until=NULL")
        rt.transport = httpx.MockTransport(lambda req: httpx.Response(
            404 if req.url.path == "/0" and case in {"stale", "skipped"} else 200, json={"ok": True}))
        _, run = await bound(rt, key)
        await rt.execute(run["id"])
        state = rt.receipts(run["id"])
        receipt = state["receipts"][0]
        assert receipt["targets_succeeded"] == successes
        assert receipt["targets_total"] == total
        assert receipt["target_coverage_met"] is True
        assert any(r["row_coverage"] == "partial" for r in state["receipts"]) is (case != "healthy")
        assert state["run"]["status"] == ("succeeded" if case in {"healthy", "mixed"} else "partial")
        assert state["run"]["coverage"] == ("full" if case == "healthy" else "partial")
        assert rt.cycles.close(run["cycle_id"])["status"] == "closed"
    finally:
        REGISTRY.pop(key, None)


async def test_expected_schema_is_scoped_to_table(rt, databases):
    from mdp_functions.expected_schema import acknowledge

    class Before(BaseModel):
        value: int

    class After(BaseModel):
        value: int
        added: str | None = None

    key = "test_review_schema"
    tables = ["raw.review_a", "raw.review_b"]

    @bronze(source_key=key, writes=tables, schema=dict.fromkeys(tables, Before))
    async def collect(ctx):
        for table in tables:
            ctx.observed(1)
            ctx.emit(table, {"value": 1})

    try:
        sync(rt.db)
        _, first = await bound(rt, key)
        await rt.execute(first["id"])
        REGISTRY[key].schema[tables[0]] = After
        with psycopg.connect(databases["admin_control"]) as conn:
            acknowledge(conn, rt.settings.schema_root)
        REGISTRY[key].schema[tables[1]] = After
        _, second = await bound(rt, key)
        await rt.execute(second["id"])
        alerts = rt.db.all("SELECT subject_id FROM control.alert WHERE run_id=%s AND class='schema_drift'", (second["id"],))
        assert len(alerts) == 1 and "raw.review_b" in alerts[0]["subject_id"]
    finally:
        REGISTRY.pop(key, None)


async def test_parking_warning_is_deduplicated_and_unavailable_check_fails_open(rt, monkeypatch):
    from mdp_functions.promoter import ControlTargets
    from mdp_functions.recovery import Recovery

    _, run = await bound(rt)
    recovery = Recovery(rt)
    monkeypatch.setattr(recovery, "recover", list)
    monkeypatch.setattr(recovery, "probe_reference", lambda: None)
    monkeypatch.setattr(recovery, "retain", lambda: None)
    rt.settings = rt.settings.model_copy(update={"control_api_url": "http://control.invalid", "control_api_key": "fixture"})

    async def warning(*args):
        return {"parked": [], "warnings": [{"run_id": str(run["id"]), "source": "fixture", "cycle_id": str(run["cycle_id"]), "reason": "No healthy peer on the same host; targets stay active"}]}

    monkeypatch.setattr(ControlTargets, "command", warning)
    await recovery.once(resume=False)
    await recovery.once(resume=False)
    assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='stale_target'", (run["id"],))["n"] == 1

    async def unavailable(*args):
        raise OSError("unavailable")

    monkeypatch.setattr(ControlTargets, "command", unavailable)
    await recovery.once(resume=False)


@pytest.mark.parametrize("expected", [False, True])
async def test_untargeted_empty_run_only_fails_when_records_are_expected(rt, expected):
    key = "test_empty_review"

    @bronze(source_key=key, writes=["raw.empty_review"], knobs={"allow_partial": True})
    async def collect(ctx):
        if expected:
            ctx.observed(1)

    try:
        sync(rt.db)
        _, run = await bound(rt, key)
        await rt.execute(run["id"])
        assert rt.receipts(run["id"])["run"]["status"] == ("failed" if expected else "succeeded")
    finally:
        REGISTRY.pop(key, None)


async def test_failed_cadence_sweep_still_reaps_deadlines_and_leases(rt, monkeypatch, caplog):
    from mdp_functions import admission, cadence_health
    from mdp_functions.recovery import Recovery

    _, expired = await bound(rt)
    old = admission.attempt(rt.db, expired["id"])
    rt.db.execute("UPDATE control.run_attempt SET deadline_at=now()-interval '1 second' WHERE id=%s", (old["id"],))
    _, resumable = await bound(rt)
    fresh = admission.attempt(rt.db, resumable["id"])
    rt.db.execute("UPDATE control.batch SET status='running',attempt_id=%s,lease_expires_at=now()-interval '1 second' WHERE run_id=%s", (fresh["id"], resumable["id"]))

    def unavailable(conn):
        conn.execute("SELECT 1/0")  # The sweep's transaction must roll back before recovery proceeds.

    monkeypatch.setattr(cadence_health, "sweep", unavailable)
    recovery = Recovery(rt)
    runs = recovery.reap()
    recovery.reap()
    assert set(runs) == {expired["id"], resumable["id"]}
    assert rt.db.one("SELECT status,error_class FROM control.run WHERE id=%s", (expired["id"],)) == {"status": "failed", "error_class": "invoke_timeout"}
    assert rt.db.one("SELECT status,lease_expires_at FROM control.batch WHERE run_id=%s", (resumable["id"],)) == {"status": "queued", "lease_expires_at": None}
    assert "Cadence health check unavailable" in caplog.text
    assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE class='cadence_failed'")["n"] <= 1
