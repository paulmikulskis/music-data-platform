"""Service callers validate samples without changing collection state."""

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import psycopg
import pytest
from conftest import bound, isolated_databases
from mdp_functions import canary
from mdp_functions.api import create_app
from mdp_functions.layers import Targets, bronze
from mdp_functions.registry import REGISTRY, sync
from psycopg import sql
from pydantic import BaseModel


class Record(BaseModel):
    value: int


@pytest.fixture(scope="module")
def databases():
    # Other source suites keep large warehouse inputs; probes compare their own clean pair.
    yield from isolated_databases()


@pytest.fixture
async def probe_source(rt, databases):
    @bronze(canary=True, source_key="test_canary", writes=["raw.canary_fixture"], schema=Record,
            targets=Targets("account"), hosts=["canary.invalid"])
    async def collect(ctx, targets):
        for target in targets:
            response = await ctx.http.get("https://canary.invalid/" + target["platform_account_id"])
            ctx.observed(1)
            ctx.set_cursor(target, {"done": True})
            ctx.record_completion(generation="whole-snapshot")
            ctx.alert("partial_coverage", "Fixture alert")
            yield response.json()
    sync(rt.db)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.target")
        conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) SELECT id,'fixture',n::text,'resolved',now() FROM control.target_set CROSS JOIN generate_series(0,58) n")
        conn.execute("INSERT INTO control.host_health(host,host_rps) VALUES ('canary.invalid',10000) ON CONFLICT(host) DO UPDATE SET host_rps=10000,blocked_until=NULL")
    try:
        yield "test_canary"
    finally:
        REGISTRY.pop("test_canary", None)


def stored_state(rt, databases, scheduled=False):
    result = {}
    for namespace, url in [("control", databases["admin_control"]), ("raw", databases["admin_warehouse"])]:
        with psycopg.connect(url) as conn:
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (namespace,)).fetchall()
            for (table,) in tables:
                # A running batch renews its lease independently of a probe.
                row = sql.SQL("to_jsonb(t) - 'heartbeat_at' - 'lease_expires_at'") if scheduled and namespace == "control" and table == "batch" else sql.SQL("to_jsonb(t)")
                digest = conn.execute(sql.SQL("SELECT md5(coalesce(string_agg(row::text,',' ORDER BY row::text),'')) FROM (SELECT {} AS row FROM {} t) q").format(row, sql.Identifier(namespace, table))).fetchone()[0]
                result[namespace + '.' + table] = digest
    root = Path(urlparse(rt.settings.dump_root).path)
    result["files"] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    return result


async def request_probe(rt, source, scope="global"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
                                base_url="http://test", headers={"Authorization": "Bearer " + rt.settings.service_token}) as client:
        response = await client.post(f"/v1/functions/{source}/probe", params={"scope": scope})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("body,status,error", [({"value": 1}, "passed", None), ({"value": "invalid"}, "failed", "schema_drift")])
async def test_probe_validates_large_sample_without_any_publication(rt, databases, probe_source, body, status, error):
    calls = []
    rt.transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json=body))[1])
    before = stored_state(rt, databases)
    result = await request_probe(rt, probe_source)
    assert result["status"] == status and result["error_class"] == error
    assert len(calls) == canary.SAMPLE_SIZE
    assert result["records_validated"] == (canary.SAMPLE_SIZE if status == "passed" else 0)
    assert stored_state(rt, databases) == before


async def test_probe_timeout_leaves_no_cleanup_work(rt, databases, probe_source, monkeypatch):
    entered = asyncio.Event()
    deadline = asyncio.timeout(None)
    async def respond(request):
        entered.set()
        # Expire the real timeout only once setup has reached the blocked request.
        deadline.reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()
    rt.transport = httpx.MockTransport(respond)
    monkeypatch.setattr(canary, "asyncio", SimpleNamespace(timeout=lambda _: deadline))
    before = stored_state(rt, databases)
    result = await asyncio.wait_for(request_probe(rt, probe_source), 30)
    assert entered.is_set() and result["status"] == "timed_out"
    assert result["error_class"] == "invoke_timeout"
    assert stored_state(rt, databases) == before


