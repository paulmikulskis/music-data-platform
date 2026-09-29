"""The runtime-wide forbidden-path list: every transport the runtime builds refuses each listed path
(on a first request, a redirect hop, an auth flow's follow-up, an event hook's rewrite, and a Host header),
whatever the declared hosts allow, and never sends it. A refusal inside a run records its dead letter."""

import ast
import asyncio
import re
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.errors import ServiceError, error_catalog
from mdp_functions.fetch.forbidden import (
    FORBIDDEN,
    RefusingClient,
    RefusingTransport,
    forbidden,
)
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx, bronze
from mdp_functions.registry import REGISTRY, Manifest, sync
from mdp_functions.runbooks import RUNBOOKS

# One example URL per list entry, in list order; the last two check case and slash folding.
EXAMPLES = [
    "https://open.spotify.com/api/token?reason=init&productType=web-player",
    "https://open.spotify.com/get_access_token?reason=transport",
    "https://clienttoken.spotify.com/v1/clienttoken",
    "https://api-partner.spotify.com/pathfinder/v1/query?operationName=fetchPlaylist",
    "https://gew1-spclient.spotify.com/metadata/4/track/abc",
    "https://amp-api.music.apple.com/v1/catalog/us/playlists/pl.u-abc",
    "https://www.shazam.com/shazam/v1/en/US/web/-/tracks/web_chart_global",
    "https://www.shazam.com/shazam/v3/en/US/web/-/tracks/123",
]
EVERY_HOST = [
    *EXAMPLES,
    "https://spclient.wg.spotify.com/color-lyrics/v2/track/abc",
    "https://OPEN.SPOTIFY.COM//API/Token",
    # Encoded dot segments resolve after decoding, and ;params never hide a path.
    "https://open.spotify.com/api/%2e/token",
    "https://open.spotify.com/foo/%2e%2e/api/token",
    "https://open.spotify.com/api/token;x=1",
    "https://open.spotify.com/api/token%3Bx",
    "https://open.spotify.com/api%2Ftoken",
    # A segment that decodes to a separator or NUL is refused on a listed host: a server that decodes before
    # routing would read /api/token/../ or a path cut short.
    "https://open.spotify.com/api/token%2f..%2f",
    "https://open.spotify.com/api/token%252f..%252f",
    "https://open.spotify.com/api/token%00",
    "https://open.spotify.com/api%5Ctoken",
    # The Apple web app catalog API's edge hosts.
    "https://amp-api-edge.music.apple.com/v1/catalog/us/songs/1",
]
ALLOWED = [
    "https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M",
    "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
    "https://open.spotify.com/api/tokens-are-not-this-path",
    "https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb",
    "https://itunes.apple.com/lookup?id=1",
    "https://www.shazam.com/track/123/song",
]


class FakeDB:
    def __init__(self, health=None):
        self.health = health or {}

    def one(self, query, params=()):
        if "cost_ledger" in query:
            return {"n": 0, "bytes": 0}
        return self.health if "host_health" in query else {}

    def all(self, query, params=()):
        return []

    def execute(self, query, params=()):
        return None


async def no_page():
    pass


def context(hosts):
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    return Ctx(Manifest("test_forbidden", "bronze", [], hosts=hosts), run), run


def test_every_list_entry_has_an_example():
    assert len(EXAMPLES) == len(FORBIDDEN)
    for url, (_, _, what) in zip(EXAMPLES, FORBIDDEN, strict=True):
        parsed = httpx.URL(url)
        assert forbidden(parsed.host, parsed.path) == what
    for url in ALLOWED:
        parsed = httpx.URL(url)
        assert forbidden(parsed.host, parsed.path) is None
    # A decoded separator is refused only on a listed host.
    assert forbidden("music.apple.com", "/us/album/a%2Fb/1") is None


@pytest.mark.parametrize("hosts", [[], ["*.spotify.com", "open.spotify.com", "*.apple.com"]])
@pytest.mark.parametrize("url", EVERY_HOST)
async def test_the_traced_client_refuses_a_forbidden_path(monkeypatch, url, hosts):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    sent = []
    ctx, run = context(hosts)
    transport = httpx.MockTransport(lambda request: sent.append(str(request.url)) or httpx.Response(200))
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=transport) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get(url)
    assert caught.value.error_class == "forbidden_path"
    assert sent == []


