"""Missing launcher credentials fail before an app deploys, without printing values."""

import importlib.util
import os
from pathlib import Path

import pytest


def test_secret_map_checks_required_and_optional_without_values(capsys):
    path = Path(__file__).parents[2] / "ops/fly/secret-map.py"
    spec = importlib.util.spec_from_file_location("secret_map_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = {key: "synthetic-secret-value" for key in module.MAP["mdp-control-api"]}
    values.pop("MDP_FLY_MACHINES_TOKEN")
    with pytest.raises(SystemExit):
        module.check("mdp-control-api", values)
    output = capsys.readouterr()
    assert "required secret mdp-control-api: MDP_FLY_MACHINES_TOKEN" in output.err
    assert "synthetic-secret-value" not in output.out + output.err
    values["MDP_FLY_MACHINES_TOKEN"] = "synthetic-secret-value"
    values.pop("CLERK_SECRET_KEY")
    module.check("mdp-control-api", values)
    assert "WARN optional secret" in capsys.readouterr().err


def test_scheduled_runner_has_no_canary_gate():
    root = Path(__file__).parents[2]
    assert "cadence_health canaries" not in (root / "ops/run.sh").read_text()
    deploy = (root / "ops/deploy.sh").read_text()
    assert deploy.index("resilience-checks.py") > deploy.rindex("if $rebuild_due; then rebuild_marts; fi")


def test_canary_client_has_one_total_deadline():
    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("canary_check_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TOTAL_TIMEOUT_S <= 300
    assert module.READINESS_TIMEOUT_S <= 30


def test_shazam_seed_and_declared_floor():
    from mdp_functions.registry import discover

    root = Path(__file__).parents[2]
    assert "washington,-d.c." not in (root / "inputs/chart_seed.csv").read_text()
    assert discover()["sz_chart"].min_target_coverage == .9


def test_unavailable_canary_service_exhausts_one_short_readiness_budget(monkeypatch):
    import httpx

    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("canary_budget_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    class Unavailable:
        def get(self, path, timeout):
            assert timeout <= 2
            clock[0] += timeout
            raise httpx.ReadTimeout("unavailable")

        def post(self, *args, **kwargs):
            pytest.fail("An unavailable service must not start canaries")

    with pytest.raises(TimeoutError, match="not ready"):
        module.check(Unavailable())
    assert clock[0] <= 30


@pytest.mark.parametrize(
    "machines,expected",
    [
        ([{"name": "mdp-hourly", "state": "started"}], "mdp-hourly is started"),
        (
            [{"name": "mdp-daily", "state": "stopped", "config": {"schedule": "hourly"},
              "events": [{"type": "start", "status": "starting", "source": "flyd", "timestamp": "NOW-50"}]}],
            "mdp-daily is due in 9 min",
        ),
        (
            [{"name": "mdp-daily", "state": "stopped", "config": {"schedule": "hourly"},
              "events": [{"type": "start", "status": "starting", "source": "flyd", "timestamp": "NOW-20"}]},
             {"name": "mdp-rebuild-hourly", "state": "stopped",
              "config": {"env": {"MDP_RUN_REASON_CATEGORY": "other"}}}],
            "",
        ),
        (
            [{"name": "mdp-hourly", "state": "stopped", "config": {"schedule": "hourly"},
              "events": [{"type": "start", "status": "starting", "source": "user", "timestamp": "NOW-20"},
                         {"type": "start", "status": "starting", "source": "flyd", "timestamp": "NOW-50"}]}],
            "mdp-hourly is due in 9 min",
        ),
        (
            [{"name": "mdp-hourly", "state": "stopped", "config": {"schedule": "hourly"},
              "events": [{"type": "start", "status": "starting", "source": "user", "timestamp": "NOW-20"}]}],
            "mdp-hourly has no scheduled start on record since its last update",
        ),
        (
            [{"name": "mdp-hourly", "state": "stopped", "config": {"schedule": ""}, "events": []}],
            ("mdp-hourly has no valid schedule and is not held by this deploy. Rerun: "
             "ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api"),
        ),
        (
            [{"name": "mdp-hourly", "state": "created", "config": {
                "schedule": "", "metadata": {"mdp_deploy_hold": "this-deploy"}}, "events": []}],
            "",
        ),
        (
            [{"name": "mdp-hourly", "state": "stopped", "config": {
                "schedule": "", "metadata": {"mdp_deploy_hold": "another-deploy"}}, "events": []}],
            ("mdp-hourly has no valid schedule and is not held by this deploy. Rerun: "
             "ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api"),
        ),
        (
            [{"name": "mdp-hourly", "state": "started", "config": {"schedule": ""}, "events": []}],
            "mdp-hourly is started",
        ),
        (None, "the core-runner machine list is unavailable"),
    ],
)
def test_database_restart_waits_for_a_quiet_runner(tmp_path, machines, expected):
    import json
    import subprocess
    import time

    root = Path(__file__).parents[2]
    body = (root / "ops/deploy.sh").read_text()
    function = body[body.index("quiet_reason() {"):body.index("\nwait_quiet() {")]
    (tmp_path / "ops/fly").mkdir(parents=True)
    now_ms = int(time.time() * 1000)
    listing = json.dumps(machines).replace('"NOW-50"', str(now_ms - 50 * 60_000 - 30_000))
    listing = listing.replace('"NOW-20"', str(now_ms - 20 * 60_000))
    stub = "exit 1" if machines is None else f"cat <<'JSON'\n{listing}\nJSON"
    (tmp_path / "ops/fly/fly.sh").write_text(stub + "\n")
    result = subprocess.run(
        ["bash", "-c", f"{function}\nquiet_reason"], cwd=tmp_path, capture_output=True,
        text=True, check=False, env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "FLY_ORG": "test", "hold_id": "this-deploy"},
    )
    assert result.stdout.strip() == expected


def test_canary_client_stops_at_once_on_a_refused_key():
    import httpx

    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("canary_refusal_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(403, json={"error_class": "forbidden"})

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(base_url="http://control.invalid", transport=transport) as client,
        pytest.raises(PermissionError, match="MDP_ADMIN_API_KEY"),
    ):
        module.check(client)
    assert calls == ["/api/streamlines"]


def test_canary_report_lists_outcomes_and_counts_with_next_steps(capsys):
    import httpx

    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("canary_report_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = [{"source_key": f"fixture_{status}", "status": status,
                "next_step": "Open /functions/fixture"}
               for status in ("passed", "failed", "not_due", "skipped", "timed_out")]
    def respond(request):
        return httpx.Response(200, json={"results": results})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(respond)) as client:
        module.check(client)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6
    assert all(line.endswith("Open /functions/fixture") for line in lines[:-1])
    assert "passed=1 failed=1 not_due=1 skipped=1 timed_out=1" in lines[-1]
    assert lines[-1].endswith("Open /runbooks/source-canary-failed")


