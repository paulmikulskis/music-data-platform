"""The Postgres invoke UDF body, run offline against a real local HTTP service.

The PL/Python body executes as a plain function with a fake `plpy`; sockets, httpx, and
read timeouts are real. Only the read timeout is shortened so a stalled poll is quick.
"""

import json
import sys
import textwrap
import threading
import time
import types
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest
from mdp_functions.settings import REPO

SQL = (REPO / "functions/udf/postgres/mdp_invoke.sql").read_text()
BODY = SQL.split("AS $python$", 1)[1].split("$python$;", 1)[0]  # mdp.invoke
RECEIPT = {
    "run_id": "run-1",
    "status": "succeeded",
    "coverage": "full",
    "rows_written": 3,
    "rows_rejected": 0,
    "dump_id": "dump-1",
    "landed_seq": 7,
    "trace_url": "local://run-1",
    "message": "",
}


class PlpyError(Exception):
    pass


class Canceled(Exception):
    pass


class Plpy:
    """SPI stand-in: private config, heartbeat count, optional cancellation."""

    def __init__(self, url, cancel_at=None):
        self.url, self.cancel_at, self.heartbeats = url, cancel_at, 0

    def execute(self, query):
        if "mdp.config" in query:
            return [
                {"key": "service_url", "value": self.url},
                {"key": "service_token", "value": "token"},
            ]
        self.heartbeats += 1
        if self.heartbeats == self.cancel_at:
            raise Canceled("canceling statement due to user request")
        return []

    def error(self, message):
        raise PlpyError(message)


@pytest.fixture
def service():
    """Answers admission at once; holds the first `stalls` polls past the read timeout."""

    class Handler(BaseHTTPRequestHandler):
        stalls, polls, post_stalls, posts = 0, 0, 0, 0
        bodies: ClassVar[list] = []
        answer: ClassVar[dict] = {"run": {"status": "succeeded"}, "receipts": [RECEIPT]}

        def log_message(self, *args):
            pass

        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            Handler.bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            Handler.posts += 1
            if Handler.posts <= Handler.post_stalls:
                threading.Event().wait(0.5)  # well past the client's read timeout
                return
            self.respond(202, {"run_id": "run-1", "status": "running"})

        def do_GET(self):
            Handler.polls += 1
            if Handler.polls <= Handler.stalls:
                threading.Event().wait(0.5)  # well past the client's read timeout
                return
            self.respond(200, Handler.answer)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def invoke(monkeypatch):
    """The UDF body as a function; backoff sleeps are recorded instead of slept."""
    shim = types.ModuleType("httpx")
    shim.__dict__.update(vars(httpx))
    shim.Timeout = lambda read, connect: httpx.Timeout(min(read, 0.2), connect=connect)
    monkeypatch.setitem(sys.modules, "httpx", shim)
    pauses = []
    monkeypatch.setattr(time, "sleep", pauses.append)

    def run(plpy, params=None):
        namespace = {"plpy": plpy}
        source = "def invoke(source_key, params):\n" + textwrap.indent(BODY, "    ")
        exec(source, namespace)  # noqa: S102 - execute the checked-in UDF body offline
        return namespace["invoke"]("fixture_accounts", json.dumps(params or {}))

    run.pauses = pauses
    return run


def test_two_poll_timeouts_then_answer(service, invoke):
    handler, url = service
    handler.stalls = 2
    plpy = Plpy(url)
    rows = invoke(plpy)
    assert rows == [tuple(RECEIPT.values())]
    assert handler.polls == 3 and invoke.pauses == [0.5, 1.0]
    assert plpy.heartbeats == 3  # the SPI boundary still runs before every poll


def test_service_that_stays_stalled_fails_after_three_polls(service, invoke):
    handler, url = service
    handler.stalls = 3
    with pytest.raises(PlpyError, match="service_unreachable: run polling failed 3"):
        invoke(Plpy(url))
    assert handler.polls == 3


def test_poll_timeouts_fail_at_the_deadline(service, invoke):
    """Reaching the caller's deadline is a timeout, named with the run and its state, not an outage."""
    handler, url = service
    handler.stalls = 3
    with pytest.raises(PlpyError) as raised:
        invoke(Plpy(url), {"deadline_s": 0.4})
    assert str(raised.value) == (
        "invoke_timeout: run run-1 still running at the 0.4s invocation deadline. Open /runs/run-1"
    )
    assert handler.polls == 1 and not invoke.pauses


def test_deadline_message_carries_the_last_receipt(service, invoke):
    """Sleeps are recorded, so the run polls until the real half-second deadline passes."""
    handler, url = service
    handler.answer = {"run": {"status": "running"}, "receipts": [
        {**RECEIPT, "status": "running", "targets_succeeded": 27, "targets_total": 48}]}
    with pytest.raises(PlpyError, match=r"^invoke_timeout: run run-1 still running at the 0\.5s invocation "
                       r"deadline; last receipt 27/48 targets\. Open /runs/run-1$"):
        invoke(Plpy(url), {"deadline_s": 0.5})
    assert handler.polls > 1


