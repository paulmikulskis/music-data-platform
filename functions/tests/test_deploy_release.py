"""A partial deploy needs every serving machine on the pinned, healthy release."""

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def release():
    path = Path(__file__).parents[2] / "ops/fly/check-release.py"
    spec = importlib.util.spec_from_file_location("deploy_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("app", ["mdp-functions", "mdp-control-api"])
@pytest.mark.parametrize(
    "second", ["current", "older", None, "unavailable", "unhealthy"]
)
def test_every_serving_machine_reports_the_full_release(
    release, monkeypatch, app, second
):
    calls = []

    def fly(service, *args):
        assert service == app
        calls.append(args)
        if args[0] == "machine":
            return json.dumps(
                [
                    {
                        "id": key,
                        "state": "started",
                        "config": {"metadata": {"fly_process_group": "api"}},
                    }
                    for key in ("first", "second")
                ]
            )
        machine = args[args.index("--machine") + 1]
        value = "current" if machine == "first" else second
        if value == "unavailable":
            raise subprocess.TimeoutExpired(args, 30)
        return json.dumps(
            {"revision": value, "status": "degraded" if value == "unhealthy" else "ok"}
        )

    monkeypatch.setattr(release, "fly", fly)
    if second == "current":
        release.check(app, "current")
    else:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            release.check(app, "current")
    assert len(calls) == 3


@pytest.mark.parametrize("machines", [[], [{"id": "stopped", "state": "stopped"}]])
def test_no_running_service_refuses_release(release, monkeypatch, machines):
    monkeypatch.setattr(release, "fly", lambda *args: json.dumps(machines))
    with pytest.raises(ValueError):
        release.check("mdp-control-api", "current")


def test_functions_host_check_reads_health_and_image_revision(release):
    command = shlex.split(release.COMMANDS["mdp-functions"])
    command[0] = sys.executable
    command[-1] = (
        "import os, urllib.request\n"
        "from io import BytesIO\n"
        "os.environ['MDP_BUILD_SHA'] = 'current'\n"
        "def health(url, timeout):\n"
        "    assert url == 'http://127.0.0.1:8080/v1/health' and timeout == 10\n"
        '    return BytesIO(b\'{"status":"ok"}\')\n'
        "urllib.request.urlopen = health\n" + command[-1]
    )
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"revision": "current", "status": "ok"}


def test_control_host_check_reads_health_and_image_revision(release):
    command = shlex.split(release.COMMANDS["mdp-control-api"])
    command[-1] = (
        "process.env.MDP_BUILD_SHA = 'current'; "
        "globalThis.fetch = async (url, options) => { "
        "if (url !== 'http://[::1]:8090/health' || !options.signal) throw new Error('wrong health check'); "
        "return { ok: true, json: async () => ({ status: 'ok' }) }; }; " + command[-1]
    )
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"revision": "current", "status": "ok"}


def test_release_failure_prints_recovery_without_remote_output(
    release, monkeypatch, capsys
):
    def check(*args):
        raise subprocess.CalledProcessError(1, "ssh", stderr="fixture-private-output")

    monkeypatch.setattr(release, "check", check)
    monkeypatch.setattr(release.sys, "argv", ["check-release.py", "current"])
    assert release.main() == 1
    output = capsys.readouterr().err
    assert release.RECOVER in output
    assert "fixture-private-output" not in output