@pytest.mark.parametrize("url", EVERY_HOST)
async def test_a_redirect_hop_to_a_forbidden_path_is_refused(monkeypatch, url):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    sent = []

    def transport(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={"Location": url})

    ctx, run = context([])
    async with TracedClient(
        ctx, FakeDB(), run, no_page, transport=httpx.MockTransport(transport), follow_redirects=True
    ) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M")
    assert caught.value.error_class == "forbidden_path"
    assert sent == ["https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M"]


ALLOWED_PAGE = "https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M"


class FollowUp(httpx.Auth):
    """An auth flow whose second request goes to a listed path."""

    def auth_flow(self, request):
        yield request
        yield httpx.Request("POST", "https://clienttoken.spotify.com/v1/clienttoken")


def recording():
    sent = []
    return sent, httpx.MockTransport(lambda request: sent.append(str(request.url)) or httpx.Response(200))


@pytest.mark.parametrize("health", [{}, {"http_version": "1.1"}])
async def test_an_auth_flow_follow_up_is_refused_at_the_transport(monkeypatch, health):
    """Both the client's own transport and the HTTP/1.1 client it opens for a host refuse the follow-up."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    sent, transport = recording()
    ctx, run = context([])
    async with TracedClient(ctx, FakeDB(health), run, no_page, transport=transport) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get(ALLOWED_PAGE, auth=FollowUp())
    assert caught.value.error_class == "forbidden_path"
    assert sent == [ALLOWED_PAGE]


async def test_an_event_hook_rewrite_is_refused_at_the_transport(monkeypatch):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    sent, transport = recording()

    async def rewrite(request):
        request.url = httpx.URL("https://api-partner.spotify.com/pathfinder/v1/query")

    ctx, run = context([])
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=transport, event_hooks={"request": [rewrite]}) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get(ALLOWED_PAGE)
    assert caught.value.error_class == "forbidden_path" and sent == []


@pytest.mark.parametrize("host", ["clienttoken.spotify.com", "gew1-spclient.spotify.com", "example.org"])
async def test_a_host_header_naming_another_authority_is_refused(monkeypatch, host):
    """The Host header is the HTTP/2 :authority; one naming a listed host or any other authority is refused."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    sent, transport = recording()
    ctx, run = context([])
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=transport) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get("https://open.spotify.com/v1/clienttoken", headers={"Host": host})
        assert (await client.get(ALLOWED_PAGE, headers={"Host": "OPEN.spotify.com:443"})).status_code == 200
    assert caught.value.error_class == "forbidden_path" and sent == [ALLOWED_PAGE]


async def test_every_transport_the_clients_build_refuses():
    """The default transport, a supplied one, and every proxy mount are wrapped; a proxied request to a
    listed path is refused before any connection is attempted."""
    ctx, run = context([])
    async with TracedClient(ctx, FakeDB(), run, no_page) as client:
        assert isinstance(client._transport, RefusingTransport)
    async with RefusingClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        assert isinstance(client._transport, RefusingTransport)
    async with RefusingClient(proxy="http://127.0.0.1:9") as client:
        assert client._mounts and all(isinstance(t, RefusingTransport) for t in client._mounts.values())
        with pytest.raises(ServiceError) as caught:
            await client.get("https://open.spotify.com/api/token")
    assert caught.value.error_class == "forbidden_path"


async def refused(url, **kwargs):
    """The error a traced client raises for one request, and what reached the transport."""
    sent, transport = recording()
    ctx, run = context([])
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=transport) as client:
        try:
            await client.request(kwargs.pop("method", "GET"), url, **kwargs)
        except ServiceError as exc:
            return exc.error_class, sent
    return None, sent