def test_admission_sends_the_time_left(service, invoke):
    handler, url = service
    invoke(Plpy(url), {"deadline_s": 270})
    assert 260 < handler.bodies[0]["deadline_s"] <= 270


def test_admission_stalls_twice_then_answers(service, invoke):
    """The Idempotency-Key makes a repeated admission return the same run, so a slow service is retried."""
    handler, url = service
    handler.post_stalls = 2
    assert invoke(Plpy(url)) == [tuple(RECEIPT.values())]
    assert handler.posts == 3 and invoke.pauses == [0.5, 1.0]


def test_admission_that_never_answers_fails_at_the_deadline(service, invoke):
    handler, url = service
    handler.post_stalls = 99
    with pytest.raises(PlpyError, match=r"^invoke_timeout: admission did not answer within the 0\.3s"):
        invoke(Plpy(url), {"deadline_s": 0.3})


FAILED = {"run": {"status": "failed", "error_class": "invoke_timeout"}, "receipts": [{
    **RECEIPT, "status": "failed", "coverage": "partial", "error_class": "invoke_timeout",
    "target_coverage_met": False, "targets_succeeded": 38, "targets_total": 48,
    "message": "Attempt deadline expired; a new attempt can resume; Target coverage 38/48 is below the floor 90%"}]}


def test_a_failed_run_reports_its_receipt(service, invoke):
    """A run that ended under its floor raises its own receipt, never service_unreachable."""
    handler, url = service
    handler.answer = FAILED
    with pytest.raises(PlpyError) as raised:
        invoke(Plpy(url))
    assert str(raised.value) == (
        "invoke_timeout: Attempt deadline expired; a new attempt can resume; "
        "Target coverage 38/48 is below the floor 90%. Open /runs/run-1"
    )


@pytest.mark.parametrize("status", ["failed", "partial"])
def test_a_non_blocking_source_returns_its_miss_as_rows(service, invoke, status):
    """blocks_cycle=False: the failed or under-floor receipt comes back as a row, so the close can run."""
    handler, url = service
    receipt = {**FAILED["receipts"][0], "status": status, "blocks_cycle": False}
    handler.answer = {"run": {"status": status}, "receipts": [receipt]}
    rows = invoke(Plpy(url))
    assert [(r[1], r[2], r[8]) for r in rows] == [(status, "partial", receipt["message"])]


@pytest.mark.parametrize("status", ["cancelled", "superseded"])
def test_a_non_blocking_source_still_fails_when_its_cycle_moves_on(service, invoke, status):
    handler, url = service
    handler.answer = {"run": {"status": status, "error_message": "Cycle superseded"},
                      "receipts": [{**RECEIPT, "status": status, "blocks_cycle": False, "message": ""}]}
    with pytest.raises(PlpyError, match="Cycle superseded. Open /runs/run-1"):
        invoke(Plpy(url))


def test_cancellation_between_polls_is_not_retried(service, invoke):
    handler, url = service
    handler.stalls = 1
    with pytest.raises(Canceled):
        invoke(Plpy(url, cancel_at=2))
    assert handler.polls == 1


def test_service_that_stays_down_fails_after_three_polls(invoke, monkeypatch):
    """Admission lands, then every poll is refused: three tries, then unreachable."""
    polls = []

    def down(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "run-1", "status": "running"})
        polls.append(request)
        raise httpx.ConnectError("connection refused", request=request)

    shim = sys.modules["httpx"]
    transport = httpx.MockTransport(down)
    monkeypatch.setattr(shim, "Client", partial(httpx.Client, transport=transport))
    plpy = Plpy("http://functions.invalid")
    with pytest.raises(PlpyError, match="run polling failed 3 times in a row"):
        invoke(plpy)
    assert len(polls) == 3 and invoke.pauses == [0.5, 1.0] and plpy.heartbeats == 3


def test_fly_first_boot_udf_matches_the_tested_body():
    """Fly installs mdp.invoke from its boot copy (first boot and every bootstrap), so it
    must carry exactly the body tested here."""
    fly = (REPO / "ops/fly/postgres/boot/init/20-mdp-udf.sql").read_text()
    assert fly.split("AS $python$", 1)[1].split("$python$;", 1)[0] == BODY
    assert fly == SQL

@pytest.mark.parametrize("met,allowed", [(True, False), (False, True)])
def test_target_floor_overrides_legacy_partial_switch(service, invoke, monkeypatch, met, allowed):
    monkeypatch.setitem(RECEIPT, "coverage", "partial")
    monkeypatch.setitem(RECEIPT, "target_coverage_met", met)
    monkeypatch.setitem(RECEIPT, "allow_partial", allowed)
    _, url = service
    if met:
        assert invoke(Plpy(url))[0][2] == "partial"
    else:
        with pytest.raises(PlpyError, match="partial_not_allowed"):
            invoke(Plpy(url))
