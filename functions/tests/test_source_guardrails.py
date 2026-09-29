"""Refusals happen before traffic, including alternate clients and hook rewrites."""

import asyncio
import socket
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient
from mdp_functions.fetch.guard import source_network
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx, bronze
from mdp_functions.registry import Manifest
from mdp_functions.settings import Settings
from mdp_functions.source_lint import check
from test_fetch_runtime import FakeDB, no_page


@pytest.mark.parametrize(
    "route",
    [
        "header",
        "empty",
        "request_jar",
        "client_jar",
        "set",
        "replace",
        "raw_jar",
        "hook",
    ],
)
async def test_cookies_refused_before_transport(route):
    sent = []

    async def hook(request):
        request.headers["Cookie"] = "visitor=fixture"

    kwargs = {"transport": httpx.MockTransport(lambda r: sent.append(r))}
    if route == "hook":
        kwargs["event_hooks"] = {"request": [hook]}
    with pytest.raises(ServiceError, match="Cookies are refused.*docs/operating"):
        if route == "client_jar":
            RefusingClient(cookies={"visitor": "fixture"}, **kwargs)
        else:
            async with RefusingClient(**kwargs) as client:
                if route == "set":
                    client.cookies.set("visitor", "fixture")
                elif route == "replace":
                    client.cookies = {"visitor": "fixture"}
                elif route == "raw_jar":
                    cookie = next(iter(httpx.Cookies({"visitor": "fixture"}).jar))
                    client.cookies.jar.set_cookie(cookie)
                else:
                    await client.get(
                        "https://public.example/",
                        **(
                            {
                                "headers": {
                                    "cOoKiE": ""
                                    if route == "empty"
                                    else "visitor=fixture"
                                }
                            }
                            if route in {"header", "empty"}
                            else {"cookies": {"visitor": "fixture"}}
                            if route == "request_jar"
                            else {}
                        ),
                    )
    assert sent == []


@pytest.mark.parametrize("layer", ["bronze", "silver", "universal", None])
@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "https://api.typesafe.ai/v1/systemone",
        "https://proxy.example/typesafe/v1/systemone",
        "https://litellm.internal/model",
        "https://custom.example/v1/responses",
        "https://gateway.example/custom-route",
    ],
)
async def test_non_gold_model_calls_are_refused(layer, url):
    async with RefusingClient(
        layer=layer,
        model_hosts=("gateway.example",),
        transport=httpx.MockTransport(lambda r: pytest.fail("sent")),
    ) as client:
        with pytest.raises(ServiceError, match="gold llm_step.*docs/operating"):
            await client.post(url)


async def test_gold_model_and_response_metadata_pass():
    async with RefusingClient(
        layer="gold",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                headers=[
                    ("Set-Cookie", "visitor=fixture"),
                    ("Set-Cookie", "consent=fixture"),
                ],
            )
        ),
    ) as client:
        response = await client.post("https://api.openai.com/v1/chat/completions")
        assert len(response.headers.get_list("set-cookie")) == 2
        assert not list(client.cookies.jar)


async def test_hook_model_rewrite_refused():
    async def hook(request):
        request.url = httpx.URL("https://api.openai.com/v1/chat/completions")
        request.headers["host"] = "api.openai.com"

    async with RefusingClient(
        layer="bronze",
        event_hooks={"request": [hook]},
        transport=httpx.MockTransport(lambda r: pytest.fail("sent")),
    ) as client:
        with pytest.raises(ServiceError, match="gold llm_step"):
            await client.get("https://public.example/")


@pytest.mark.parametrize("thread", [False, True])
async def test_raw_socket_clears_unrelated_lineage(thread):
    ctx = SimpleNamespace(request_id="previous-page")
    with (
        source_network(ctx),
        pytest.raises(ServiceError, match="ctx.http.*docs/operating"),
    ):
        if thread:
            await asyncio.to_thread(socket.socket)
        else:
            socket.socket()
    assert ctx.request_id is None
    # Runtime tasks outside source execution still work.
    with socket.socket():
        pass


async def test_own_httpx_client_is_refused():
    with (
        pytest.raises(ServiceError, match="ctx.http"),
        source_network(SimpleNamespace(request_id="previous-page")),
    ):
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get("http://127.0.0.1:1/")


async def test_traced_request_id_cannot_be_supplied_by_author(monkeypatch):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *a: None)
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4())}
    ctx = Ctx(
        Manifest(source_key="probe", layer="bronze", external=True, writes=[]), run
    )
    db = FakeDB()
    db.health = {"host_rps": 1000}
    async with TracedClient(
        ctx,
        db,
        run,
        no_page,
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    ) as client:
        response = await client.get(
            "https://public.example/", headers={"X-Request-ID": "unrelated"}
        )
        assert (
            response.request.headers["X-Request-ID"] == ctx.request_id == db.rows[0][4]
        )
        with pytest.raises(ServiceError):
            await client.get("https://public.example/", headers={"Cookie": "fixture"})
        assert ctx.request_id is None


