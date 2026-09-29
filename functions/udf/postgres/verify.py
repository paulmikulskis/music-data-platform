"""Exercise installed PL/Python over real HTTP; always restore private configuration."""

import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import psycopg


class FixtureHandler(BaseHTTPRequestHandler):
    mode = "running"
    keys: ClassVar[list[str | None]] = []
    polls = 0

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def respond(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        FixtureHandler.keys.append(self.headers.get("Idempotency-Key"))
        if self.mode == "ambiguous" and len(self.keys) == 1:
            self.respond(503, {"error_class": "gateway_timeout"})
        elif self.path == "/v1/bind_cycle":
            self.respond(
                409,
                {"error_class": "runner_inactive", "message": "fixture runner refusal"},
            )
        else:
            self.respond(202, {"run_id": "fixture-run", "status": "running"})

    def do_GET(self) -> None:
        FixtureHandler.polls += 1
        if self.mode == "flaky" and FixtureHandler.polls <= 2:
            # Past the UDF's 10 s read timeout, as a service busy parsing would be.
            time.sleep(11)
            return
        status = "running" if self.mode == "running" else "succeeded"
        partial = self.mode in ("partial_denied", "partial_allowed")
        self.respond(
            200,
            {
                "run": {"status": status},
                "receipts": [
                    {
                        "run_id": "fixture-run",
                        "status": status,
                        "coverage": "partial" if partial else "full",
                        "rows_written": "9007199254740993",
                        "rows_rejected": "0",
                        "dump_id": None,
                        "landed_seq": "9007199254740994",
                        "trace_url": "local://fixture",
                        "message": "fixture partial" if partial else "",
                        "allow_partial": self.mode == "partial_allowed",
                    }
                ],
            },
        )


def main() -> None:
    admin_url = os.environ["MDP_UDF_ADMIN_URL"]
    transform_url = os.environ["MDP_UDF_TRANSFORM_URL"]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        config = admin.execute(
            "SELECT value FROM mdp.config WHERE key='service_url'"
        ).fetchone()[0]
        for role in (
            "service_read",
            "workbench_wh",
            "reader_wh",
            "loader_wh",
            "dbt_transform",
        ):
            assert not admin.execute(
                "SELECT has_table_privilege(%s,'mdp.config','SELECT')", (role,)
            ).fetchone()[0]
        print("PASS private mdp.config: all warehouse application roles denied")
        try:
            with psycopg.connect(os.environ["MDP_UDF_WORKBENCH_URL"]) as conn:
                conn.execute("select * from mdp.invoke('fixture_accounts','{}')")
            raise AssertionError("Workbench unexpectedly invoked")
        except psycopg.Error as exc:
            assert exc.sqlstate == "42501", exc
            print(f"workbench: SQLSTATE {exc.sqlstate}: {exc.diag.message_primary}")
        server = ThreadingHTTPServer(("0.0.0.0", 18084), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            admin.execute(
                "UPDATE mdp.config SET value=%s WHERE key='service_url'",
                ("http://host.docker.internal:1",),
            )
            start = time.monotonic()
            try:
                with psycopg.connect(transform_url) as conn:
                    conn.execute(
                        "select * from mdp.invoke('fixture_accounts','{\"deadline_s\":15}')"
                    )
                raise AssertionError("Closed port unexpectedly succeeded")
            except psycopg.Error as exc:
                assert "service_unreachable" in str(exc), exc
                assert time.monotonic() - start < 15
                print(
                    f"unreachable: {time.monotonic() - start:.2f}s: {exc.diag.message_primary}"
                )
            admin.execute(
                "UPDATE mdp.config SET value=%s WHERE key='service_url'",
                ("http://host.docker.internal:18084",),
            )
            for mode, timeout in (
                ("running", "5s"),
                ("deadline", "15s"),
                ("ambiguous", "15s"),
                ("partial_denied", "15s"),
                ("partial_allowed", "15s"),
                ("flaky", "45s"),
            ):
                FixtureHandler.mode = "running" if mode == "deadline" else mode
                FixtureHandler.keys = []
                FixtureHandler.polls = 0
                params = {"deadline_s": 1 if mode == "deadline" else 40}
                start = time.monotonic()
                try:
                    with psycopg.connect(transform_url) as conn:
                        conn.execute(
                            "SELECT set_config('statement_timeout',%s,true)", (timeout,)
                        )
                        row = conn.execute(
                            "select * from mdp.invoke('fixture_accounts',%s::jsonb)",
                            (json.dumps(params),),
                        ).fetchone()
                        assert mode in ("ambiguous", "partial_allowed", "flaky"), mode
                        assert row[3] == 9007199254740993 and row[6] == 9007199254740994
                        if mode == "ambiguous":
                            expected = hashlib.sha256(b"fixture_accounts||||").hexdigest()
                            assert FixtureHandler.keys == [expected, expected], (
                                FixtureHandler.keys
                            )
                        if mode == "flaky":
                            assert FixtureHandler.polls == 3, FixtureHandler.polls
                        print(
                            f"{mode}: PASS exact bigint receipt; POSTs={len(FixtureHandler.keys)}; "
                            f"GETs={FixtureHandler.polls}; {time.monotonic() - start:.2f}s"
                        )
                except psycopg.Error as exc:
                    expected = {
                        "running": "statement timeout",
                        "deadline": "invoke_timeout",
                        "partial_denied": "fixture partial",
                    }.get(mode)
                    assert expected and expected in str(exc), exc
                    print(
                        f"{mode}: {time.monotonic() - start:.2f}s: SQLSTATE {exc.sqlstate}: {exc.diag.message_primary}"
                    )
            try:
                with psycopg.connect(transform_url) as conn:
                    conn.execute(
                        "select mdp.bind_cycle('hourly','global','fixture','scheduled','fixture',null,'core')"
                    )
                raise AssertionError("Binding error not propagated")
            except psycopg.Error as exc:
                assert "runner_inactive" in str(exc), exc
                print("bind_cycle: " + str(exc.diag.message_primary))
        finally:
            admin.execute(
                "UPDATE mdp.config SET value=%s WHERE key='service_url'", (config,)
            )
            server.shutdown()
            server.server_close()
            thread.join()
            print("RESTORED service_url")


if __name__ == "__main__":
    main()
