"""A dead member never withholds the other 58 targets from a daily close."""


import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.api import create_app
from mdp_functions.coverage import target_coverage
from mdp_functions.layers import Targets, bronze
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.target_probe import probe_target


def test_coverage_counts_members_not_rows_and_excludes_stale_completions():
    run = {"resolved_config": {"target_coverage": {"min_target_coverage": None}}}
    batch = {"target_ids": list(range(59)), "cursor_checkpoint": {"completed_targets": [*map(str, range(59)), "stale_target:0"]}}
    result = target_coverage(run, [batch])
    assert result["target_coverage"] == 58 / 59
    assert result["min_target_coverage"] == .9 and result["target_coverage_met"]
    batch["target_ids"] = [0]
    assert not target_coverage(run, [batch])["target_coverage_met"]


async def test_runner_failure_before_binding_and_overdue_cycle_alert_once(rt, databases):
    from mdp_functions.cadence_health import fail_runner, sweep

    with rt.db.transaction() as conn:
        first = fail_runner(conn, "early-failure", "local:daily", "runner", "missing profile")
        assert fail_runner(conn, "early-failure", "local:daily", "runner", "missing profile") == first
    _, run = await bound(rt)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.cycle SET opened_at=now()-interval '8 days' WHERE id=%s", (run["cycle_id"],))
    with rt.db.transaction() as conn:
        sweep(conn)
        sweep(conn)
    alerts = rt.db.all("SELECT severity,subject_type FROM control.alert WHERE class='cadence_failed'")
    assert sorted((a["severity"], a["subject_type"]) for a in alerts) == [("critical", "cycle"), ("critical", "dbt_job")]


def test_deploy_seed_marks_only_new_members_for_activation(rt, databases):
    from mdp_functions.chart_targets import seed_chart_targets
    from mdp_functions.settings import REPO

    with psycopg.connect(databases["admin_control"]) as conn:
        seed_chart_targets(conn, REPO / "inputs/chart_fixture_seed.csv", activate=False)
        rows = conn.execute("SELECT t.id,t.activated_at,s.promotion_reason FROM control.target t JOIN control.target_spec s ON s.target_id=t.id WHERE platform='shazam'").fetchall()
        assert len(rows) == 3 and all(row[1:] == (None, "seed") for row in rows)
        conn.execute("UPDATE control.target SET activated_at=now(),deactivated_at=now() WHERE id=%s", (rows[0][0],))
        conn.execute("UPDATE control.target_spec SET promotion_reason=NULL WHERE target_id=%s", (rows[1][0],))
        seed_chart_targets(conn, REPO / "inputs/chart_fixture_seed.csv", activate=False)
        assert conn.execute("SELECT t.deactivated_at IS NOT NULL,s.promotion_reason FROM control.target t JOIN control.target_spec s ON s.target_id=t.id WHERE t.id=%s", (rows[0][0],)).fetchone() == (True, "seed")
        assert conn.execute("SELECT promotion_reason FROM control.target_spec WHERE target_id=%s", (rows[1][0],)).fetchone() == (None,)


@pytest.fixture
async def source(rt, databases, monkeypatch):
    key = "test_target_resilience"
    @bronze(source_key=key, writes=["raw.resilience_fixture"], targets=Targets("account"), hosts=["resilience.invalid"])
    async def collect(ctx, targets):
        for target in targets:
            await ctx.http.get("https://resilience.invalid/" + target["platform_account_id"])
            ctx.observed(1)
            yield {"target": target["platform_account_id"]}
    sync(rt.db)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.target")
        conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) SELECT id,'fixture',n::text,'resolved',now() FROM control.target_set CROSS JOIN generate_series(0,58) n")
        conn.execute("UPDATE control.streamline SET batch_size=59,timeout_s=120 WHERE source_key=%s", (key,))
        conn.execute("INSERT INTO control.host_health(host,host_rps) VALUES ('resilience.invalid',10000) ON CONFLICT(host) DO UPDATE SET blocked_until=NULL")
    rt.settings = rt.settings.model_copy(update={"lease_s": 30})
    try:
        yield key
    finally:
        REGISTRY.pop(key, None)