async def test_probe_and_scheduled_run_overlap_without_shared_locks_or_supersession(rt, databases, probe_source):
    entered, release = asyncio.Event(), asyncio.Event()
    original = REGISTRY[probe_source].function
    async def collecting(ctx, targets):
        if not ctx.http.dry_run:
            entered.set()
            await release.wait()
        async for row in original(ctx, targets):
            yield row
    REGISTRY[probe_source].function = collecting
    rt.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"value": 1}))
    _, run = await bound(rt, probe_source)
    task = asyncio.create_task(rt.execute(run["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        before = stored_state(rt, databases, scheduled=True)
        assert (await asyncio.wait_for(request_probe(rt, probe_source), 5))["status"] == "passed"
        assert stored_state(rt, databases, scheduled=True) == before
    finally:
        release.set()
        await task
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    assert rt.db.one("SELECT status FROM control.cycle WHERE id=%s", (run["cycle_id"],))["status"] == "open"
    # A later scheduled bind cannot preempt a probe: there is no probe cycle to find.
    assert len(rt.db.all("SELECT id FROM control.cycle")) == 1


async def test_not_due_weekly_probe_reads_no_vendor_and_changes_no_state(rt, databases):
    from datetime import UTC, datetime

    from psycopg.types.json import Jsonb
    with psycopg.connect(databases["admin_control"]) as conn:
        set_id = conn.execute("INSERT INTO control.target_set(kind,name) VALUES ('playlist','Fixture playlists') RETURNING id").fetchone()[0]
        target_id = conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) VALUES (%s,'apple_music','us:pl.fixture','resolved',now()) RETURNING id", (set_id,)).fetchone()[0]
        conn.execute("INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES (%s,'playlist','am:playlist:us:pl.fixture',%s)",
                     (target_id, Jsonb({"cadence": "weekly", "weekday_bucket": (datetime.now(UTC).weekday()+1)%7})))
    rt.transport = httpx.MockTransport(lambda _: pytest.fail("A not-due probe sends no request"))
    before = stored_state(rt, databases)
    assert (await request_probe(rt, "am_playlist_weekly"))["status"] == "not_due"
    assert stored_state(rt, databases) == before


@pytest.mark.parametrize("source", ["mb_spine", "jev_instrument_family"])
async def test_stateful_sources_skip_before_preparation_or_completion(rt, databases, source, monkeypatch):
    def forbidden(*args):
        pytest.fail("A stateful source must not run")
    monkeypatch.setattr(REGISTRY[source], "canary", True)
    monkeypatch.setattr(REGISTRY[source], "function", forbidden)
    monkeypatch.setattr(REGISTRY[source], "prepare", forbidden)
    before = stored_state(rt, databases)
    result = await request_probe(rt, source)
    assert result["status"] == "skipped"
    assert "completion state" in result["next_step"]
    assert stored_state(rt, databases) == before


async def test_scheduled_run_can_bind_during_probe_and_uses_shared_http_capacity(rt, databases, probe_source):
    # Two requests prove slot sharing; the large sample is covered separately.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.target WHERE id NOT IN (SELECT id FROM control.target ORDER BY id LIMIT 2)")
    entered, release, scheduled_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first = True
    async def respond(request):
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        else:
            scheduled_sent.set()
        return httpx.Response(200, json={"value": 1})
    rt.transport = httpx.MockTransport(respond)
    task = asyncio.create_task(request_probe(rt, probe_source))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        _, run = await bound(rt, probe_source)
        scheduled = asyncio.create_task(rt.execute(run["id"]))
        await asyncio.sleep(.05)
        assert not scheduled_sent.is_set()
    finally:
        release.set()
    assert (await task)["status"] == "passed"
    await asyncio.wait_for(scheduled, 10)
    assert scheduled_sent.is_set()
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    rt.cycles.close(run["cycle_id"])
    assert len(rt.db.all("SELECT id FROM control.cycle")) == 1


async def test_live_playlist_parser_validates_its_sample_without_landing(rt, databases):
    import json

    from mdp_functions.http import FixtureTransport
    from mdp_functions.settings import PACKAGE

    fixture = PACKAGE / "sources/am_playlist/fixtures/normal.jsonl"
    url = json.loads(fixture.read_text())["request"]["url"]
    with psycopg.connect(databases["admin_control"]) as conn:
        set_id = conn.execute("INSERT INTO control.target_set(kind,name) VALUES ('playlist','Fixture playlists') RETURNING id").fetchone()[0]
        conn.execute("INSERT INTO control.target(id,target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES ('00000000-0000-0000-0000-000000000001',%s,'apple_music',%s,%s,'resolved',now())",
                     (set_id, 'us:'+url.split('/')[-1], url.split('/')[-2]))
        conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) SELECT %s,'apple_music',n::text,'resolved',now() FROM generate_series(1,58) n", (set_id,))
    fixture_transport = FixtureTransport([fixture])
    async def public_request(request):
        assert request.method == "GET" and request.url.host == "music.apple.com"
        assert not any(name in request.headers for name in ("authorization", "x-api-key", "x-access-key"))
        assert not request.url.params
        return await fixture_transport.handle_async_request(request)
    rt.transport = httpx.MockTransport(public_request)
    before = stored_state(rt, databases)
    result = await request_probe(rt, "am_playlist")
    assert result["status"] == "passed", result
    assert result["records_validated"] == canary.SAMPLE_SIZE
    assert stored_state(rt, databases) == before


@pytest.mark.parametrize("status,error", [(404, "stale_target"), (429, "vendor_retryable"), (503, "vendor_retryable")])
async def test_transport_failure_keeps_shared_health_and_budgets_unchanged(rt, databases, probe_source, status, error):
    rt.transport = httpx.MockTransport(lambda _: httpx.Response(status, headers={"Retry-After": "60"}))
    before = stored_state(rt, databases)
    result = await request_probe(rt, probe_source)
    assert result["status"] == "failed" and result["error_class"] == error
    assert stored_state(rt, databases) == before


async def test_write_method_is_skipped_before_egress(rt, databases, probe_source, monkeypatch):
    async def write(ctx, targets):
        await ctx.http.post("https://canary.invalid/write", json={})
    monkeypatch.setattr(REGISTRY[probe_source], "function", write)
    rt.transport = httpx.MockTransport(lambda _: pytest.fail("A probe cannot send a write request"))
    before = stored_state(rt, databases)
    result = await request_probe(rt, probe_source)
    assert result["status"] == "skipped"
    assert stored_state(rt, databases) == before


async def test_metered_sources_skip_without_drawing_a_budget(rt, databases, probe_source):
    rt.settings.vendor_estimates = {probe_source: 1}
    rt.transport = httpx.MockTransport(lambda _: pytest.fail("A metered probe must not make a request"))
    before = stored_state(rt, databases)
    result = await request_probe(rt, probe_source)
    assert result["status"] == "skipped"
    assert "paid request accounting" in result["next_step"]
    assert stored_state(rt, databases) == before


@pytest.mark.parametrize("blocked", ["occupied", "queued", "backoff", "tokens", "health"])
async def test_probe_skips_unavailable_shared_host_capacity(rt, databases, probe_source, blocked):
    from mdp_functions.fetch.hosts import host_limiter

    limiter = host_limiter("canary.invalid", .001)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.host_health SET host_rps=.001 WHERE host='canary.invalid'")
    if blocked == "occupied":
        await limiter.semaphore.acquire()
    elif blocked == "queued":
        await limiter.lock.acquire()
    elif blocked == "backoff":
        limiter.throttle(60)
    elif blocked == "tokens":
        async with limiter.permit():
            pass
    else:
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("UPDATE control.host_health SET blocked_until=now()+interval '1 minute' WHERE host='canary.invalid'")
    rt.transport = httpx.MockTransport(lambda _: pytest.fail("A probe must not bypass shared host admission"))
    before = stored_state(rt, databases)
    try:
        for _ in range(2):
            result = await asyncio.wait_for(request_probe(rt, probe_source), 1)
            assert result["status"] == "skipped"
            assert "Shared host" in result["next_step"]
    finally:
        if blocked == "occupied":
            limiter.semaphore.release()
        if blocked == "queued":
            limiter.lock.release()
    assert stored_state(rt, databases) == before


async def test_probe_consumes_shared_token_and_respects_retry_after(rt, databases, probe_source):
    from mdp_functions.fetch.hosts import host_limiter

    calls = []
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.host_health SET host_rps=.001 WHERE host='canary.invalid'")
    rt.transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json={"value": 1}))[1])
    assert (await request_probe(rt, probe_source))["status"] == "passed"
    assert (await request_probe(rt, probe_source))["status"] == "skipped"
    limiter = host_limiter("canary.invalid", .001)
    assert limiter.tokens < 1 and len(calls) == 1
    # A received Retry-After is shared with the scheduled transport, too.
    limiter.tokens = 1
    rt.transport = httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "60"}))
    assert (await request_probe(rt, probe_source))["status"] == "failed"
    assert limiter.delay() > 0
    assert (await request_probe(rt, probe_source))["status"] == "skipped"


