"""Source refusals must not leak into pooled jobs or the runtime's own I/O."""

import asyncio
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx
import pytest
from mdp_functions.derived import call_function
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient
from mdp_functions.fetch.guard import source_network
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx, universal
from mdp_functions.registry import Manifest
from mdp_functions.settings import Settings
from mdp_functions.store import make_store
from test_fetch_runtime import FakeDB, no_page


@pytest.mark.parametrize("prewarm", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_executor_context_stays_with_job(prewarm, fail):
    async def run():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        if prewarm:
            await loop.run_in_executor(None, lambda: None)
        ctx = SimpleNamespace(request_id="previous-page")

        def job():
            if fail:
                raise ValueError("job failed")

        with source_network(ctx):
            if fail:
                with pytest.raises(ValueError, match="job failed"):
                    await asyncio.to_thread(job)
            else:
                await asyncio.to_thread(job)
        # Reuse that exact worker without the source context.
        with await loop.run_in_executor(None, socket.socket):
            pass
        assert ctx.request_id == "previous-page"
        # A guarded job on an already warm worker still refuses its own client.
        with source_network(ctx), pytest.raises(ServiceError, match="ctx.http"):
            await loop.run_in_executor(None, socket.socket)
        with await loop.run_in_executor(None, socket.socket):
            pass

    asyncio.run(run())


@pytest.fixture
def runtime_http():
    objects, calls = {}, []

    class Handler(BaseHTTPRequestHandler):
        def respond(self, data=b"", content_type="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_PUT(self):
            objects[urlparse(self.path).path] = self.rfile.read(
                int(self.headers["Content-Length"])
            )
            self.respond()

        def do_GET(self):
            path = urlparse(self.path)
            if "list-type" in parse_qs(path.query):
                keys = "".join(
                    f"<Contents><Key>{escape(k.removeprefix('/bucket/'))}</Key></Contents>"
                    for k in objects
                )
                self.respond(
                    f"<ListBucketResult><IsTruncated>false</IsTruncated>{keys}</ListBucketResult>".encode(),
                    "application/xml",
                )
            else:
                self.respond(objects[path.path], "application/octet-stream")

        def do_HEAD(self):
            self.respond()

        def do_DELETE(self):
            objects.pop(urlparse(self.path).path, None)
            self.respond()

        def do_POST(self):
            calls.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.respond(b'{"alert_id":"fixture-alert","ok":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    settings = Settings(
        dump_root="s3://bucket/root",
        r2_endpoint=url,
        r2_access_key_id="fixture",
        r2_secret_access_key="fixture",
    )
    try:
        yield settings, objects, calls
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_s3_store_owns_its_network_boundary(runtime_http):
    settings, objects, _ = runtime_http
    ctx = SimpleNamespace(request_id="previous-page")
    with source_network(ctx):
        store = make_store(settings)
        store.health()
        store.put("page.json", b'{"id":1}')
        assert store.get("page.json") == b'{"id":1}'
        assert store.list("") == ["page.json"]
        store.delete("page.json")
        assert objects == {}
        assert ctx.request_id == "previous-page"
        with pytest.raises(ServiceError, match="ctx.http"):
            socket.socket()


async def test_runtime_response_stream_permits_io_only_while_advancing():
    closed = []

    class Stream(httpx.AsyncByteStream):
        def __aiter__(self):
            with socket.socket():
                pass
            return self.chunks()

        async def chunks(self):
            for chunk in (b"first", b"second"):
                with socket.socket():
                    pass
                yield chunk

        async def aclose(self):
            with socket.socket():
                pass
            closed.append("stream")

    class Transport(httpx.MockTransport):
        async def aclose(self):
            with socket.socket():
                pass
            closed.append("transport")

    ctx = SimpleNamespace(
        request_id="previous-page", manifest=SimpleNamespace(layer="bronze")
    )
    with source_network(ctx):
        async with (
            RefusingClient(
                transport=Transport(lambda r: httpx.Response(200, stream=Stream()))
            ) as client,
            client.stream("GET", "https://public.example/") as response,
        ):
            chunks = []
            async for chunk in response.aiter_raw():
                chunks.append(chunk)
                with pytest.raises(ServiceError, match="ctx.http"):
                    socket.socket()
    assert chunks == [b"first", b"second"]
    assert closed == ["stream", "transport"]


@pytest.mark.parametrize("fail", [False, True])
async def test_direct_before_page_owns_its_flush(runtime_http, fail):
    settings, objects, _ = runtime_http
    store = make_store(settings)

    async def flush():
        # Runtime work can also open a connection before it reaches the dump store.
        with socket.socket():
            pass
        await asyncio.to_thread(store.put, "page.json", b'{"id":1}')
        if fail:
            raise ValueError("flush failed")

    async def source(ctx, rows):
        if fail:
            with pytest.raises(ValueError, match="flush failed"):
                await ctx.http.before_page()
        else:
            await ctx.http.before_page()
        assert ctx.request_id == "previous-page"
        with pytest.raises(ServiceError, match="ctx.http"):
            socket.socket()

    run = {"id": str(uuid4()), "cycle_id": str(uuid4())}
    ctx = Ctx(
        Manifest(
            source_key="flush_probe", layer="universal", function=source, writes=[]
        ),
        run,
    )
    ctx.request_id = "previous-page"
    async with TracedClient(ctx, FakeDB(), run, flush, settings=settings) as client:
        ctx.http = client
        await call_function(ctx, [])
    assert objects["/bucket/root/page.json"] == b'{"id":1}'


async def test_runtime_flush_lands_s3_dumps(rt, runtime_http):
    from conftest import bound
    from mdp_functions.registry import REGISTRY, sync

    settings, objects, _ = runtime_http
    rt.store = rt.dumps.store = make_store(settings)

    @universal(
        source_key="s3_" + "flush_probe", writes=["raw.s3_flush_probe"], cadence="daily"
    )
    async def probe(ctx, rows):
        for n in range(2):
            ctx.observed(1)
            ctx.yield_row({"id": n})
            await ctx.http.before_page()

    try:
        sync(rt.db)
        _, run = await bound(rt, "s3_flush_probe")
        await rt.execute(run["id"])
        result = rt.db.one(
            "SELECT status,error_class,rows_written FROM control.run WHERE id=%s",
            (run["id"],),
        )
        assert result == {"status": "succeeded", "error_class": None, "rows_written": 2}
        assert any(key.endswith("manifest.json") for key in objects)
        dumps = rt.db.all(
            "SELECT uri_prefix FROM control.dump WHERE run_id=%s", (run["id"],)
        )
        assert dumps and all(
            row["uri_prefix"].startswith("s3://bucket/root/") for row in dumps
        )
    finally:
        REGISTRY.pop("s3_flush_probe")


async def test_runtime_control_and_service_calls_keep_source_lineage(
    runtime_http, monkeypatch, tmp_path
):
    from mdp_functions.core_gate import report_cadence_failed
    from mdp_functions.promoter import ControlTargets
    from mdp_functions.reference import INCOMPLETE, report_incomplete

    settings, _, calls = runtime_http
    monkeypatch.setenv("MDP_SERVICE_URL", settings.r2_endpoint)
    monkeypatch.setenv("MDP_SERVICE_TOKEN", "fixture")
    (tmp_path / "run_results.json").write_text(
        json.dumps({"results": [{"message": INCOMPLETE + ": fixture"}]})
    )
    control = ControlTargets(Settings(control_api_url=settings.r2_endpoint))
    ctx = SimpleNamespace(
        request_id="previous-page", manifest=SimpleNamespace(layer="universal")
    )
    with source_network(ctx):
        async with control.client() as client:
            assert (await control.command(client, "resolve", {}))["ok"]
        assert "fixture-alert" in await asyncio.to_thread(
            report_cadence_failed, "daily", datetime.now(UTC)
        )
        assert "fixture-alert" in await asyncio.to_thread(
            report_incomplete, "run", str(tmp_path)
        )
        assert ctx.request_id == "previous-page"
        with pytest.raises(ServiceError, match="ctx.http"):
            socket.socket()
    assert len(calls) == 3


@pytest.mark.parametrize("cookies", ["request", "client"])
async def test_cookie_construction_refusal_clears_reject_lineage(monkeypatch, cookies):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4())}
    ctx = Ctx(
        Manifest(
            source_key="cookie_probe",
            layer="bronze",
            external=True,
            writes=["raw.cookie_probe"],
        ),
        run,
    )
    async with TracedClient(
        ctx,
        FakeDB(),
        run,
        no_page,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    ) as client:
        await client.get("https://public.example/")
        previous = ctx.request_id
        ctx.yield_row({"id": "public"})
        kwargs = {"cookies": {"visitor": "fixture"}}
        if cookies == "client":
            client.cookies.jar = httpx.Cookies(kwargs["cookies"]).jar
            kwargs = {}
        with pytest.raises(ServiceError, match="Cookies are refused"):
            await client.get("https://public.example/", **kwargs)
        ctx.reject({"id": "blocked"}, reason="cookie_refused")
        assert ctx.rejected[-1]["_request_id"] is None
        assert ctx.outputs["raw.cookie_probe"][0]["_request_id"] == previous


def test_musicbrainz_mirror_connect_resolves_its_host_inside_a_source(monkeypatch):
    from mdp_functions import musicbrainz

    def fake_connect(url, **kwargs):
        # psycopg resolves a host name in Python before libpq connects.
        socket.getaddrinfo("localhost", 5432)
        return "connection"

    monkeypatch.setattr(musicbrainz.psycopg, "connect", fake_connect)
    ctx = SimpleNamespace(request_id="previous-page")
    with source_network(ctx):
        assert musicbrainz.connect("postgresql://mb_reader@mdp-mb-db.internal/musicbrainz") == "connection"
        with pytest.raises(ServiceError, match="ctx.http"):
            socket.getaddrinfo("localhost", 5432)