@pytest.mark.parametrize("extensions", [
    {"target": b"/api/token"}, {"target": b"/api/token?reason=init"},
    {"target": b"https://clienttoken.spotify.com/v1/clienttoken"}, {"target": b"/embed/playlist/other"},
    {"target": httpx.URL(ALLOWED_PAGE).raw_path}, {"trace": lambda *args: None}, {"sni_hostname": "clienttoken.spotify.com"},
    {"network_stream": None},
])
async def test_a_request_extension_outside_the_runtime_set_is_refused(monkeypatch, extensions):
    """httpcore acts on request extensions: `target` replaces the request-target, `trace` hands a callback the
    live request before its headers are written, `sni_hostname` changes the TLS name."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    assert await refused(ALLOWED_PAGE, extensions=extensions) == ("forbidden_path", [])
    runtime = {"timeout": {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}, "mdp_accept": (404,), "mdp_redirects": 1}
    assert await refused(ALLOWED_PAGE, extensions=runtime) == (None, [ALLOWED_PAGE])


class Listener:
    """A local HTTP/1.1 server on 127.0.0.1 that records each request line and Host header it reads."""

    def __init__(self):
        self.seen = []

    async def handle(self, reader, writer):
        try:
            while True:
                lines = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
                host = next((line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("host:")), None)
                self.seen.append((lines[0], host))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()


def rewrite_to_token(event, info):
    """The reviewer's trace callback: rewrite the live request's target and Host just before its headers go."""
    if event.endswith("send_request_headers.started"):
        info["request"].url.target = b"/api/token?reason=init&productType=web-player"
        info["request"].headers[:] = [(b"Host", b"open.spotify.com")]


@pytest.mark.parametrize("health", [{}, {"http_version": "1.1"}])
async def test_a_trace_callback_never_reaches_the_wire(monkeypatch, health):
    """Both the client's own (HTTP/2-capable) transport and its HTTP/1.1 client refuse the callback."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    ctx, run = context([])
    async with Listener() as listener, TracedClient(ctx, FakeDB(health), run, no_page) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get(f"{listener.url}/embed/playlist/x", extensions={"trace": rewrite_to_token})
        assert caught.value.error_class == "forbidden_path"
        assert listener.seen == []