def test_bronze_llm_declaration_refused():
    with pytest.raises(ServiceError, match="gold llm_step"):

        @bronze(source_key="bad_model", llm_step="bad_model", writes=[])
        async def bad(ctx):
            yield {}


@pytest.mark.parametrize(
    "statement",
    [
        "import httpx as client",
        "from requests import Session",
        "from urllib import request",
        "import socket",
        "from playwright.async_api import async_playwright",
        "import aiohttp",
        "from http import client",
    ],
)
def test_source_import_lint(tmp_path, statement):
    (tmp_path / "function.py").write_text(statement)
    assert len(check(tmp_path)) == 1


def test_existing_sources_pass_and_url_parsing_is_allowed(tmp_path):
    assert check() == []
    (tmp_path / "function.py").write_text("from urllib.parse import urlparse\n")
    assert check(tmp_path) == []


def test_local_setup_refuses_remote_and_verified_connections():
    from mdp_functions.llm_scaffold import local_url

    for settings, url in [
        (Settings(dbt_cloud_verify=True), "postgresql://localhost/control"),
        (Settings(dbt_cloud_verify=False), "postgresql://remote.invalid/control"),
    ]:
        with pytest.raises(ValueError, match="Local setup is refused"):
            local_url(url, settings)


@pytest.mark.parametrize(
    "settings", [Settings(fixture=True), Settings(dbt_cloud_verify=False)]
)
async def test_local_block_stops_this_client_but_not_next_run(monkeypatch, settings):
    from test_fetch_runtime import context

    monkeypatch.setattr("mdp_functions.http.draw", lambda *a: None)
    pauses = []
    monkeypatch.setattr(
        "mdp_functions.http.block_host", lambda *a: pauses.append(a[-1])
    )
    ctx, run = context()
    db = FakeDB()
    db.health = {"host_rps": 1000}
    sent = []

    def answer(request):
        sent.append(request)
        return httpx.Response(403, headers={"cf-mitigated": "challenge"})

    async with TracedClient(
        ctx, db, run, no_page, settings=settings, transport=httpx.MockTransport(answer)
    ) as client:
        for _ in range(2):
            with pytest.raises(ServiceError, match="blocked:cloudflare"):
                await client.get("https://blocked.test/")
    assert len(sent) == 1 and pauses == [0]
    async with TracedClient(
        ctx,
        db,
        run,
        no_page,
        settings=settings,
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    ) as client:
        assert (await client.get("https://blocked.test/")).status_code == 200


def test_scaffold_installs_matching_hashes_and_budget(databases, monkeypatch, tmp_path):
    import psycopg
    from mdp_functions.llm import params_hash, step_version
    from mdp_functions.llm_scaffold import scaffold
    from psycopg.rows import dict_row

    monkeypatch.setenv("MDP_CONTROL_DATABASE_URL", databases["admin_control"])
    key = "fixture_" + uuid4().hex
    version = scaffold(key, Settings(dbt_cloud_verify=False), tmp_path)
    with psycopg.connect(databases["admin_control"], row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM control.llm_step WHERE source_key=%s", (key,)
        ).fetchone()
        assert row["params_hash"] == params_hash(row["params"])
        assert (
            version
            == row["step_version"]
            == step_version("local-stub", 1, row["params_hash"])
        )
        assert (
            conn.execute(
                "SELECT count(*) n FROM control.budget WHERE scope='llm_step' AND scope_id=%s",
                (row["id"],),
            ).fetchone()["n"]
            == 1
        )
    assert (tmp_path / "control/prompts" / key / "1.md").is_file()
    assert (
        tmp_path / "functions/src/mdp_functions/sources" / key / "fixtures/llm.json"
    ).is_file()
    with pytest.raises(ValueError, match="already exists"):
        scaffold(key, Settings(dbt_cloud_verify=False), tmp_path)


async def test_local_model_stub_needs_no_key(tmp_path):
    import json

    from mdp_functions.llm_scaffold import LocalModel

    path = tmp_path / "llm.json"
    path.write_text(json.dumps({"responses": ["first", "second"]}))
    async with RefusingClient(layer="gold", transport=LocalModel(path)) as client:
        for n, answer in enumerate(["first", "second"]):
            response = await client.post(
                "https://local-model.invalid/chat/completions",
                headers={"x-litellm-call-id": str(n)},
            )
            assert response.json()["choices"][0]["message"]["content"] == answer
            assert response.json()["id"] == str(n)
            assert "authorization" not in response.request.headers