def test_production_opt_in_is_limited_to_public_sources():
    from mdp_functions.registry import discover
    discover()
    assert {key for key, manifest in REGISTRY.items() if manifest.canary} == PUBLIC_SOURCES
    for key in PUBLIC_SOURCES:
        manifest = REGISTRY[key]
        assert manifest.provider is None and manifest.transport == "direct"
        assert manifest.public()["canary"] is True


PUBLIC_SOURCES = {
    'am_playlist', 'am_playlist_weekly', 'sp_playlist', 'sp_playlist_weekly',
    'sz_chart', 'billboard_hot100', 'kexp_plays',
    'bc_tralbum', *('bc_' + kind + suffix for kind in ('daily_list', 'fan_playlist', 'discover', 'radio')
                   for suffix in ('', '_weekly')),
}


@pytest.mark.parametrize('source', sorted(PUBLIC_SOURCES - {'am_playlist', 'am_playlist_weekly'}))
async def test_public_source_probe_uses_real_parser_without_writes(rt, databases, source):
    import json
    from datetime import UTC, datetime

    from mdp_functions.http import FixtureTransport
    from mdp_functions.settings import PACKAGE
    from playlist_collector_fixture import TARGETS, target
    from psycopg.types.json import Jsonb

    key = source.removesuffix('_weekly')
    manifest = REGISTRY[source]
    member = None
    if key.startswith('bc_'):
        member = TARGETS[key]()
    elif key == 'sp_playlist':
        member = target('spotify', 'Synth1531769ee345db9a2')
    elif key == 'sp_artist_daily':
        member = target('spotify', '0FixtureActOneSpotify1')
    elif key == 'artist_discography':
        member = target('apple_music', '9609916580')
    elif key == 'sz_chart':
        from free_source_fixture import chart_targets_from
        from mdp_functions.settings import REPO
        member = chart_targets_from(REPO / 'inputs/chart_fixture_seed.csv')[0]
    scope = 'global'
    with psycopg.connect(databases['admin_control']) as conn:
        tenant_id = None
        if manifest.tenant_bound:
            tenant_id = conn.execute("INSERT INTO control.tenant(name,slug) VALUES ('Fixture',%s) RETURNING id", ('probe-'+source,)).fetchone()[0]
            scope = 'tenant:' + str(tenant_id)
        if member:
            set_id = conn.execute("INSERT INTO control.target_set(kind,name,tenant_id) VALUES (%s,'Public fixture',%s) RETURNING id", (manifest.targets.kind, tenant_id)).fetchone()[0]
            conn.execute("INSERT INTO control.target(id,target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES (%s,%s,%s,%s,%s,'resolved',now())", (member['id'], set_id, member.platform, member.platform_account_id, member.get('handle')))
            params = {**member.get('params_json', {}), 'cadence': 'weekly' if source.endswith('_weekly') else 'daily', 'weekday_bucket': datetime.now(UTC).weekday()}
            conn.execute("INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES (%s,%s,%s,%s)", (member['id'], manifest.targets.kind, 'fixture:'+source, Jsonb(params)))
        for host in manifest.hosts:
            conn.execute("INSERT INTO control.host_health(host,host_rps) VALUES (%s,10000) ON CONFLICT(host) DO UPDATE SET host_rps=10000,blocked_until=NULL", (host.removeprefix('*.'),))
    fixture_key = 'billboard' if key == 'billboard_hot100' else key
    fixture = PACKAGE / 'sources' / fixture_key / 'fixtures/normal.jsonl'
    transport = FixtureTransport([fixture])
    calls = []
    async def request(req):
        assert req.method == 'GET'
        assert not any(name in req.headers for name in ('authorization', 'x-api-key', 'x-access-key'))
        calls.append(req)
        if key == 'kexp_plays':
            # This endpoint's query follows the current clock; replay its recorded body.
            response = json.loads(fixture.read_text().splitlines()[0])['response']
            return httpx.Response(200, json=response['body'])
        return await transport.handle_async_request(req)
    rt.transport = httpx.MockTransport(request)
    before = stored_state(rt, databases)
    result = await request_probe(rt, source, scope)
    # POST-based public reads cannot cross the existing probe method guard.
    skipped = key in {'bc_discover', 'bc_radio', 'bc_fan_playlist'}
    assert result['status'] == ('skipped' if skipped else 'passed'), result
    assert len(calls) == 0 if key in {'bc_discover', 'bc_radio'} else len(calls) > 0
    assert stored_state(rt, databases) == before


