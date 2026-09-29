"""Fetch policy regressions, including actual runtime-role writes and frozen retries."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from time import monotonic
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.control_db import block_host, envelope_miss
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.detectors import classify, retry_after, signature
from mdp_functions.fetch.hosts import DEFAULT_USER_AGENT, HostLimiter
from mdp_functions.fetch.providers import Webshare, market_country, provider_scope_id
from mdp_functions.http import FixtureTransport, TracedClient
from mdp_functions.layers import Ctx, Target, Targets, bronze
from mdp_functions.registry import REGISTRY, Manifest, sync
from mdp_functions.settings import Settings
from pydantic import SecretStr


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (200, "ok"),
        (304, "ok"),
        (404, "gone"),
        (410, "gone"),
        (403, "client_error"),
        (429, "throttled"),
        (503, "retryable"),
        (408, "retryable"),
    ],
)
def test_classify(status, kind):
    assert classify(httpx.Response(status)).kind == kind


@pytest.mark.parametrize(
    ("sig", "status", "headers", "body"),
    [
        ("cloudflare", 403, {"CF-Mitigated": "challenge"}, b""),
        ("cloudflare", 200, {}, b'<script src="/cdn-cgi/challenge-platform/a.js">'),
        ("akamai", 403, {}, b"Access Denied https://errors.edgesuite.net/123"),
        ("datadome", 200, {}, b"https://geo.captcha-delivery.com/captcha"),
        ("perimeterx", 403, {}, b'<div id="px-captcha">'),
        ("imperva", 403, {}, b"Incapsula incident ID: 1234"),
        ("aws_waf", 202, {"X-Amzn-Waf-Action": "challenge"}, b""),
    ],
)
def test_detectors(sig, status, headers, body):
    response = httpx.Response(status, headers=headers, content=body)
    assert classify(response).kind == "blocked"
    assert classify(response).signature == sig
    assert signature(status, {}, b"x" * 65536 + body) is None


def test_shell_requires_missing_envelope_and_cdn_headers_are_not_blocks():
    response = httpx.Response(
        200,
        headers={"Server": "cloudflare", "CF-Ray": "123"},
        text='<div id="root"></div>',
    )
    assert classify(response).kind == "ok"
    assert classify(response, envelope_missing=True).signature == "soft_shell"
    assert (
        classify(httpx.Response(200, json={"data": []}), envelope_missing=True).kind
        == "ok"
    )


def test_retry_after_date_and_integer():
    now = datetime(2026, 9, 23, tzinfo=UTC)
    assert retry_after("120", now=now) == 120
    assert retry_after("Wed, 23 Sep 2026 00:02:00 GMT", now=now) == 120
    assert retry_after("Tue, 22 Sep 2026 00:00:00 GMT", now=now) == 0
    assert retry_after("invalid") is None


async def test_host_limiter_spaces_requests_and_limits_concurrency():
    limiter = HostLimiter(50)
    times, active = [], 0

    async def request():
        nonlocal active
        async with limiter.permit():
            active += 1
            assert active == 1
            times.append(monotonic())
            await asyncio.sleep(0.001)
            active -= 1

    await asyncio.gather(*(request() for _ in range(3)))
    assert all(b - a >= 0.017 for a, b in pairwise(times))
    limiter.throttle(0.04)
    start = monotonic()
    async with limiter.permit():
        assert monotonic() - start >= 0.035
    assert limiter.factor == 0.5


@pytest.mark.parametrize(
    "params", [{}, {"market": ""}, {"country": "GB"}, {"storefront": "not-a-country"}]
)
def test_market_required(params):
    with pytest.raises(ServiceError, match="market or storefront"):
        market_country(params, "GB")


async def test_webshare_caches_config_pins_sessions_and_redacts(caplog):
    caplog.set_level("DEBUG")
    calls = []

    def config(request):
        calls.append(request.url)
        assert request.headers["Authorization"] == "Token secret-key"
        return httpx.Response(
            200, json={"username": "proxyuser", "password": "proxysecret"}
        )

    provider = Webshare(SecretStr("secret-key"), transport=httpx.MockTransport(config))
    first = await provider.proxy_url("run", "target", "gb")
    again = await provider.proxy_url("run", "target", "gb")
    other = await provider.proxy_url("run", "other", "gb")
    assert (
        first.get_secret_value() == again.get_secret_value() != other.get_secret_value()
    )
    assert len(calls) == 1 and "-gb-" in first.get_secret_value()
    assert "proxysecret" not in repr(first)
    assert provider.redact(first.get_secret_value()) == "[redacted]"
    assert "secret-key" not in caplog.text and "proxysecret" not in caplog.text


async def no_page():
    pass


class FakeDB:
    def __init__(self):
        self.health = {}
        self.knobs = {}
        self.rows = []

    def one(self, query, params=()):
        if "cost_ledger" in query:
            return {"n": 0, "bytes": 0}
        return self.health if "host_health" in query else self.knobs

    def execute(self, query, params=()):
        self.rows.append(params)

    def all(self, query, params=()):
        return (
            [
                {
                    "period": "monthly",
                    "cap_bytes": 1000000,
                    "cap_cents": 9999,
                    "hard_action": "pause",
                }
            ]
            if "budget" in query
            else []
        )


def context(**options):
    run = {
        "id": str(uuid4()),
        "cycle_id": str(uuid4()),
        "streamline_id": str(uuid4()),
        "tenant_id": None,
    }
    return Ctx(Manifest("test_fetch", "bronze", [], **options), run), run


async def test_allowlist_covers_redirects_and_default_is_compatible(monkeypatch):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    calls = []

    def transport(request):
        calls.append(request.url.host)
        assert request.headers["user-agent"] == DEFAULT_USER_AGENT
        return httpx.Response(302, headers={"Location": "https://forbidden.test/"})

    ctx, run = context(hosts=["allowed.test"])
    async with TracedClient(
        ctx,
        FakeDB(),
        run,
        no_page,
        transport=httpx.MockTransport(transport),
        follow_redirects=True,
    ) as client:
        with pytest.raises(ServiceError, match="allowlist"):
            await client.get("https://allowed.test/")
    assert calls == ["allowed.test"]
    ctx, run = context()
    async with TracedClient(
        ctx, FakeDB(), run, no_page, transport=httpx.MockTransport(transport)
    ) as client:
        assert (await client.get("https://any.test/")).status_code == 302


async def test_block_pauses_host_without_second_attempt(monkeypatch):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    db, calls = FakeDB(), []

    def block(db, run, host, sig, seconds):
        db.health = {
            "blocked_until": datetime.now(UTC) + timedelta(seconds=seconds),
            "last_signature": sig,
            "host_rps": 1000,
        }

    monkeypatch.setattr("mdp_functions.http.block_host", block)
    ctx, run = context()

    def response(request):
        calls.append(request)
        return httpx.Response(403, headers={"cf-mitigated": "challenge"})

    async with TracedClient(
        ctx, db, run, no_page, transport=httpx.MockTransport(response)
    ) as client:
        for _ in range(2):
            with pytest.raises(ServiceError, match="blocked:cloudflare"):
                await client.get("https://blocked.test/")
    assert len(calls) == 1 and len(db.rows) == 1
    assert [r["reason"] for r in ctx.rejected] == ["blocked:cloudflare"] * 2


async def test_residential_is_explicit_market_only_and_credentials_never_persist(
    monkeypatch, caplog
):
    caplog.set_level("DEBUG")
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    provider = Webshare(
        SecretStr("key"),
        Decimal("0.2"),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"username": "secretuser", "password": "secretpass"}
            )
        ),
    )
    ctx, run = context(transport="residential", keep_payload=True)
    db = FakeDB()
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, text="echo secretuser secretpass")

    async with TracedClient(
        ctx,
        db,
        run,
        no_page,
        provider=provider,
        transport=httpx.MockTransport(response),
    ) as client:
        monkeypatch.setattr(client, "_bytes_cost", lambda *args: None)
        with pytest.raises(ServiceError, match="market or storefront"):
            await client.get("https://market.test/")
        assert not calls
        ctx.target = Target(params_json={"market": "GB"})
        result = await client.get("https://market.test/")
        assert result.text == "echo [redacted] [redacted]"
    retained = repr(ctx.payloads) + repr(db.rows) + caplog.text
    assert "secretuser" not in retained and "secretpass" not in retained
    assert len(calls) == 1 and db.rows[0][9:11] == ("fixture", None)
    assert "proxy-authorization" not in calls[0].headers


async def test_fixture_scenario(tmp_path):
    for scenario, status in [("normal", 200), ("blocked-cloudflare", 403)]:
        (tmp_path / f"{scenario}.jsonl").write_text(
            json.dumps(
                {
                    "request": {"method": "GET", "url": "https://fixture.test/"},
                    "response": {"status": status, "body": ""},
                }
            )
            + "\n"
        )
    settings = Settings(fixture_scenario="blocked-cloudflare")
    async with httpx.AsyncClient(
        transport=FixtureTransport(
            list(tmp_path.glob(f"{settings.fixture_scenario}*.jsonl"))
        )
    ) as client:
        assert (await client.get("https://fixture.test/")).status_code == 403


@pytest.mark.docker
async def test_frozen_spec_survives_live_edit_and_retry(rt, databases):
    seen = []
    fail = True

    @bronze(
        source_key="frozen_fetch_test",
        writes=["raw.frozen_fetch_test"],
        targets=Targets("account"),
    )
    async def collect(ctx, targets):
        seen.append((targets[0].params_json, targets[0].handle))
        if fail:
            raise ServiceError("vendor_retryable", "temporary")
        ctx.observed(1)
        yield {"market": targets[0].params_json["market"]}

    try:
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute(
                "INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) SELECT id,'playlist',platform_account_id,'{\"market\":\"GB\"}' FROM control.target"
            )
        sync(rt.db)
        dbt_id, run = await bound(rt, "frozen_fetch_test")
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute(
                'UPDATE control.target_spec SET params_json=\'{"market":"US"}\''
            )
            conn.execute("UPDATE control.target SET handle='changed'")
        await rt.execute(run["id"])
        assert rt.receipts(run["id"])["run"]["status"] == "failed"
        fail = False
        retry = rt.admit("frozen_fetch_test", dbt_run_id=dbt_id)
        await rt.execute(retry["id"])
        assert rt.receipts(run["id"])["run"]["status"] == "succeeded"
        assert len(seen) == 4
        assert all(
            params == {"market": "GB"} and handle != "changed"
            for params, handle in seen
        )
    finally:
        REGISTRY.pop("frozen_fetch_test", None)


@pytest.mark.docker
async def test_three_distinct_misses_pause_streamline_and_host_grants(rt, databases):
    _, run = await bound(rt)
    targets = rt.db.all("SELECT id FROM control.target")
    for _ in range(3):
        envelope_miss(
            rt.db, str(run["id"]), str(targets[0]["id"]), "embed", "data.items"
        )
    assert not rt.db.one(
        "SELECT id FROM control.alert WHERE class='surface_drift' AND run_id=%s",
        (run["id"],),
    )
    for target in (targets[1]["id"], uuid4()):
        envelope_miss(rt.db, str(run["id"]), str(target), "embed", "data.items")
    assert (
        rt.db.one(
            "SELECT enabled FROM control.streamline WHERE id=%s",
            (run["streamline_id"],),
        )["enabled"]
        is False
    )
    assert (
        rt.db.one(
            "SELECT severity FROM control.alert WHERE class='surface_drift' AND run_id=%s",
            (run["id"],),
        )["severity"]
        == "critical"
    )
    block_host(rt.db, str(run["id"]), "grant.test", "cloudflare")
    assert (
        rt.db.one(
            "SELECT last_signature FROM control.host_health WHERE host='grant.test'"
        )["last_signature"]
        == "cloudflare"
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rt.db.execute(
            "UPDATE control.host_health SET host_rps=100 WHERE host='grant.test'"
        )
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("SET ROLE control_rt")
        conn.execute(
            "UPDATE control.host_health SET robots_policy='override',robots_override_by='owner: approved public JSON' WHERE host='grant.test'"
        )
    assert (
        rt.db.one("SELECT actor FROM control.audit_log WHERE subject='grant.test'")[
            "actor"
        ]
        == "owner: approved public JSON"
    )
    with (
        psycopg.connect(databases["admin_control"]) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("SET ROLE control_rt")
        conn.execute(
            "UPDATE control.host_health SET blocked_until=NULL WHERE host='grant.test'"
        )


@pytest.mark.docker
async def test_provider_budget_stops_only_market_transport_and_bytes_persist(
    rt, databases
):
    _, run = await bound(rt)
    ctx = Ctx(REGISTRY["fixture_accounts"], run)
    provider = Webshare(SecretStr("unused"), Decimal("0.5"))
    async with TracedClient(ctx, rt.db, run, no_page, provider=provider) as client:
        client._bytes_cost("proxy-byte-test", 123)
        cost = rt.db.one(
            "SELECT * FROM control.cost_ledger WHERE provider_request_id='proxy-byte-test'"
        )
        assert (
            cost["unit"] == "byte"
            and cost["quantity"] == 123
            and cost["cost_microcents"] == 62
        )
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute(
                "INSERT INTO control.budget(scope,scope_id,period,cap_cents,soft_pct,hard_action) VALUES ('provider',%s,'daily',0,80,'pause')",
                (provider_scope_id("webshare"),),
            )
        with pytest.raises(ServiceError, match="Provider budget exhausted"):
            client._provider_budget()
        # Direct calls never consult this provider scope.
        rt.transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": []})
        )
        assert ctx.manifest.transport == "direct"


def test_require_returns_values_or_rejects_with_surface_path():
    ctx, _ = context()
    assert ctx.require({"data": {"items": []}}, "data.items", "embed") == []
    assert ctx.require({"data": [{"a/b": 3}]}, "/data/0/a~1b", "embed") == 3
    with pytest.raises(ServiceError, match="envelope_mismatch:embed:data.items"):
        ctx.require({"data": {}}, "data.items", "embed")
    assert ctx.observed_count == len(ctx.rejected) == 1
    assert ctx.rejected[0]["reason"] == "envelope_mismatch:embed:data.items"


async def test_require_shell_blocks_instead_of_counting_drift(monkeypatch):
    db = FakeDB()
    ctx, run = context()
    blocked = []
    monkeypatch.setattr(
        "mdp_functions.http.block_host", lambda *args: blocked.append(args)
    )
    monkeypatch.setattr(
        "mdp_functions.http.envelope_miss",
        lambda *args: pytest.fail("A detected shell must not count as drift"),
    )
    async with TracedClient(ctx, db, run, no_page) as client:
        ctx.http = client
        client._last_host = "shell.test"
        client._last_response = httpx.Response(200, text='<div id="root"></div>')
        with pytest.raises(ServiceError, match="blocked:soft_shell"):
            ctx.require({}, "data", "embed")
    assert len(blocked) == 1 and ctx.rejected[0]["reason"] == "blocked:soft_shell"


async def test_h1_knob_user_agent_and_direct_market_never_use_provider(monkeypatch):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    ctx, run = context()
    ctx.target = Target(params_json={"market": "GB"})
    db = FakeDB()
    db.health = {
        "http_version": "1.1",
        "user_agent": "MusicDataPlatform/test (operator@example.invalid)",
        "host_rps": 1000,
    }
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async with TracedClient(
        ctx, db, run, no_page, transport=httpx.MockTransport(response)
    ) as client:

        async def fail_proxy(*args):
            pytest.fail("A market alone must never select a proxy")

        monkeypatch.setattr(client.provider, "proxy_url", fail_proxy)
        result = await client.get("https://h1.test/")
        assert result.status_code == 200
    assert seen[0].headers["User-Agent"] == db.health["user_agent"]
    assert db.rows[0][9:11] == ("fixture", None)


async def test_transport_failure_cannot_leak_proxy_credentials(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    provider = Webshare(
        SecretStr("key"),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"username": "secretuser", "password": "secretpass"}
            )
        ),
    )
    ctx, run = context(transport="residential", keep_payload=True)
    ctx.target = Target(params_json={"storefront": "GB"})
    db = FakeDB()
    db.health = {"host_rps": 1000}

    def fail(request):
        raise httpx.ProxyError(
            "Cannot connect http://secretuser:secretpass@proxy.invalid"
        )

    async with TracedClient(
        ctx,
        db,
        run,
        no_page,
        backoff_s=0,
        provider=provider,
        transport=httpx.MockTransport(fail),
    ) as client:
        monkeypatch.setattr(client, "_bytes_cost", lambda *args: None)
        with pytest.raises(ServiceError) as error:
            await client.get("https://proxy-failure.test/")
    assert str(error.value) == "ProxyError"
    retained = repr(db.rows) + repr(ctx.payloads) + caplog.text + str(error.value)
    assert "secretuser" not in retained and "secretpass" not in retained
    assert len(db.rows) == 4


@pytest.mark.docker
async def test_runtime_three_envelope_targets_pause_streamline(rt, databases):
    @bronze(
        source_key="drift_fetch_test",
        writes=["raw.drift_fetch_test"],
        targets=Targets("account"),
    )
    async def collect(ctx, targets):
        ctx.require({}, "data.items", "embed")

    try:
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute(
                "INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) SELECT id,'fixture','acceptance-003','resolved',now() FROM control.target_set"
            )
        sync(rt.db)
        _, run = await bound(rt, "drift_fetch_test")
        await rt.execute(run["id"])
        assert (
            rt.db.one(
                "SELECT enabled FROM control.streamline WHERE id=%s",
                (run["streamline_id"],),
            )["enabled"]
            is False
        )
        assert (
            rt.db.one(
                "SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='surface_drift' AND severity='critical'",
                (run["id"],),
            )["n"]
            == 1
        )
        assert rt.receipts(run["id"])["run"]["rows_rejected"] == 3
    finally:
        REGISTRY.pop("drift_fetch_test", None)


async def test_provider_configuration_cache_is_shared_across_batches():
    from mdp_functions.fetch.providers import webshare_provider

    first = webshare_provider(SecretStr("cache-key"), Decimal("0.2"))
    assert first is webshare_provider(SecretStr("cache-key"), Decimal("0.2"))
    assert first is not webshare_provider(SecretStr("different-key"), Decimal("0.2"))
    assert "cache-key" not in repr(first)


def test_client_construction_before_event_loop_is_supported():
    ctx, run = context()
    client = TracedClient(ctx, FakeDB(), run, no_page)
    assert client.provider.name == "webshare"
    asyncio.run(client.aclose())