async def test_socket_opened_before_source_cannot_send():
    left, right = socket.socketpair()
    try:
        with (
            source_network(SimpleNamespace(request_id="old")),
            pytest.raises(ServiceError, match="ctx.http"),
        ):
            left.send(b"untraced")
    finally:
        left.close()
        right.close()


def test_source_thread_inherits_socket_refusal():
    import threading

    errors = []

    def worker():
        try:
            socket.socket()
        except ServiceError as exc:
            errors.append(exc.error_class)

    with source_network(SimpleNamespace(request_id="old")):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
    assert errors == ["egress_blocked"]


async def test_transport_can_connect_from_guarded_source(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from test_fetch_runtime import context

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"public fixture")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("mdp_functions.http.draw", lambda *a: None)
    ctx, run = context()
    try:
        async with TracedClient(ctx, FakeDB(), run, no_page) as client:
            ctx.http = client
            with source_network(ctx):
                response = await client.get(f"http://127.0.0.1:{server.server_port}/")
                assert response.text == "public fixture"
                assert await asyncio.to_thread(lambda: 42) == 42
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


async def test_runtime_records_own_client_refusal(rt):
    from conftest import bound
    from mdp_functions.registry import REGISTRY, sync

    @bronze(source_key="network_probe", writes=["raw.network_probe"], cadence="daily")
    async def probe(ctx):
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get("http://127.0.0.1:1/")
        ctx.observed(1)
        yield {"id": "untraced"}

    try:
        sync(rt.db)
        _, run = await bound(rt, "network_probe")
        await rt.execute(run["id"])
        result = rt.db.one(
            "SELECT status,error_class,rows_written FROM control.run WHERE id=%s",
            (run["id"],),
        )
        assert result == {
            "status": "failed",
            "error_class": "egress_blocked",
            "rows_written": 0,
        }
    finally:
        REGISTRY.pop("network_probe")


def test_host_unpause_clears_only_local_host(databases, monkeypatch):
    import psycopg
    from mdp_functions.cli import host_unpause

    monkeypatch.setenv("MDP_CONTROL_URL", databases["control_url"])
    monkeypatch.setenv("MDP_DBT_CLOUD_VERIFY", "false")
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.host_health(host,blocked_until,last_signature) VALUES ('fixture.invalid',now()+interval '1 hour','cloudflare')"
        )
    host_unpause("fixture.invalid")
    with psycopg.connect(databases["admin_control"]) as conn:
        assert conn.execute(
            "SELECT blocked_until,last_signature FROM control.host_health WHERE host='fixture.invalid'"
        ).fetchone() == (None, None)



def test_run_command_prints_the_refusal_and_allowed_path(monkeypatch):
    from mdp_functions.cli import app
    from mdp_functions.fetch.forbidden import COOKIE_MESSAGE
    from typer.testing import CliRunner
    async def failed(*args):
        return {"receipts": [], "run": {"status": "failed", "coverage": "partial", "error_class": "forbidden_path", "error_message": COOKIE_MESSAGE}}
    monkeypatch.setattr("mdp_functions.cli.local_run", failed)
    result = CliRunner().invoke(app, ["run", "probe", "--fixture"])
    assert result.exit_code == 1
    assert "Cookies are refused" in result.output
    assert "docs/operating.md#what-the-platform-refuses" in result.output



async def test_gold_accounting_can_read_its_model_gateway(monkeypatch):
    from mdp_functions import costsync
    monkeypatch.setattr(costsync, "mirror_costs", lambda rt: None)
    monkeypatch.setattr(costsync, "RefusingClient", lambda **kw: RefusingClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])), **kw,
    ))
    runtime = SimpleNamespace(settings=Settings(litellm_base_url="https://litellm.example/v1", litellm_admin_key="fixture"))
    assert await costsync.reconcile(runtime) == {"reconciled": 0}


@pytest.mark.parametrize("layer", ["bronze", "silver", "universal", "gold"])
async def test_source_layer_overrides_service_model_client(layer):
    ctx = SimpleNamespace(manifest=SimpleNamespace(layer=layer), request_id=None)
    async with RefusingClient(
        layer="gold", transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        with source_network(ctx):
            if layer == "gold":
                assert (await client.post("https://api.typesafe.ai/v1/systemone")).status_code == 200
            else:
                with pytest.raises(ServiceError, match="gold llm_step"):
                    await client.post("https://api.typesafe.ai/v1/systemone")