@pytest.mark.parametrize('source', ['fixture_accounts', 'lb_sitewide', 'sc_playlist'])
async def test_keyed_and_metered_sources_remain_undeclared(rt, source):
    assert not REGISTRY[source].canary
    rt.transport = httpx.MockTransport(lambda _: pytest.fail('An excluded source sends no request'))
    assert (await request_probe(rt, source))['status'] == 'skipped'


@pytest.mark.parametrize('caller', ['http', 'direct'])
async def test_disabled_kexp_probe_skips_without_requests(rt, databases, caller):
    calls = []
    with psycopg.connect(databases['admin_control']) as conn:
        conn.execute("UPDATE control.streamline SET enabled=false WHERE source_key='kexp_plays'")
    rt.transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200))[1])
    before = stored_state(rt, databases)
    result = await (request_probe(rt, 'kexp_plays') if caller == 'http' else canary.probe(rt, 'kexp_plays'))
    assert result['status'] == 'skipped' and calls == []
    assert '/functions/kexp_plays' in result['next_step']
    assert stored_state(rt, databases) == before


@pytest.mark.parametrize('pause_at', ['collector', 'dispatch'])
async def test_probe_checks_pause_after_admission_and_before_dispatch(rt, databases, probe_source, monkeypatch, pause_at):
    from contextlib import asynccontextmanager

    from mdp_functions.fetch.hosts import HostLimiter

    calls = []
    def pause():
        with psycopg.connect(databases['admin_control']) as conn:
            conn.execute('UPDATE control.streamline SET enabled=false WHERE source_key=%s', (probe_source,))
    if pause_at == 'collector':
        original = REGISTRY[probe_source].function
        async def collect(ctx, targets):
            pause()
            async for row in original(ctx, targets):
                yield row
        monkeypatch.setattr(REGISTRY[probe_source], 'function', collect)
    else:
        permit = HostLimiter.permit
        @asynccontextmanager
        async def paused_permit(self, **kwargs):
            async with permit(self, **kwargs) as admitted:
                pause()
                yield admitted
        monkeypatch.setattr(HostLimiter, 'permit', paused_permit)
    rt.transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json={'value': 1}))[1])
    result = await request_probe(rt, probe_source)
    assert result['status'] == 'skipped' and calls == []


async def test_canary_alert_uses_catalog_runbook(rt):
    from mdp_functions.errors import error_hint

    async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
                                base_url="http://test", headers={"Authorization": "Bearer " + rt.settings.service_token}) as client:
        for _ in range(2):
            response = await client.post("/v1/alerts/canary_failed", json={"source_key": "am_playlist", "scope": "global"})
            assert response.status_code == 200, response.text
    alerts = rt.db.all("SELECT runbook_slug FROM control.alert WHERE class='source_canary_failed'")
    assert alerts == [{"runbook_slug": error_hint("source_canary_failed")["runbook"]}]
    assert alerts[0]["runbook_slug"] == "source-canary-failed"
