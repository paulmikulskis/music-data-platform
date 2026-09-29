"""Recovery polling tolerates temporary unavailability without accepting auth errors."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from harness import Harness


def test_large_reset_sql_uses_stdin(monkeypatch):
    harness = configured_harness()
    query = "BEGIN;\n" + "SELECT 1;\n" * 20000 + "COMMIT;"
    calls = []

    def run(argv, **kwargs):
        assert max(map(len, argv)) < 131072
        assert argv[-2:] == ["-f", "-"]
        assert kwargs["input"] == query
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="COMMIT\n", stderr="")

    monkeypatch.setattr("harness.subprocess.run", run)
    assert harness.reset_transaction(query, wh=True) == "COMMIT\n"
    assert len(calls) == 1


def configured_harness():
    harness = Harness(SimpleNamespace(target="pg_local"))
    harness.env = {
        "MDP_CONTROL_ADMIN_URL": "postgresql://admin@127.0.0.1:5435/control",
        "MDP_CONTROL_URL": "postgresql://worker@127.0.0.1:5435/control",
        "MDP_WAREHOUSE_URL": "postgresql://loader@127.0.0.1:5435/warehouse",
        "MDP_PG_HOST": "127.0.0.1",
        "MDP_PG_PORT": "5435",
    }
    return harness


@pytest.mark.parametrize(
    "key,value,reason",
    [
        (
            "MDP_CONTROL_ADMIN_URL",
            "postgresql://admin@127.0.0.1:5435/postgres",
            "admin database",
        ),
        (
            "MDP_CONTROL_ADMIN_URL",
            "postgresql://admin@remote.example/control",
            "allowed fixture endpoint",
        ),
        (
            "MDP_WAREHOUSE_URL",
            "postgresql://loader@127.0.0.1:5435/other",
            "database names match",
        ),
        ("MDP_PG_HOST", "remote.example", "effective database endpoints"),
        ("MDP_PG_PORT", "5432", "effective database endpoints"),
        ("PGHOSTADDR", "192.0.2.1", "alternate libpq authority"),
    ],
)
def test_refuses_wrong_effective_authority_before_database_access(
    monkeypatch, key, value, reason
):
    harness = configured_harness()
    harness.env[key] = value
    monkeypatch.setattr(
        harness, "sql", lambda *a, **k: pytest.fail("database accessed before refusal")
    )
    with pytest.raises(AssertionError, match=reason):
        harness.reset()


@pytest.mark.parametrize("host", ["fixture.internal", "fixture.flycast"])
def test_fly_fixture_endpoint_requires_explicit_remote_opt_in(host):
    harness = configured_harness()
    for key in ("MDP_CONTROL_ADMIN_URL", "MDP_CONTROL_URL", "MDP_WAREHOUSE_URL"):
        harness.env[key] = harness.env[key].replace("127.0.0.1", host)
    harness.env["MDP_PG_HOST"] = host
    with pytest.raises(AssertionError, match="allowed fixture endpoint"):
        harness.endpoint_safety()
    harness.env["MDP_LIFECYCLE_ALLOW_REMOTE"] = "1"
    harness.endpoint_safety()


@pytest.mark.parametrize(
    "recorded,claim",
    [
        (None, False),
        ("different:5435:control", False),
        ("different:5435:control", True),
    ],
)
def test_claim_refuses_missing_or_mismatching_marker(monkeypatch, recorded, claim):
    harness = configured_harness()
    monkeypatch.setattr(harness, "scalar", lambda *a, **k: recorded)
    monkeypatch.setattr(
        harness, "sql", lambda *a, **k: pytest.fail("mutation before valid claim")
    )
    with pytest.raises(AssertionError, match="audit claim matches"):
        harness.stack_claim(claim)


def test_first_claim_writes_and_rechecks_audit_marker(monkeypatch):
    harness = configured_harness()
    reads = iter([None, harness.fingerprint()])
    monkeypatch.setattr(harness, "scalar", lambda *a, **k: next(reads))
    writes = []
    monkeypatch.setattr(harness, "sql", lambda query, **kw: writes.append((query, kw)))
    harness.stack_claim(True)
    assert len(writes) == 1
    assert "lifecycle_stack_claimed" in writes[0][0]
    assert harness.fingerprint() in writes[0][0]
    assert writes[0][1] == {"admin": True, "rows": False}


def test_matching_claim_never_rewrites_marker(monkeypatch):
    harness = configured_harness()
    monkeypatch.setattr(harness, "scalar", lambda *a, **k: harness.fingerprint())
    monkeypatch.setattr(
        harness, "sql", lambda *a, **k: pytest.fail("existing marker rewritten")
    )
    harness.stack_claim()
    harness.stack_claim(True)


def test_claimed_reset_removes_foreign_manifest_references_but_preserves_foreign_dumps(
    monkeypatch,
):
    harness = configured_harness()
    monkeypatch.setattr(harness, "safety", lambda **kwargs: None)
    writes = []
    foreign_counts = iter([2, 0])

    def scalar(query, **kwargs):
        if "status IN ('queued','running')" in query:
            return 0
        if "control.cycle_input" in query:
            return next(foreign_counts)
        if "to_regclass" in query:
            return True
        pytest.fail("unexpected SQL: " + query)

    def sql(query, **kwargs):
        if not kwargs.get("rows", True):
            writes.append(query)
            return ""
        if query.startswith("SELECT id FROM control.cycle"):
            return [{"id": "own-cycle"}]
        if query.startswith("SELECT id FROM control.dump"):
            return [{"id": "own-dump"}]
        if query.startswith("SELECT id,cycle_id,run_id"):
            return [
                {
                    "id": "foreign-dump",
                    "cycle_id": "foreign-cycle",
                    "run_id": "foreign-run",
                }
            ]
        if "information_schema.columns" in query:
            return [
                {
                    "table_name": "account_snapshots",
                    "columns": ["_cycle_id", "_dump_id"],
                }
            ]
        pytest.fail("unexpected SQL: " + query)

    monkeypatch.setattr(harness, "scalar", scalar)
    monkeypatch.setattr(harness, "sql", sql)
    harness.reset()
    assert (
        "DELETE FROM raw.cycle_inputs WHERE dump_id::text IN ('own-dump')"
        in "\n".join(writes)
    )
    assert (
        "DELETE FROM raw.dump_stamps WHERE dump_id::text IN ('own-dump')"
        in "\n".join(writes)
    )
    assert (
        "DELETE FROM control.cycle_input WHERE cycle_id IN ('own-cycle') OR dump_id IN ('own-dump')"
        in "\n".join(writes)
    )
    assert "DELETE FROM control.dump WHERE id IN ('own-dump')" in "\n".join(writes)
    assert all(
        "foreign-dump" not in query and "TRUNCATE" not in query for query in writes
    )


def test_loop_refuses_pg_even_with_local_overrides(monkeypatch):
    harness = configured_harness()
    harness.args.target = "pg"
    harness.env["MDP_LOOP_PG_HOST"] = "127.0.0.1"
    monkeypatch.setattr(
        harness, "command", lambda *a, **k: pytest.fail("initializer invoked")
    )
    with pytest.raises(AssertionError, match="refuses target pg"):
        harness.case_loop()


def test_reset_refuses_cloud_without_mutation(monkeypatch):
    harness = configured_harness()
    witness = {
        "id": "fixture-run",
        "cycle_id": "fixture-cycle",
        "warehouse_id": "fixture-warehouse",
    }

    def scalar(query, **kwargs):
        if "control.warehouse" in query:
            return 1
        if "runner_mode" in query:
            return "cloud"
        pytest.fail("unexpected SQL: " + query)

    def sql(query, **kwargs):
        assert kwargs.get("rows", True), "mutation attempted during refusal"
        assert query.startswith("SELECT id,cycle_id,warehouse_id")
        return [witness]

    def api(path, **kwargs):
        harness.last_http_status = 200
        return (
            [witness]
            if path.startswith("/v1/runs?")
            else {"started": {}, "status": "ok"}
        )

    monkeypatch.setattr(harness, "scalar", scalar)
    monkeypatch.setattr(harness, "sql", sql)
    monkeypatch.setattr(harness, "api", api)
    with pytest.raises(AssertionError, match="runner_mode must be core"):
        harness.reset()


def test_loop_passes_only_consistent_sanitized_initialization_environment(monkeypatch):
    harness = configured_harness()
    harness.env.update(
        MDP_SERVICE_READ_URL=harness.env["MDP_WAREHOUSE_URL"],
        MDP_CONTROL_DATABASE_URL="postgresql://unrelated.invalid/control",
        PGDATABASE="unrelated",
        PGOPTIONS="unrelated",
    )
    monkeypatch.setattr(harness, "safety", lambda: None)
    monkeypatch.setattr(harness, "select_fixture_targets", lambda: None)
    monkeypatch.setattr(harness, "scalar", lambda *a, **k: 0)
    commands = []

    def command(argv, *, env, isolated):
        assert isolated
        assert env["MDP_CONTROL_ADMIN_URL"] == env["MDP_CONTROL_RT_URL"]
        assert env["MDP_PG_HOST"] == "127.0.0.1"
        assert env["MDP_PG_PORT"] == "5435"
        assert "MDP_CONTROL_DATABASE_URL" not in env
        assert not any(k.startswith("PG") for k in env)
        commands.append(argv)

    monkeypatch.setattr(harness, "command", command)
    harness.case_loop()
    assert commands[0][-3:] == ["control", "init", "--local"]


def test_recovery_poll_retries_503_but_requires_a_terminal_receipt(monkeypatch):
    harness = Harness(SimpleNamespace(target="pg_local"))
    harness.env.update(
        MDP_SERVICE_URL="http://fixture.invalid", MDP_SERVICE_TOKEN=uuid4().hex
    )
    responses = iter(
        [
            httpx.Response(503, json={"error_class": "warehouse_unavailable"}),
            httpx.Response(200, json={"run": {"status": "running"}}),
            httpx.Response(200, json={"run": {"status": "succeeded"}, "receipts": []}),
        ]
    )
    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: next(responses))
    assert harness.terminal("fixture-run") is None
    assert harness.terminal("fixture-run") is None
    assert harness.terminal("fixture-run")["run"]["status"] == "succeeded"


def test_recovery_poll_does_not_retry_auth_errors(monkeypatch):
    harness = Harness(SimpleNamespace(target="pg_local"))
    harness.env.update(
        MDP_SERVICE_URL="http://fixture.invalid", MDP_SERVICE_TOKEN=uuid4().hex
    )
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            401, json={"error_class": "unauthorized"}
        ),
    )
    with pytest.raises(AssertionError, match="401.*unauthorized"):
        harness.terminal("fixture-run")


def test_bind_retries_disconnect_with_same_identity(monkeypatch):
    harness = configured_harness()
    harness.env.update(
        MDP_SERVICE_URL="http://fixture.invalid", MDP_SERVICE_TOKEN=uuid4().hex
    )
    body = {"dbt_run_id": "lifecycle-retry-identity"}
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs["json"]))
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, json={"cycle_id": "same-cycle"})

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr("harness.time.sleep", lambda seconds: None)
    assert harness.api("/v1/bind_cycle", body) == {"cycle_id": "same-cycle"}
    assert calls[0] == calls[1]


def test_unkeyed_mutation_does_not_retry_disconnect(monkeypatch):
    harness = configured_harness()
    harness.env.update(
        MDP_SERVICE_URL="http://fixture.invalid", MDP_SERVICE_TOKEN=uuid4().hex
    )
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        raise httpx.RemoteProtocolError("Server disconnected")

    monkeypatch.setattr(httpx, "request", request)
    with pytest.raises(httpx.RemoteProtocolError):
        harness.api("/v1/_fixture/plan", {"pages": 1})
    assert len(calls) == 1


def test_dbt_transport_retry_preserves_work_identity(monkeypatch):
    harness = configured_harness()
    env = {"DBT_CLOUD_RUN_ID": "lifecycle-fixed", "MDP_RUN_ID": "lifecycle-fixed"}
    calls = []
    result = object()

    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(calls) == 1:
            raise AssertionError("command failed: plpy.Error: service_unreachable")
        return result

    monkeypatch.setattr(harness, "command", command)
    monkeypatch.setattr(harness, "service_alive", lambda: True)
    assert harness.bound_command(["ops/run.sh", "hourly"], env=env) is result
    assert calls[0] == calls[1]


def test_reset_retries_only_rolled_back_deadlocks(monkeypatch):
    harness = configured_harness()
    calls = []

    def sql(query, **kwargs):
        calls.append((query, kwargs))
        if len(calls) < 3:
            raise AssertionError("ERROR: deadlock detected")

    monkeypatch.setattr(harness, "sql", sql)
    monkeypatch.setattr("harness.time.sleep", lambda seconds: None)
    harness.reset_transaction("BEGIN; DELETE FROM raw.cycles; COMMIT;", wh=True)
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    calls.clear()
    with pytest.raises(AssertionError, match="deadlock"):
        monkeypatch.setattr(
            harness,
            "sql",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("ERROR: deadlock detected")
            ),
        )
        harness.reset_transaction("BEGIN; DELETE FROM raw.cycles; COMMIT;", wh=True)


@pytest.mark.parametrize("session", [None, "mdp-isolated-test"])
def test_service_restart_uses_configured_session(monkeypatch, session):
    if session:
        monkeypatch.setenv("MDP_LIFECYCLE_SERVICE_SESSION", session)
    else:
        monkeypatch.delenv("MDP_LIFECYCLE_SERVICE_SESSION", raising=False)
    harness = configured_harness()
    harness.service_env = {}
    commands = []

    def command(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(harness, "command", command)
    harness.restart_local()
    expected = session or "mdp-test-svc"
    assert commands[0] == ["tmux", "has-session", "-t", expected]
    assert commands[1][:5] == ["tmux", "new-session", "-d", "-s", expected]
    if session:
        assert "mdp-test-svc" not in commands[1]