@pytest.mark.parametrize("health", [{}, {"http_version": "1.1"}])
async def test_a_response_carries_no_connection_handle(monkeypatch, health):
    """The reviewer's raw write after an allowed GET needs the response's network_stream; it never returns."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    ctx, run = context([])
    async with Listener() as listener, TracedClient(ctx, FakeDB(health), run, no_page) as client:
        response = await client.get(f"{listener.url}/embed/playlist/x")
        assert response.status_code == 200 and "network_stream" not in response.extensions
        assert response.http_version == "HTTP/1.1" and response.reason_phrase == "OK"
    assert listener.seen == [("GET /embed/playlist/x HTTP/1.1", listener.url.removeprefix("http://"))]


async def test_an_http2_response_carries_no_connection_handle():
    handle = object()
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, extensions={"http_version": b"HTTP/2", "network_stream": handle, "stream_id": 1, "other": handle}))
    async with RefusingClient(transport=transport) as client:
        response = await client.get(ALLOWED_PAGE)
    assert set(response.extensions) == {"http_version", "stream_id"} and response.http_version == "HTTP/2"


async def test_explicit_mounts_are_wrapped():
    sent, transport = recording()
    async with RefusingClient(mounts={"all://": transport, "all://example.org": None}) as client:
        assert all(t is None or isinstance(t, RefusingTransport) for t in client._mounts.values())
        with pytest.raises(ServiceError) as caught:
            await client.get("https://clienttoken.spotify.com/v1/clienttoken")
    assert caught.value.error_class == "forbidden_path" and sent == []


async def test_an_idn_host_passes_in_its_ascii_form():
    sent, transport = recording()
    async with RefusingClient(transport=transport) as client:
        assert (await client.get("https://bücher.example/katalog")).status_code == 200
    assert sent == ["https://xn--bcher-kva.example/katalog"]


LISTENBRAINZ_ALLOWED = ["/1/popularity/recording", "/1/popularity/artist/", "/1/popularity/release",
                        "/1/popularity/release-group", "/1/stats/sitewide/artists?range=week",
                        "/1/explore/fresh-releases/?days=7", "/similar-artists/json?artist_mbids=x"]
LISTENBRAINZ_REFUSED = ["/1/stats/release-group/0b1b8d1a-5a2c-4c4f-8f0e-9a3c7b1d2e3f/listeners",
                        "/1/user/someone/listens", "/1/stats/user/someone/artists", "/1/donors/recent",
                        "/1/popularity/top-recordings-for-artist/x", "/1/stats/artist/x/listeners",
                        "/1/%70opularity/recording/../../user/x/listens", "/",
                        "/1/stats/sitewide/../../user/x/listens", "/1/stats/sitewide/%2e%2e/%2e%2e/donors/recent",
                        "/1/popularity/recording%2f..%2f..%2fuser"]


@pytest.mark.parametrize("host", ["api.listenbrainz.org", "labs.api.listenbrainz.org"])
async def test_listenbrainz_passes_only_its_allowlist(monkeypatch, host):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    for path in LISTENBRAINZ_ALLOWED:
        assert forbidden(host, path.split("?", 1)[0]) is None, path
        assert (await refused(f"https://{host}{path}"))[0] is None, path
    for path in LISTENBRAINZ_REFUSED:
        assert forbidden(host, path), path
        assert await refused(f"https://{host}{path}") == ("forbidden_path", []), path






# Call sites whose class the scan cannot resolve, and why each needs no row of its own.
UNRESOLVED_SITES = {
    ("control_db.py", "item['kind']"): "open_alerts forwards body alert classes; catalog checks and the runbook foreign key apply",
    ("derived.py", "result['error_class']"): "the silver sandbox relays a class its body raised, scanned there",
    ("runs.py", "exc.error_class"): "record_error records a ServiceError raised elsewhere, scanned there",
    ("landing.py", "kind"): "landing's reject wrapper; its callers pass scanned classes",
    ("runs.py", "error"): "the run's recorded class again, or accounting_mismatch (scanned)",
}


def local_strings(function: ast.AST) -> dict[str, list[str | None]]:
    """Every value a function assigns to each plain name: the string, or None when it is not a string
    constant (a parameter, a tuple target, any other expression)."""
    values: dict[str, list[str | None]] = {}
    arguments = getattr(function, "args", None)
    for arg in [*(arguments.posonlyargs + arguments.args + arguments.kwonlyargs if arguments else [])]:
        values.setdefault(arg.arg, []).append(None)
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = getattr(node, "value", None)
            constant = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        values.setdefault(name.id, []).append(constant if target is name else None)
    return values


def resolve(arg: ast.AST, local: dict[str, list[str | None]], consts: dict[str, str]) -> tuple[set[str], bool]:
    """The classes an argument can name, and whether that set is complete."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return {arg.value}, True
    if isinstance(arg, ast.IfExp):
        (body, whole), (other, rest) = resolve(arg.body, local, consts), resolve(arg.orelse, local, consts)
        return body | other, whole and rest
    if isinstance(arg, ast.Name) and arg.id in local:
        known = local[arg.id]
        return {v for v in known if v is not None}, None not in known
    if isinstance(arg, ast.Name) and arg.id in consts:
        return {consts[arg.id]}, True
    return set(), False