@pytest.mark.parametrize("dead", [1, 7])
async def test_incident_receipt_and_cycle(rt, source, dead):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(404 if int(request.url.path[1:]) < dead else 200, json={"ok": True})
    rt.transport = httpx.MockTransport(respond)
    dbt_id, run = await bound(rt, source)
    await rt.execute(run["id"])
    state = rt.receipts(run["id"])
    assert len(calls) == 59
    receipt = state["receipts"][0]
    assert receipt["targets_succeeded"] == 59 - dead
    assert receipt["targets_total"] == 59
    assert receipt["min_target_coverage"] == .9
    assert receipt["target_coverage_met"] == (dead == 1)
    if dead == 1:
        assert state["run"]["status"] == "partial"
        closed = rt.cycles.close(run["cycle_id"])
        assert closed["status"] == "closed"
    else:
        assert state["run"]["status"] == "failed"
        app = create_app(rt.settings, rt, recover=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                     headers={"Authorization": "Bearer " + rt.settings.service_token}) as client:
            for _ in range(2):
                response = await client.post("/v1/alerts/runner_failed", json={"dbt_run_id": dbt_id, "model": "bronze_invoke__test_target_resilience", "error": "partial_coverage"})
                assert response.status_code == 200, response.text
        alert = rt.db.one("SELECT count(*) AS n FROM control.alert WHERE class='cadence_failed' AND subject_id=%s", (str(run["cycle_id"]),))
        assert alert["n"] == 1


@pytest.mark.parametrize("status", [200, 404, 410, 500])
async def test_probe_sends_one_paced_request_and_never_lands(rt, source, status):
    calls = []
    rt.transport = httpx.MockTransport(lambda req: (calls.append(req), httpx.Response(status, text="fixture"))[1])
    original = dict(REGISTRY)
    REGISTRY.clear()
    REGISTRY[source] = original[source]
    target = rt.db.one("SELECT * FROM control.target ORDER BY id LIMIT 1")
    try:
        result = await probe_target(rt, target, "account")
    finally:
        REGISTRY.update(original)
    assert result["status"] == ({200: "ok", 404: "stale_target", 410: "stale_target", 500: "vendor_retryable"}[status])
    assert len(calls) == 1
    assert calls[0].headers["User-Agent"].startswith("MusicDataPlatform/")
    assert rt.db.one("SELECT count(*) AS n FROM control.dump")["n"] == 0

async def test_expected_code_schema_is_acknowledged_but_upstream_widening_alerts(rt, databases):
    from mdp_functions.expected_schema import acknowledge
    from pydantic import BaseModel

    class Before(BaseModel):
        value: int

    class After(BaseModel):
        value: int
        owner_class_observed: str | None = None

    key = "test_expected_drift"
    @bronze(source_key=key, writes=["raw.expected_drift"], schema=Before)
    async def collect(ctx):
        ctx.observed(1)
        yield {"value": 1, "owner_class_observed": "platform"}
    try:
        sync(rt.db)
        _, first = await bound(rt, key)
        await rt.execute(first["id"])
        REGISTRY[key].schema = After
        with psycopg.connect(databases["admin_control"]) as conn:
            acknowledge(conn, rt.settings.schema_root)
        _, second = await bound(rt, key)
        await rt.execute(second["id"])
        assert rt.receipts(second["id"])["run"]["status"] == "succeeded"
        assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='schema_drift'", (second["id"],))["n"] == 0
        class Upstream(BaseModel):
            value: float
            owner_class_observed: str | None = None
        REGISTRY[key].schema = Upstream
        _, third = await bound(rt, key)
        await rt.execute(third["id"])
        alert = rt.db.one("SELECT subject_id FROM control.alert WHERE run_id=%s AND class='schema_drift'", (third["id"],))
        assert "widened value" in alert["subject_id"]
    finally:
        REGISTRY.pop(key, None)


@pytest.mark.parametrize("response", ["challenge", "retry_after"])
async def test_probe_cannot_block_collection_but_shares_request_cap(rt, source, databases, response):
    import asyncio
    import time

    from mdp_functions.fetch.hosts import host_limiter

    rt.settings = rt.settings.model_copy(update={"fixture": False, "dbt_cloud_verify": True})
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.host_health SET host_rps=10 WHERE host='resilience.invalid'")
        conn.execute("DELETE FROM control.target WHERE id<>(SELECT id FROM control.target ORDER BY id LIMIT 1)")
    target = rt.db.one("SELECT * FROM control.target")
    limiter = host_limiter("resilience.invalid", 10)
    calls = []

    def respond(request):
        calls.append(time.monotonic())
        if len(calls) == 1:
            if response == "challenge":
                return httpx.Response(403, text='<html><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/test"></script></html>')
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json={"ok": True})

    rt.transport = httpx.MockTransport(respond)
    original = dict(REGISTRY)
    REGISTRY.clear()
    REGISTRY[source] = original[source]
    health_before = rt.db.one("SELECT * FROM control.host_health WHERE host='resilience.invalid'")
    try:
        result = await probe_target(rt, target, "account")
    finally:
        REGISTRY.update(original)
    assert result["status"] == ("scrape_blocked" if response == "challenge" else "vendor_retryable")
    assert rt.db.one("SELECT * FROM control.host_health WHERE host='resilience.invalid'") == health_before
    assert limiter.not_before == 0 and limiter.factor == 1
    _, run = await bound(rt, source)
    await asyncio.wait_for(rt.execute(run["id"]), 5)
    assert rt.receipts(run["id"])["run"]["status"] == "succeeded"
    assert len(calls) == 2 and calls[1] - calls[0] >= .09


