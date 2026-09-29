#!/usr/bin/env python3
"""Report advisory checks after deployment, with one five-minute process deadline."""

import importlib.util
import inspect
import os
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx

TOTAL_TIMEOUT_S = 300
READINESS_TIMEOUT_S = 30


def expired(_signal, _frame):
    raise TimeoutError("Canary checks reached their total five-minute limit")


def check(client):
    deadline = time.monotonic() + READINESS_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            response = client.get("/api/streamlines", timeout=min(2, max(.01, deadline - time.monotonic())))
            if response.status_code in (401, 403):
                raise PermissionError(
                    f"MDP_ADMIN_API_KEY was refused ({response.status_code}); issue an admin key as in "
                    "ops/fly/SECRETS.md#admin-keys and store it in secret store prd"
                )
            response.raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    else:
        raise TimeoutError("Canary service is not ready; check /v1/health and rerun ops/fly/resilience-checks.py")
    response = client.post("/api/streamlines/canaries", json={}, timeout=240)
    response.raise_for_status()
    counts = dict.fromkeys(("passed", "failed", "not_due", "skipped", "timed_out"), 0)
    for result in response.json()["results"]:
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
        error = f" ({result['error_class']})" if result.get("error_class") else ""
        step = result.get("next_step") or "Open /runbooks/source-canary-failed"
        print(f"CANARY {result['source_key']} {status}{error}; {step}", flush=True)
    summary = " ".join(f"{status}={count}" for status, count in counts.items())
    step = "Open /runbooks/source-canary-failed" if counts["failed"] or counts["timed_out"] else "Review /ops for scheduled recovery"
    print(f"CANARY summary {summary}; {step}", flush=True)



def check_registry(conn, registered):
    """Runs in the deployed functions image with its functions_rt connection."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('deploy:source_unregistered'))")
    rows = conn.execute(
        "SELECT source_key FROM control.streamline WHERE enabled ORDER BY source_key"
    ).fetchall()
    missing = [row[0] for row in rows if row[0] not in registered]
    for key in missing:
        conn.execute(
            "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) "
            "SELECT 'source_unregistered','warning','streamline',%s,'source-unregistered' "
            "WHERE NOT EXISTS (SELECT 1 FROM control.alert WHERE class='source_unregistered' "
            "AND subject_id=%s AND resolved_at IS NULL)",
            (key, key),
        )
        print(f"WARN source_unregistered {key}; disable it at /functions/{key} or deploy its function", flush=True)
    if not missing:
        print("REGISTRY all enabled sources are registered; continue with the canary report", flush=True)


def deployed_registry_check():
    # Import the registry on Fly, never from this checkout. Send only this check's
    # code; credentials stay in the deployed environment and never enter argv.
    code = (
        "import os, psycopg\nfrom mdp_functions.registry import discover\n"
        + inspect.getsource(check_registry)
        + "\nregistered = discover()\n"
        + "with psycopg.connect(os.environ['MDP_CONTROL_URL'], connect_timeout=10, "
        "options='-c statement_timeout=15000') as conn:\n"
        + "    check_registry(conn, registered)\n"
    )
    result = subprocess.run(
        ["bash", "ops/fly/fly.sh", "ssh", "console", "--app", "mdp-functions",
         "--org", os.environ.get("FLY_ORG", "example-org"), "--command", "/app/functions/.venv/bin/python -c " + shlex.quote(code)],
        capture_output=True, text=True, timeout=45, check=True,
    )
    print(result.stdout.strip(), flush=True)


def registry_advisory():
    try:
        deployed_registry_check()
    except Exception as exc:  # noqa: BLE001 - a registry outage must not hide canary results
        print(f"WARN registry check unavailable ({type(exc).__name__}); "
              "rerun uv run --project functions python ops/fly/resilience-checks.py", flush=True)


def main():
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(TOTAL_TIMEOUT_S)
    proxy = None
    try:
        registry_advisory()
        spec = importlib.util.spec_from_file_location("secret_map", Path(__file__).with_name("secret-map.py"))
        secrets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(secrets)
        values = secrets.download()
        # The canaries RPC is an operator action; the promoters' MDP_CONTROL_API_KEY cannot call it.
        if not values.get("MDP_ADMIN_API_KEY"):
            raise PermissionError("MDP_ADMIN_API_KEY is missing from secret store prd; see ops/fly/SECRETS.md#admin-keys")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with open(os.devnull, "w") as log:
            proxy = subprocess.Popen(["bash", "ops/fly/fly.sh", "proxy", f"{port}:8090", "--org", os.environ.get("FLY_ORG", "example-org"), "--app", "mdp-control-api"], stdout=log, stderr=log)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"x-api-key": values["MDP_ADMIN_API_KEY"]}, timeout=2) as client:
            check(client)
    except PermissionError as exc:
        print(f"WARN canary checks unavailable: {exc}; see ops/fly/SECRETS.md#admin-keys", flush=True)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - advisory checks fail open
        print(f"WARN canary checks unavailable ({type(exc).__name__}); see ops/runbooks/partial_coverage.md", flush=True)
    finally:
        signal.alarm(0)
        if proxy:
            proxy.terminate()
            try:
                proxy.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=2)


if __name__ == "__main__":
    main()
