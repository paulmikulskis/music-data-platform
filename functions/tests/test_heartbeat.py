"""An optional external ping has one request and never logs its secret URL."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
def heartbeat():
    spec = importlib.util.spec_from_file_location("heartbeat", Path(__file__).parents[2] / "ops/heartbeat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_secret_is_recorded(heartbeat, monkeypatch, capsys):
    connection = MagicMock()
    conn = connection.__enter__.return_value
    conn.execute.return_value.fetchone.side_effect = [("daily", "global"), None]
    monkeypatch.setattr(heartbeat.psycopg, "connect", lambda *args, **kwargs: connection)
    get = MagicMock(side_effect=AssertionError("Unexpected request"))
    monkeypatch.setattr(heartbeat.httpx, "get", get)
    heartbeat.ping("scheduled:fixture", {"MDP_CONTROL_RT_URL": "fixture"})
    assert "MDP_HEARTBEAT_URL is unset" in capsys.readouterr().out
    get.assert_not_called()
    assert conn.execute.call_args.args[1][1] == "heartbeat.skipped"
    assert conn.execute.call_args.args[1][3].obj == {"cadence": "daily", "scope": "global", "configured": False}


@pytest.mark.parametrize("closed,status", [(False, 200), (True, 200), (True, 500), (True, 302)])
def test_one_get_after_confirmed_close(heartbeat, monkeypatch, capsys, closed, status):
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.side_effect = [("daily", "global") if closed else None, None]
    monkeypatch.setattr(heartbeat.psycopg, "connect", lambda *args, **kwargs: connection)
    url = "https://heartbeat.invalid/private-token"
    get = MagicMock(return_value=httpx.Response(status, request=httpx.Request("GET", url)))
    monkeypatch.setattr(heartbeat.httpx, "get", get)
    heartbeat.ping("scheduled:fixture", {"MDP_HEARTBEAT_URL": url, "MDP_CONTROL_RT_URL": "fixture"})
    assert get.call_count == int(closed)
    if closed:
        get.assert_called_once_with(url, timeout=10, follow_redirects=False, trust_env=False)
    output = capsys.readouterr().out
    assert "private-token" not in output
    assert len(output.splitlines()) == 1


@pytest.mark.parametrize("prefix", ["scheduled:", "manual:", "canary:", "backfill:"])
@pytest.mark.parametrize("reason", ["scheduled", "other"])
def test_ping_reads_closed_scheduled_cycle(heartbeat, rt, databases, monkeypatch, prefix, reason):
    import psycopg
    from test_alert_recovery import run

    with psycopg.connect(databases["admin_control"]) as conn:
        _, cycle = run(conn, "fixture_accounts", prefix=prefix)
        run_id = conn.execute("SELECT opened_by_dbt_run_id FROM control.cycle WHERE id=%s", (cycle,)).fetchone()[0]
        conn.execute("INSERT INTO control.cycle_attempt(cycle_id,dbt_run_id,reason_category,runner) VALUES (%s,%s,%s,'core')", (cycle, run_id, reason))
    rt.cycles.close(cycle)
    get = MagicMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://heartbeat.invalid")))
    monkeypatch.setattr(heartbeat.httpx, "get", get)
    heartbeat.ping(run_id, {"MDP_HEARTBEAT_URL": "https://heartbeat.invalid", "MDP_CONTROL_RT_URL": databases["control_url"].replace("user=functions_rt password=functions_rt", "user=control_rt password=control_rt")})
    assert get.call_count == int(prefix == "scheduled:" and reason == "scheduled")
    heartbeat.ping(run_id, {"MDP_HEARTBEAT_URL": "https://heartbeat.invalid", "MDP_CONTROL_RT_URL": databases["control_url"].replace("user=functions_rt password=functions_rt", "user=control_rt password=control_rt")})
    assert get.call_count == int(prefix == "scheduled:" and reason == "scheduled")
    with psycopg.connect(databases["admin_control"]) as conn:
        rows = conn.execute('SELECT action,"after" FROM control.audit_log WHERE subject=%s AND action LIKE %s',
                            (run_id, "heartbeat.%")).fetchall()
    assert len(rows) == int(prefix == "scheduled:" and reason == "scheduled")
    if rows:
        assert rows[0] == ("heartbeat.sent", {"cadence": "daily", "scope": "global", "configured": True})


def test_invalid_secret_url_does_not_fail_the_scheduled_build(heartbeat, monkeypatch, capsys):
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.side_effect = [("daily", "global"), None]
    monkeypatch.setattr(heartbeat.psycopg, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(heartbeat.httpx, "get", MagicMock(side_effect=httpx.InvalidURL("private-token")))
    heartbeat.ping("scheduled:fixture", {"MDP_HEARTBEAT_URL": "invalid", "MDP_CONTROL_RT_URL": "fixture"})
    output = capsys.readouterr().out
    assert "HEARTBEAT failed" in output and "private-token" not in output


def test_trickling_response_stops_at_the_total_deadline(heartbeat, monkeypatch, capsys):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = []
    stopped = threading.Event()

    class Trickle(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    if stopped.wait(.02):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.side_effect = [("daily", "global"), None]
    monkeypatch.setattr(heartbeat.psycopg, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(heartbeat, "HEARTBEAT_TIMEOUT_S", .25)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        heartbeat.ping("scheduled:fixture", {"MDP_HEARTBEAT_URL": f"http://127.0.0.1:{server.server_port}/private-token",
                                            "MDP_CONTROL_RT_URL": "fixture"})
        elapsed = time.monotonic() - started
        assert .2 <= elapsed < 1.5
        assert calls == ["/private-token"]
        output = capsys.readouterr().out
        assert "HEARTBEAT failed" in output and "private-token" not in output
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