@pytest.mark.parametrize("size", [1, 3, 9])
async def test_parked_tenant_target_leaves_next_cycle_coverage(rt, databases, size):
    from uuid import uuid4

    from mdp_functions.targets import export_targets

    key = "test_small_tenant"
    tenant = uuid4()
    scope = f"tenant:{tenant}"

    @bronze(source_key=key, writes=["raw.small_tenant"], targets=Targets("account"),
            tenant_bound=True, hosts=["small.invalid"])
    async def collect(ctx, targets):
        for target in targets:
            await ctx.http.get("https://small.invalid/" + target["platform_account_id"])
            ctx.observed(1)
            yield {"value": target["platform_account_id"]}

    try:
        sync(rt.db)
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("INSERT INTO control.tenant(id,name,slug) VALUES (%s,'Fixture',%s)", (tenant, "fixture_" + tenant.hex))
            set_id = conn.execute("INSERT INTO control.target_set(kind,name,tenant_id) VALUES ('account','Fixture',%s) RETURNING id", (tenant,)).fetchone()[0]
            conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) SELECT %s,'fixture',n::text,'resolved',now() FROM generate_series(0,%s) n", (set_id, size - 1))
            conn.execute("INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('small:daily','core','daily',%s)", (scope,))
            conn.execute("INSERT INTO control.host_health(host,host_rps) VALUES ('small.invalid',10000) ON CONFLICT(host) DO UPDATE SET blocked_until=NULL")
        rt.transport = httpx.MockTransport(lambda req: httpx.Response(404 if req.url.path == '/0' else 200))

        async def run_cycle():
            dbt_id = "local:" + uuid4().hex
            binding = await rt.cycles.bind_cycle("daily", scope, dbt_id, "scheduled", "small:daily", runner="core")
            export_targets(rt.db, rt.warehouse, binding["cycle_id"], tenant_id=tenant)
            run = rt.admit(key, dbt_run_id=dbt_id, scope=scope)
            await rt.execute(run["id"])
            return run, rt.receipts(run["id"])

        first, failed = await run_cycle()
        assert failed["run"]["status"] == "failed"
        assert failed["receipts"][0]["targets_total"] == size
        with psycopg.connect(databases["admin_control"]) as conn:
            # The control API parks by setting this field. Its lifecycle tests prove the decision.
            conn.execute("UPDATE control.target SET deactivated_at=now() WHERE target_set_id=%s AND platform_account_id='0'", (set_id,))
        rt.cycles.close(first["cycle_id"])
        second, succeeded = await run_cycle()
        assert succeeded["run"]["status"] == "succeeded"
        receipt = succeeded["receipts"][0]
        assert receipt["targets_total"] == receipt["targets_succeeded"] == size - 1
        assert receipt["target_coverage"] == 1 and receipt["target_coverage_met"]
        assert rt.cycles.close(second["cycle_id"])["status"] == "closed"
        # Parking does not rewrite the earlier cycle's frozen membership or receipt.
        assert rt.receipts(first["id"])["receipts"][0]["targets_total"] == size
    finally:
        REGISTRY.pop(key, None)


@pytest.mark.parametrize('pause_at', ['admission', 'dispatch'])
async def test_advisory_probe_skips_a_disabled_source(rt, databases, source, monkeypatch, pause_at):
    from contextlib import asynccontextmanager

    from mdp_functions import target_probe
    from mdp_functions.fetch.hosts import HostLimiter

    monkeypatch.setattr(target_probe, 'REGISTRY', {source: REGISTRY[source]})
    def pause():
        with psycopg.connect(databases['admin_control']) as conn:
            conn.execute('UPDATE control.streamline SET enabled=false WHERE source_key=%s', (source,))
    if pause_at == 'admission':
        pause()
    else:
        permit = HostLimiter.permit
        @asynccontextmanager
        async def paused_permit(self, **kwargs):
            async with permit(self, **kwargs) as admitted:
                pause()
                yield admitted
        monkeypatch.setattr(HostLimiter, 'permit', paused_permit)
    calls = []
    rt.transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200))[1])
    target = rt.db.one('SELECT * FROM control.target ORDER BY id LIMIT 1')
    result = await target_probe.probe_target(rt, target, 'account')
    assert result['status'] == 'skipped' and calls == []