def raised_classes() -> tuple[dict[str, set[str]], set[tuple[str, str]]]:
    """Every error class a ServiceError(...), an alert(conn, run, class, ...) or a landing reject(claim, dump,
    class, ...) under src/ can name, through literals, conditionals, local string assignments and module
    constants, with the files that name it; an SQL literal naming a runbook_slug counts as that slug's class.
    Also every call site whose class cannot be resolved, as (file, argument source)."""
    package = Path(__file__).resolve().parents[1] / "src/mdp_functions"
    found: dict[str, set[str]] = {}
    unresolved: set[tuple[str, str]] = set()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text())
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                  for t in n.targets if isinstance(t, ast.Name)}
        where = str(path.relative_to(package))
        # Each node's innermost enclosing function (ast.walk visits an outer function before its inner ones).
        owner: dict[int, ast.AST] = {}
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    owner[id(node)] = function
        locals_of: dict[int, dict[str, list[str | None]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "runbook_slug" in node.value:
                for slug in re.findall(r"'([a-z]+(?:-[a-z0-9]+)+)'", node.value):
                    found.setdefault(slug.replace("-", "_"), set()).add(where)
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            index = {"ServiceError": 0, "alert": 2, "reject": 2}.get(name or "")
            if index is None or len(node.args) <= index:
                continue
            function = owner.get(id(node))
            local = locals_of.setdefault(id(function), local_strings(function)) if function else {}
            classes, complete = resolve(node.args[index], local, consts)
            for kind in classes:
                found.setdefault(kind, set()).add(where)
            if not complete:
                unresolved.add((where, ast.unparse(node.args[index])))
    return found, unresolved


# Classes raised only where no run is recording: function registration, the workbench service, the
# request layer answering its caller, and `superseded`, which work_batch never records. Each maps to the
# only files allowed to raise it; anything else a run can reach needs a control.runbook row, because
# record_error's alert names the class's slug and a missing row fails that insert.
REQUEST_ONLY = {
    **dict.fromkeys(("duplicate_source", "gold_requires_egress", "invalid_cadence", "invalid_completion",
                     "invalid_cycle_inputs", "invalid_dedupe", "invalid_input_order", "invalid_prepare",
                     "invalid_source_key", "invalid_time_budget", "invalid_version", "invalid_park_after",
                     "invalid_retain_days", "invalid_blocks_cycle"),
                    frozenset({"registry.py"})),
    **dict.fromkeys(("backtest_column_missing", "backtest_duplicate_key", "backtest_key_missing",
                     "unknown_operation", "workbench_artifact_invalid", "workbench_build_failed",
                     "workbench_compile_failed", "workbench_draft_invalid", "workbench_isolation",
                     "workbench_model_missing", "workbench_pr_failed", "workbench_preview_limit",
                     "workbench_query_refused", "workbench_review_required", "workbench_row_cap",
                     "workbench_run_missing", "workbench_schema_cap", "workbench_session_missing"),
                    frozenset({"workbench.py", "workbench_pr.py"})),
    **dict.fromkeys(("fixture_unavailable", "not_found", "unknown_dump", "unknown_fixture", "unknown_job"), frozenset({"api.py"})),
    **dict.fromkeys(("model_ephemeral", "model_not_built", "tenant_read_denied"),
                    frozenset({"workbench.py", "workbench_access.py"})),
    "raw_read_denied": {"workbench_access.py"},
    "workbench_cycle_missing": {"workbench.py"},
    "workbench_history_unavailable": {"workbench_history.py"},
    "idempotency_conflict": {"admission.py"},
    "invalid_cursor": {"api.py", "preview.py"},
    "superseded": {"admission.py", "runs.py"},
}


def test_every_error_class_a_run_can_record_has_a_runbook():
    found, unresolved = raised_classes()
    # A class the scan cannot see would slip past this test and fail record_error's alert insert at run time.
    assert unresolved == set(UNRESOLVED_SITES), sorted(unresolved ^ set(UNRESOLVED_SITES))
    assert {"vendor_retryable", "vendor_4xx", "accounting_mismatch", "dump_unreadable", "schema_breaking"} <= found.keys()
    assert {"forbidden_path", "variant_mismatch", "control_api_unavailable",
            "dbt_failure", "cadence_failed"} <= found.keys()
    for kind, allowed in REQUEST_ONLY.items():
        assert found.get(kind) and found[kind] <= allowed, (kind, found.get(kind))
    assert REQUEST_ONLY.keys() <= error_catalog().keys()
    missing = sorted(k for k in found.keys() - REQUEST_ONLY.keys() if k not in RUNBOOKS)
    assert missing == []


async def test_a_refusal_inside_a_function_records_its_dead_letter(rt, databases):
    name = "forbidden_probe_" + uuid4().hex[:6]

    @bronze(source_key=name, writes=["raw." + name])
    async def probe(ctx, rows):
        await ctx.http.get("https://clienttoken.spotify.com/v1/clienttoken")
        yield {"never": 1}

    try:
        sync(rt.db)
        _, run = await bound(rt, name)
        await rt.execute(run["id"])
        row = rt.db.one("SELECT status,error_class FROM control.run WHERE id=%s", (run["id"],))
        assert row["error_class"] == "forbidden_path", row
        with psycopg.connect(databases["admin_control"]) as conn:
            letters = conn.execute("SELECT reason FROM control.dead_letter WHERE run_id=%s", (run["id"],)).fetchall()
            alerts = conn.execute(
                "SELECT runbook_slug FROM control.alert WHERE run_id=%s AND class='forbidden_path'", (run["id"],)
            ).fetchall()
        assert [r[0].split(":")[0] for r in letters] == ["forbidden_path"]
        assert alerts == [("forbidden-path",)]
    finally:
        REGISTRY.pop(name, None)