def test_canary_report_links_to_scheduled_health_when_no_probe_failed(capsys):
    import httpx

    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("canary_clean_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = [{"source_key": "fixture", "status": status, "next_step": "Open /functions/fixture"}
               for status in ("passed", "not_due", "skipped")]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": results}))
    with httpx.Client(base_url="http://fixture", transport=transport) as client:
        module.check(client)
    assert capsys.readouterr().out.splitlines()[-1].endswith("Review /ops for scheduled recovery")


@pytest.mark.parametrize("override", [None, "0", "1"])
def test_postgres_build_arg_uses_only_explicit_override(override):
    import subprocess

    root = Path(__file__).parents[2]
    env = os.environ | {"FLY_ORG": "example-org"}
    env.pop("MDP_PG_WITH_PG_LAKE", None)
    env.pop("MDP_SKIP_CI_WAIT", None)
    if override is not None:
        env["MDP_PG_WITH_PG_LAKE"] = override
    result = subprocess.run(
        ["bash", str(root / "ops/deploy.sh"), "--dry-run", "--app", "mdp-postgres"],
        env=env, capture_output=True, text=True, check=True,
    )
    if override is None:
        assert "WITH_PG_LAKE" not in result.stdout
    else:
        assert f"--build-arg WITH_PG_LAKE={override}" in result.stdout
    assert result.stdout.index("ops/ci-wait.sh") < result.stdout.index("WAIT ")


@pytest.mark.parametrize("app", ["mdp-functions", "mdp-control-api", "mdp-core-runner"])
def test_ci_gate_precedes_every_app_path_and_has_explicit_bypass(app):
    import subprocess

    root = Path(__file__).parents[2]
    env = os.environ | {"FLY_ORG": "example-org", "MDP_SKIP_CI_WAIT": "0"}
    command = ["bash", str(root / "ops/deploy.sh"), "--dry-run", "--app", app]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.index("ops/ci-wait.sh") < result.stdout.index("secret-map.py check")
    bypass = subprocess.run(command, env=env | {"MDP_SKIP_CI_WAIT": "1"},
                            capture_output=True, text=True, check=True)
    assert "CI WAIT BYPASSED" in bypass.stderr
    assert "ops/ci-wait.sh" not in bypass.stdout
    build = subprocess.run(command + ["--build-only"], env=env,
                           capture_output=True, text=True, check=True)
    assert "ops/ci-wait.sh" not in build.stdout


CI_SHA = "a" * 40
CI_REQUIRED = ("conformance", "control-ci", "dbt-ci", "functions-ci", "generated-drift")


def ci_snapshot(monkeypatch, runs=(), checks=(), passed=CI_REQUIRED):
    """ci_wait's verdict on one commit: the passed workflows plus (name, status, conclusion) rows."""
    path = Path(__file__).parents[2] / "ops/ci_wait.py"
    spec = importlib.util.spec_from_file_location("ci_wait_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rows(extra):
        return [{"name": name, "head_sha": CI_SHA, "status": status, "conclusion": conclusion,
                 "html_url": "https://github.com/fixture"}
                for name, status, conclusion in [(n, "completed", "success") for n in passed] + list(extra)]

    read = {"workflow_runs": rows(runs), "check_runs": rows([*checks, ("showcase-artifacts-postgres", "completed", "success")])}
    monkeypatch.setattr(module, "api", lambda path, key: read[key])
    return module.snapshot(CI_SHA)


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_ci_wait_passes_over_skipped_and_neutral_rows(monkeypatch, conclusion):
    # The scheduled cadence-watch runs on main's HEAD with its job gated off, so run and check are skipped.
    assert ci_snapshot(monkeypatch, [("cadence-watch", "completed", conclusion)],
                       [("status", "completed", conclusion)]) is True


@pytest.mark.parametrize("site", ["run", "check"])
@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale", None],
)
def test_ci_wait_blocks_every_other_conclusion(monkeypatch, site, conclusion):
    row = [("cadence-watch" if site == "run" else "status", "completed", conclusion)]
    assert ci_snapshot(monkeypatch, *((row, ()) if site == "run" else ((), row))) is False


def test_ci_wait_waits_on_pending_rows_and_never_counts_a_skip_as_required(monkeypatch):
    assert ci_snapshot(monkeypatch, [("cadence-watch", "in_progress", None)]) is None
    assert ci_snapshot(monkeypatch, (), [("status", "queued", None)]) is None
    others = tuple(name for name in CI_REQUIRED if name != "functions-ci")
    assert ci_snapshot(monkeypatch, [("functions-ci", "completed", "skipped")], passed=others) is None


def test_build_context_archives_every_file_a_deployed_dockerfile_copies():
    import re
    import subprocess
    import sys

    root = Path(__file__).parents[2].resolve()
    archived = subprocess.run([sys.executable, "ops/fly/build-context.py", "--paths"], cwd=root,
                              capture_output=True, text=True, check=True).stdout.split()

    def covered(path):
        return any(path == a or path.startswith(a + "/") for a in archived)

    deploy = (root / "ops/deploy.sh").read_text()
    apps = re.search(r"^apps=\((.*)\)$", deploy, re.MULTILINE).group(1).split()
    # deploy.sh's config map: two apps keep their fly.toml outside ops/fly/<app without mdp->/.
    configs = {"mdp-pg-frontend": "ops/fly/haproxy/fly.toml", "mdp-functions": "functions/fly.toml"}
    assert "mdp-showcase" in apps
    for app in apps:
        config = root / configs.get(app, f"ops/fly/{app.removeprefix('mdp-')}/fly.toml")
        dockerfile = re.search(r'dockerfile = "([^"]+)"', config.read_text()).group(1)
        dockerfile = str((config.parent / dockerfile).resolve().relative_to(root))
        assert covered(dockerfile), (app, dockerfile)
        for line in (root / dockerfile).read_text().splitlines():
            words = line.split()
            if words[:1] == ["COPY"] and not any(w.startswith("--from=") for w in words):
                for source in (w.rstrip("/") for w in words[1:-1] if not w.startswith("--")):
                    assert covered(source), f"{app}: {dockerfile} copies {source}; add it to build-context.py"


def test_registry_warns_once_per_enabled_missing_source(capsys):
    import sqlite3

    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("registry_check_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # These statements use shared SQL. Only the PostgreSQL lock and placeholders
    # need adapting to run the selection and deduplication without a service.
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS control")
    db.execute("CREATE TABLE control.streamline(source_key text, enabled boolean)")
    db.execute("CREATE TABLE control.alert(class text, severity text, subject_type text, "
               "subject_id text, runbook_slug text, resolved_at text)")
    db.executemany("INSERT INTO control.streamline VALUES (?, ?)",
                   [("present", True), ("orphan_a", True), ("orphan_b", True), ("disabled", False)])

    class Connection:
        def execute(self, sql, params=()):
            if "pg_advisory_xact_lock" in sql:
                return None
            return db.execute(sql.replace("%s", "?"), params)

    module.check_registry(Connection(), {"present"})
    module.check_registry(Connection(), {"present"})
    alerts = db.execute("SELECT class,severity,subject_id,runbook_slug FROM control.alert").fetchall()
    assert alerts == [("source_unregistered", "warning", key, "source-unregistered")
                      for key in ("orphan_a", "orphan_b")]
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 4
    assert all("disable it at /functions/" in line and line.endswith("or deploy its function") for line in lines)
    db.execute("UPDATE control.streamline SET enabled=false WHERE source_key='orphan_a'")
    module.check_registry(Connection(), {"present", "orphan_b"})
    assert "all enabled sources are registered" in capsys.readouterr().out
    db.close()


def test_registry_check_uses_deployed_imports_and_fails_open(monkeypatch, capsys):
    import shlex
    import subprocess

    path = Path(__file__).parents[2] / "ops/fly/resilience-checks.py"
    spec = importlib.util.spec_from_file_location("remote_registry_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def remote(args, **kwargs):
        assert args[:5] == ["bash", "ops/fly/fly.sh", "ssh", "console", "--app"]
        assert args[5] == "mdp-functions"
        assert shlex.split(args[-1])[:2] == ["/app/functions/.venv/bin/python", "-c"]
        code = shlex.split(args[-1])[-1]
        assert "from mdp_functions.registry import discover" in code
        assert "os.environ['MDP_CONTROL_URL']" in code
        compile(code, "remote registry check", "exec")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", remote)
    module.registry_advisory()
    assert "registry check unavailable (TimeoutExpired); rerun" in capsys.readouterr().out


def test_failed_ci_stops_deploy_before_any_fly_or_bootstrap_call(tmp_path):
    import shutil
    import subprocess

    root = Path(__file__).parents[2]
    (tmp_path / "ops/fly").mkdir(parents=True)
    (tmp_path / "bin").mkdir()
    shutil.copy(root / "ops/deploy.sh", tmp_path / "ops")
    (tmp_path / "ops/ci-wait.sh").write_text('echo "CI refused $1; inspect Actions"; exit 1\n')
    (tmp_path / "ops/fly/build-context.py").write_text(
        'import sys\nif sys.argv[1] == "--paths": print("functions")\n'
    )
    (tmp_path / "bin/git").write_text(
        '#!/bin/sh\nif [ "$1" = rev-parse ]; then printf "%040d\\n" 1; fi\n'
    )
    (tmp_path / "bin/git").chmod(0o755)
    (tmp_path / "bin/fly").write_text(
        '#!/bin/sh\n[ "$*" = version ] || exit 2\necho "fly v0.4.108 linux/amd64"\n'
    )
    (tmp_path / "bin/fly").chmod(0o755)
    env = os.environ | {"FLY_ORG": "example-org", "MDP_SKIP_CI_WAIT": "0",
                        "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]}
    env.pop("MDP_DEPLOY_REVISION", None)
    result = subprocess.run(["bash", str(tmp_path / "ops/deploy.sh"), "--app", "mdp-functions"],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "CI refused " + "0" * 39 + "1" in result.stdout
    assert "secret-map.py" not in result.stdout
    assert "bootstrap.sh" not in result.stdout
    assert "fly.sh" not in result.stdout


def test_showcase_secrets_have_phased_requirements(capsys):
    path = Path(__file__).parents[2] / "ops/fly/secret-map.py"
    spec = importlib.util.spec_from_file_location("showcase_secret_map", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "MDP_SHOWCASE_HOUSE_READER_KEY" not in module.MAP["mdp-functions"]
    assert len(module.MAP["mdp-functions"]) == len(set(module.MAP["mdp-functions"]))
    assert "showcase_wh" in module.ROLES
    assert "MDP_ROLE_PASSWORD_SHOWCASE_WH" in module.MAP["mdp-postgres"]
    assert "MDP_TRUSTED_BROWSER_ORIGINS" in module.MAP["mdp-control-api"]
    # The Explorer reads warehouse relations as reader_wh; a missing URL hides them.
    assert "MDP_READER_URL" in module.MAP["mdp-control-api"]
    assert "MDP_READER_URL" not in module.OPTIONAL
    values = {key: "fixture-value" for key in module.MAP["mdp-showcase"]}
    values["MDP_SHOWCASE_DENY_NAMES"] = "synthetic-denied-value"
    values.pop("MDP_SHOWCASE_HOUSE_READER_KEY")
    module.check("mdp-showcase", values)
    with pytest.raises(SystemExit):
        module.check("mdp-showcase", values, integrations=True)
    for key in ("MDP_SHOWCASE_PEOPLE", "MDP_SHOWCASE_WH_URL", "MDP_CONTROL_RT_URL"):
        with pytest.raises(SystemExit):
            module.check("mdp-showcase", {k: v for k, v in values.items() if k != key})
    for key in ("MDP_SHOWCASE_IDLE_DAYS", "MDP_SHOWCASE_MAX_DAYS", "MDP_SHOWCASE_ORIGIN"):
        assert key in module.MAP["mdp-showcase"]
        values.pop(key)
    module.check("mdp-showcase", values)
    control_values = {key: "fixture-value" for key in module.MAP["mdp-control-api"]}
    for key in ("MDP_SHOWCASE_PEOPLE", "MDP_SHOWCASE_LINK_SECRET", "MDP_SHOWCASE_ORIGIN"):
        assert key in module.MAP["mdp-control-api"]
        control_values.pop(key)
    module.check("mdp-control-api", control_values)
    assert "fixture-value" not in str(capsys.readouterr())


def test_fresh_fly_initializer_requires_showcase_password(tmp_path):
    import subprocess

    root = Path(__file__).parents[2]
    # Execute the real password loop without initializing a second server. The psql stub
    # records argument names only; extension and UDF setup are outside this assertion.
    stub = tmp_path / "psql"
    stub.write_text("#!/usr/bin/env bash\nfor arg in \"$@\"; do case \"$arg\" in showcase_wh_password=*) echo showcase_password_forwarded;; esac; done\n")
    stub.chmod(0o755)
    python = tmp_path / "python3"
    python.write_text("#!/usr/bin/env bash\nexit 0\n")
    python.chmod(0o755)
    env = {"PATH": f"{tmp_path}:{os.environ['PATH']}", "POSTGRES_USER": "postgres", "WITH_PG_LAKE": "0"}
    roles = (root / "ops/fly/postgres/boot/init/00-mdp-init.sh").read_text().split("for role in ")[1].split("; do")[0].split()
    for role in roles:
        env["MDP_ROLE_PASSWORD_" + role.upper()] = "fixture"
    script = root / "ops/fly/postgres/boot/init/00-mdp-init.sh"
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "showcase_password_forwarded\n"
    del env["MDP_ROLE_PASSWORD_SHOWCASE_WH"]
    assert subprocess.run(["bash", str(script)], env=env, capture_output=True, check=False).returncode != 0
    assert "control/packages/control-db/sql/showcase-role.sql" in (root / "ops/fly/postgres/Dockerfile").read_text()


def test_showcase_deploy_follows_data_api_and_allocates_a_shared_public_address():
    import subprocess

    root = Path(__file__).parents[2]
    result = subprocess.run(
        ["bash", "ops/deploy.sh", "--dry-run", "--app", "mdp-data-api", "--app", "mdp-showcase"],
        cwd=root, env={**os.environ, "FLY_ORG": "example-org"}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.index("check mdp-data-api") < result.stdout.index("check mdp-showcase")
    assert "ips allocate-v4 --shared --yes --org example-org --app mdp-showcase" in result.stdout
    assert "ips allocate-v4 --shared --yes --org example-org --app mdp-data-api" not in result.stdout
    assert "--ha=false --no-public-ips" in result.stdout


def test_full_deploy_skips_an_unconfigured_showcase():
    deploy = (Path(__file__).parents[2] / "ops/deploy.sh").read_text()
    guard = deploy.index('if [[ "$app" == mdp-showcase && "$selected" == \' \' ]]')
    assert guard < deploy.index('run python3 ops/fly/secret-map.py check "$app"')
    assert "SKIP mdp-showcase" in deploy[guard:guard + 400]


def test_optional_alert_email_names_and_plain_setup_steps(capsys):
    path = Path(__file__).parents[2] / "ops/fly/secret-map.py"
    spec = importlib.util.spec_from_file_location("email_secret_map_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = {"RESEND_API_KEY", "SMTP_URL", "MDP_EMAIL_FROM", "MDP_EMAIL_TO"}
    assert names <= set(module.MAP["mdp-control-api"]) & module.OPTIONAL
    assert "MDP_HEARTBEAT_URL" in module.MAP["mdp-core-runner"]
    assert "MDP_HEARTBEAT_URL" in module.OPTIONAL
    values = {key: "fixture-value" for key in module.MAP["mdp-control-api"] if key not in names}
    module.check("mdp-control-api", values)
    output = capsys.readouterr()
    assert "Email is off: set RESEND_API_KEY or SMTP_URL" in output.err
    assert "Email has no sender: set MDP_EMAIL_FROM" in output.err
    assert "Email has no recipient: set MDP_EMAIL_TO" in output.err
    assert "fixture-value" not in output.out + output.err


# `fly secrets import` round trip. deploy.sh pipes secret store's JSON download through
# `secret-map.py filter <app>` into flyctl's importer; these tests hold every mapped value to
# arriving on Fly byte-for-byte, judged by a port of flyctl's own parser.

FLY_SCAN_MAX = 64 * 1024  # bufio.MaxScanTokenSize, the default bufio.Scanner token limit


def fly_parse_secrets(data: bytes) -> dict[str, str]:
    """Python port of parseSecrets in superfly/flyctl internal/command/secrets/parser.go.

    Ported from flyctl v0.4.108 (commit 598c8d785986ef71fcb6b964b90f920b8936cd8e), the reader
    behind `fly secrets import`. The behaviours it pins:
    - bufio.Scanner splits on "\\n" and drops one "\\r" before it; a line of 64 KiB or more stops the
      scan, and flyctl never checks scanner.Err(), so that key and every later key vanish silently;
    - a line starting with "#" or holding only whitespace is skipped;
    - the key is the text before the first "=", trimmed; leading spaces are trimmed from the value;
    - from the first "#" the text is cut as a comment (with the spaces before it) when the text
      before that "#" holds an even number of double quotes;
    - a value that starts and ends with three double quotes (length >= 6) is taken verbatim
      between them;
    - a value that starts with three double quotes opens a multiline value, closed by the first
      later line that ends with three double quotes;
    - otherwise one pair of surrounding double quotes, or else single quotes, is removed;
    - nothing is ever unescaped.
    """
    secrets: dict[str, str] = {}
    multiline_key = None
    parts: list[str] = []
    start = 0
    while start < len(data):
        end = data.find(b"\n", start)
        raw = data[start:] if end < 0 else data[start:end]
        if len(raw) >= FLY_SCAN_MAX:
            break  # bufio.ErrTooLong, ignored by flyctl
        start = len(data) if end < 0 else end + 1
        line = raw.removesuffix(b"\r").decode()
        if multiline_key is not None:
            if line.endswith('"""'):
                parts.append(line[:-3])
                secrets[multiline_key] = "".join(parts)
                multiline_key, parts = None, []
            else:
                parts.append(line + "\n")
            continue
        if line.startswith("#") or line.strip() == "":
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"Secrets must be provided as NAME=VALUE pairs ({line} is invalid)")
        key = key.strip()
        value = value.lstrip(" ")
        before, hash_sep, _ = value.partition("#")
        if hash_sep and before.count('"') % 2 == 0:
            value = before.rstrip(" ")
        if value.startswith('"""') and value.endswith('"""') and len(value) >= 6:
            secrets[key] = value[3:-3]
        elif value.startswith('"""'):
            multiline_key, parts = key, [value[3:] + "\n"]
        else:
            if value in ('"', "'"):
                raise ValueError("flyctl panics: slice bounds out of range")
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            secrets[key] = value
    return secrets


# Cases from flyctl's parser_test.go at the same commit.
@pytest.mark.parametrize(("text", "expected"), [
    ("\n# A comment plus a new line with spaces\n\nFOO=BAR\n# Another comment\nQUX=NAH\n", {"FOO": "BAR", "QUX": "NAH"}),
    ("FOO=BAR\nQUX=NAH\n", {"FOO": "BAR", "QUX": "NAH"}),
    ("FOO=BAR\r\nQUX=NAH\r\n", {"FOO": "BAR", "QUX": "NAH"}),
    ('\nFOO=BAR\nMULTILINE="""SOMETHING\nANOTHER LINE\n\nENDSHERE"""\nTRAILERENV=what\nFIN="""Here is the end,\nmy only friend"""\n',
     {"FOO": "BAR", "MULTILINE": "SOMETHING\nANOTHER LINE\n\nENDSHERE", "TRAILERENV": "what", "FIN": "Here is the end,\nmy only friend"}),
    ("FOO=BAR,BAZ", {"FOO": "BAR,BAZ"}),
    ("FOO=BAR BAZ", {"FOO": "BAR BAZ"}),
    ('FOO="BAR BAZ"', {"FOO": "BAR BAZ"}),
    ("FOO = BAR", {"FOO": "BAR"}),
    ('FOO="BAR BAZ" # comment', {"FOO": "BAR BAZ"}),
    ("FOO='BAR BAZ'\nKEY='value'", {"FOO": "BAR BAZ", "KEY": "value"}),
    ('VARIABLE="""my-single-line-multiline-string"""\nANOTHER="""another"""',
     {"VARIABLE": "my-single-line-multiline-string", "ANOTHER": "another"}),
    ('EMPTY=""""""\nSINGLE="""x"""\nWITHSPACES="""  spaces  """\nMIXED="""line1"""\nNORMAL=regular',
     {"EMPTY": "", "SINGLE": "x", "WITHSPACES": "  spaces  ", "MIXED": "line1", "NORMAL": "regular"}),
    ('VARIABLE = """my-single-line-multiline-string"""\nANOTHER = """another"""',
     {"VARIABLE": "my-single-line-multiline-string", "ANOTHER": "another"}),
])
def test_fly_parser_port_matches_upstream_cases(text, expected):
    assert fly_parse_secrets(text.encode()) == expected


def test_fly_parser_port_shows_why_the_env_format_and_refused_values_break():
    # secret store's env format escapes quotes and backslashes; flyctl keeps the escapes.
    assert fly_parse_secrets(b'P="[{\\"handle\\":\\"a\\"}]"\n') == {"P": '[{\\"handle\\":\\"a\\"}]'}
    # A double quote and "#": an odd count of quotes in the value before "#" cuts the line, and the
    # cut triple-quoted value then opens a multiline value that never closes, so the key vanishes.
    assert fly_parse_secrets(b'P="""{"u":"x#y"}"""\nQ="ok"\n') == {}
    # A newline in a quoted value leaves a line with no "=".
    with pytest.raises(ValueError):
        fly_parse_secrets(b'P="a\nb"\n')
    # A line of 64 KiB or more silently drops that key and every later key.
    assert fly_parse_secrets(b'A="1"\nP="' + b"x" * FLY_SCAN_MAX + b'"\nZ="2"\n') == {"A": "1"}


SECRET_MAP = Path(__file__).parents[2] / "ops/fly/secret-map.py"


def run_filter(app, values):
    import json
    import subprocess
    import sys

    return subprocess.run([sys.executable, str(SECRET_MAP), "filter", app], input=json.dumps(values).encode(),
                          capture_output=True, check=False)


PLAIN = [
    "synthetic-token",
    "",
    "postgresql://showcase_wh:p%40ss@mdp-postgres.internal:5432/warehouse?sslmode=require",
    "https://example.com/path?q=1#fragment",
    "#starts-with-hash",
    "a # spaced comment-looking tail",
    "a=b==c",
    "  leading spaces",
    "trailing spaces  ",
    "   ",
    "it's got an apostrophe",
    "a'b'c",
    "back\\slash\\n and \\\\ doubled",
    "C:\\path\\",
    "unicodé ✓ 日本語 🎵",
    "tab\tinside",
    "$HOME `cmd` $(sub) ; | & > <",
]
TRIPLE = [
    '[{"handle":"ada","display_name":"Ada"}]',
    '{"name": "O\'Brien"}',
    '"',
    '""',
    '"leading quote',
    'trailing quote"',
    'backslashes \\" stay \\\\"',
    "'single quotes around a \"double\"'",
    ' "spaced" ',
    '["é","✓","日本"]',
    'a=b "c"',
]
REFUSED = {
    "line1\nline2": "spans lines",
    "carriage\rreturn": "spans lines",
    '{"url":"https://example.com/#frag"}': "double quote and '#'",
    'a"""b': "three double quotes",
    "'wrapped'": "single quote",
    "'leading": "single quote",
    "trailing'": "single quote",
    "x" * FLY_SCAN_MAX: "64 KiB",
    123: "not text",
}


@pytest.mark.parametrize("value", PLAIN + TRIPLE)
def test_secret_map_filter_round_trips_through_fly_secrets_import(value):
    result = run_filter("mdp-showcase", {"MDP_SERVICE_TOKEN": value})
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert fly_parse_secrets(result.stdout) == {"MDP_SERVICE_TOKEN": value}
    form = 'MDP_SERVICE_TOKEN="""' if value in TRIPLE else 'MDP_SERVICE_TOKEN="'
    assert result.stdout.decode().startswith(form)
    assert result.stdout.count(b"\n") == 1


@pytest.mark.parametrize(("value", "reason"), list(REFUSED.items()), ids=list(range(len(REFUSED))))
def test_secret_map_filter_refuses_values_fly_cannot_read_back(value, reason):
    result = run_filter("mdp-showcase", {"MDP_SERVICE_TOKEN": value, "MDP_SERVICE_URL": "http://fine.internal:8080"})
    assert result.returncode != 0
    assert result.stdout == b""
    err = result.stderr.decode()
    assert err.startswith("FAIL secret MDP_SERVICE_TOKEN cannot pass through fly secrets import unchanged; it ")
    assert reason in err and "then rerun the deploy" in err
    assert err.count("\n") == 1
    if isinstance(value, str):
        assert value.strip("'\n\r") not in err


def test_showcase_people_json_arrives_exactly_for_every_app_that_maps_it():
    import json

    people = [
        {"handle": "ada", "display_name": "Ada L", "admin_key": "mdp_admin_" + "a1" * 16, "api_key_id": "key_01"},
        {"handle": "zoe", "display_name": "Zoë O'Neil", "admin_key": "mdp_admin_" + "b2" * 16, "api_key_id": "key_02",
         "email": "zoe@example.com"},
    ]
    raw = json.dumps(people, ensure_ascii=False)
    for app in ("mdp-showcase", "mdp-control-api"):
        values = {"MDP_SHOWCASE_PEOPLE": raw, "MDP_SERVICE_URL": "http://api.process.mdp-functions.internal:8080",
                  "MDP_SHOWCASE_ORIGIN": "https://mdp-showcase.example.invalid", "SECRET_STORE_PROJECT": "music-data-platform",
                  "UNMAPPED_KEY": "never-imported"}
        result = run_filter(app, values)
        assert result.returncode == 0, result.stderr
        parsed = fly_parse_secrets(result.stdout)
        assert parsed["MDP_SHOWCASE_PEOPLE"] == raw
        assert json.loads(parsed["MDP_SHOWCASE_PEOPLE"]) == people
        assert parsed == {k: v for k, v in values.items() if k not in ("SECRET_STORE_PROJECT", "UNMAPPED_KEY")}
    # An escaped env export reaches Fly with its escapes still present.
    secret_store_env = 'MDP_SHOWCASE_PEOPLE="' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"\n'
    corrupted = fly_parse_secrets(secret_store_env.encode())["MDP_SHOWCASE_PEOPLE"]
    assert corrupted != raw
    with pytest.raises(json.JSONDecodeError):
        json.loads(corrupted)


def test_secret_map_filter_keeps_absent_keys_absent_and_empty_values_empty():
    # An absent key writes no line, so Fly keeps its current value (unchanged from the env pipeline).
    # An empty secret store value is written as KEY="" and arrives empty, as an escaped env export would.
    result = run_filter("mdp-showcase", {"MDP_SERVICE_TOKEN": "", "MDP_SERVICE_URL": "http://x.internal"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'MDP_SERVICE_TOKEN=""\nMDP_SERVICE_URL="http://x.internal"\n'
    assert fly_parse_secrets(result.stdout) == {"MDP_SERVICE_TOKEN": "", "MDP_SERVICE_URL": "http://x.internal"}
    assert run_filter("mdp-showcase", {"UNMAPPED_KEY": "value"}).stdout == b""


def test_secret_map_filter_keeps_the_functions_otlp_override():
    values = {"OTLP_ENDPOINT": "https://otlp.example.com/v1#x", "OTLP_HEADERS": 'Authorization=Basic "abc"'}
    parsed = fly_parse_secrets(run_filter("mdp-functions", values).stdout)
    assert parsed == {"OTLP_ENDPOINT": "http://mdp-alloy.internal:4318", "OTLP_HEADERS": 'Authorization=Basic "abc"'}
    assert fly_parse_secrets(run_filter("mdp-alloy", values).stdout) == values
    assert "OTLP_ENDPOINT" not in fly_parse_secrets(run_filter("mdp-functions", {"OTLP_HEADERS": "h"}).stdout)


def test_secret_map_filter_refuses_the_whole_import_and_names_every_refused_key():
    values = {"MDP_SERVICE_TOKEN": "a\nb", "MDP_SHOWCASE_PEOPLE": '[{"x":"#"}]', "MDP_SERVICE_URL": "http://fine"}
    result = run_filter("mdp-showcase", values)
    assert result.returncode != 0 and result.stdout == b""
    err = result.stderr.decode()
    assert "FAIL secret MDP_SERVICE_TOKEN " in err and "FAIL secret MDP_SHOWCASE_PEOPLE " in err
    assert "MDP_SERVICE_URL" not in err and '[{"x":"#"}]' not in err


def test_secret_map_filter_refuses_the_env_format_without_echoing_it():
    import subprocess
    import sys

    env_lines = b'MDP_SERVICE_TOKEN="synthetic-secret-value"\n'
    result = subprocess.run([sys.executable, str(SECRET_MAP), "filter", "mdp-showcase"], input=env_lines,
                            capture_output=True, check=False)
    assert result.returncode != 0 and result.stdout == b""
    assert b"secret_store export json" in result.stderr
    assert b"synthetic-secret-value" not in result.stderr


def test_deploy_imports_secrets_from_the_json_download():
    import subprocess

    root = Path(__file__).parents[2]
    deploy = (root / "ops/deploy.sh").read_text()
    assert "--format env" not in deploy
    pipe = 'secret_store export json |'
    assert pipe in deploy
    assert "COMMAND " + pipe + " python3 ops/fly/secret-map.py filter %s | bash ops/fly/fly.sh secrets import" in deploy
    result = subprocess.run(["bash", "ops/deploy.sh", "--dry-run", "--app", "mdp-showcase"], cwd=root,
                            env={**os.environ, "FLY_ORG": "example-org"}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert ("COMMAND " + pipe + " python3 ops/fly/secret-map.py filter mdp-showcase | bash ops/fly/fly.sh secrets import "
            "--stage --org example-org --app mdp-showcase") in result.stdout


def test_secret_map_filter_line_limit_matches_flyctls_scanner():
    prefix = len('MDP_SERVICE_TOKEN="') + len('"')
    longest = "x" * (FLY_SCAN_MAX - 1 - prefix)
    result = run_filter("mdp-showcase", {"MDP_SERVICE_TOKEN": longest, "MDP_SERVICE_URL": "http://x"})
    assert result.returncode == 0, result.stderr
    assert max(len(line) for line in result.stdout.splitlines()) == FLY_SCAN_MAX - 1
    assert fly_parse_secrets(result.stdout) == {"MDP_SERVICE_TOKEN": longest, "MDP_SERVICE_URL": "http://x"}
    one_more = longest + "x"
    assert fly_parse_secrets(b'MDP_SERVICE_TOKEN="' + one_more.encode() + b'"\n') == {}
    refused = run_filter("mdp-showcase", {"MDP_SERVICE_TOKEN": one_more})
    assert refused.returncode != 0 and refused.stdout == b"" and b"64 KiB" in refused.stderr


@pytest.mark.parametrize(
    "version,accepted",
    [
        ("fly v0.2.999 linux/amd64", False),
        ("fly v0.3.0 linux/amd64", False),
        ("fly v0.3.225 linux/amd64", False),
        ("fly v0.3.226 linux/amd64", True),
        ("flyctl v0.3.227 linux/amd64", True),
        ("flyctl v0.4.0 linux/amd64", True),
        ("fly v0.4.108 linux/amd64 Commit: fixture BuildDate: fixture", True),
        ("flyctl v0.4.108 linux/amd64 Commit: fixture BuildDate: fixture", True),
        ("flyctl v1.0.0 darwin/arm64", True),
        ("flyctl v0.4.108-dev linux/amd64", True),
        ("flyctl v0.4.108+abc linux/amd64", True),
        ("flyctl v0.4.108-dev.1+abc linux/amd64", True),
        ("flyctl v0.3.225-dev linux/amd64", False),
        ("flyctl v0.3.226-dev linux/amd64", False),
        ("flyctl v0.3.226+abc linux/amd64", True),
        ('{"Name":"flyctl","Version":"0.3.225","OS":"linux"}', False),
        ('{"Name":"flyctl","Version":"0.3.226","OS":"linux"}', True),
        ('{"Name":"flyctl","Version":"0.4.108-dev","OS":"linux"}', True),
        ('{"Name":"flyctl","Version":"v0.4.108+abc","OS":"linux"}', True),
        ('{\n  "Version": "0.4.108", "Architecture": "amd64"\n}', True),
        ('{"version":"0.4.108"}', True),
        ('{"Version":null}', False),
        ('{"Version":404108}', False),
        ('{"Version":{}}', False),
        ('{"Version":"development","Commit":"v0.4.108"}', False),
        ('{"Commit":"v0.4.108"}', False),
        ('{"Version":"0.4.108"', False),
        ('["0.4.108"]', False),
        ('"0.4.108"', False),
        ("flyctl v0.4 linux/amd64", False),
        ("flyctl v0.4.108garbage linux/amd64", False),
        ("flyctl v0.4.108+ linux/amd64", False),
        ("flyctl development", False),
        ("unrelated v0.4.108", False),
        ("", False),
    ],
)
def test_deploy_fly_version_floor(tmp_path, version, accepted):
    import subprocess

    root = Path(__file__).parents[2]
    body = (root / "ops/deploy.sh").read_text()
    function = body[body.index("check_fly_version() {"):body.index("\nif $dry; then")]
    fly = tmp_path / "fly"
    calls = tmp_path / "calls"
    fly.write_text(
        '#!/bin/sh\n[ "$*" = version ] || exit 2\n'
        'echo version >> "$TEST_FLY_CALLS"\nprintf "%s\\n" "$TEST_FLY_VERSION"\n'
    )
    fly.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{function}\ncheck_fly_version"],
        env={"PATH": f"{tmp_path}:{os.environ['PATH']}", "TEST_FLY_VERSION": version,
             "TEST_FLY_CALLS": str(calls)},
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == accepted
    assert calls.read_text() == "version\n"
    assert result.stdout == ""
    if accepted:
        assert result.stderr == ""
    else:
        assert result.stderr == (
            "FAIL deploy needs flyctl v0.3.226 or later to import secrets unchanged. "
            "Run fly version update, then rerun the deploy.\n"
        )
