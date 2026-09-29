"""A Replay under the real ops/run.sh and ops/runner_lock.py: SIGINT to the group and SIGTERM to the top
process both end in the restore, under the lock; a kill leaves control.runner_restore, and the next run
under that lock restores first. A shim runs every `uv run ... dbt` as a fake dbt; everything else is uv."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO

FAKE_DBT = """
import json, os, signal, sys, time
import psycopg
args = sys.argv[1:]
variables = json.loads(args[args.index("--vars") + 1])
step = args[args.index("--selector") + 1] if "--selector" in args else args[0]
replay = "cycle_id" in variables
with psycopg.connect(os.environ["MDP_CONTROL_RT_URL"]) as conn:
    held = conn.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted").fetchone()[0]
log = open(os.environ["FAKE_DBT_LOG"], "a")
log.write(json.dumps({"step": step, "replay": replay, "run": os.environ["DBT_CLOUD_RUN_ID"],
                      "pid": os.getpid(), "locks": held}) + "\\n")
log.flush()
if replay and step.endswith("_bronze") and not os.environ.get("FAKE_DBT_FAST"):
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        log.write(json.dumps({"step": "interrupted"}) + "\\n")
        sys.exit(130)
"""


def shim(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    (directory / "fake_dbt.py").write_text(FAKE_DBT)
    uv = directory / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == run && "$2" == --project && "$4" == dbt ]]; then\n'
        f'  shift 4; exec {sys.executable} "$(dirname "$0")/fake_dbt.py" "$@"\n'
        "fi\n"
        'if [[ "$4" == python && "$5" == -m && "$6" == mdp_functions.cadence_health && "$7" == --succeeded ]]; then\n'
        '  [[ -z "${FAKE_SUCCESS_LOG:-}" ]] || printf "%s\\n" "$8" >> "$FAKE_SUCCESS_LOG"\n'
        '  exit "${FAKE_SUCCESS_EXIT:-0}"\n'
        "fi\n"
        f'exec {shutil.which("uv")} "$@"\n'
    )
    uv.chmod(0o755)
    return directory


def start(databases, tmp_path: Path, *args: str) -> subprocess.Popen:
    env = os.environ | {
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "MDP_CONTROL_RT_URL": databases["admin_control"],
        "FAKE_DBT_LOG": str(tmp_path / "dbt.log"),
        "MDP_RUNNER_LOCK_WAIT_S": "60",
    }
    env.pop("MDP_RUN_ID", None)
    env.pop("MDP_RUNNER_LOCKED", None)
    return subprocess.Popen(
        ["bash", str(REPO / "ops/run.sh"), "hourly", *args],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
    )


def steps(tmp_path: Path) -> list[dict]:
    path = tmp_path / "dbt.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def until(tmp_path: Path, predicate, timeout: float = 60) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = steps(tmp_path)
        if predicate(found):
            return found
        time.sleep(0.2)
    raise AssertionError(f"timed out; dbt log {steps(tmp_path)}")


def pending(databases) -> list[tuple]:
    with psycopg.connect(databases["admin_control"]) as conn:
        return conn.execute("SELECT lock_key,cycle_id::text FROM control.runner_restore").fetchall()


async def closed_hourly(rt: Runtime, databases) -> str:
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('core-hourly-global','core','hourly','global')"
        )
    binding = await rt.cycles.bind_cycle(
        "hourly", "global", "core:" + uuid4().hex, "scheduled", "core-hourly-global", runner="core"
    )
    rt.cycles.close(binding["cycle_id"])
    return str(binding["cycle_id"])


def replay_started(found: list[dict]) -> bool:
    return any(s["replay"] and s["step"].endswith("_bronze") for s in found if "replay" in s)


def restored(found: list[dict]) -> list[dict]:
    return [s for s in found if s.get("replay") is False]


async def interrupted_replay(rt, databases, tmp_path, send) -> None:
    shim(tmp_path)
    cycle = await closed_hourly(rt, databases)
    process = start(databases, tmp_path, "--cycle-id", cycle)
    until(tmp_path, replay_started)
    assert pending(databases) == [("core:hourly:global", cycle)]
    send(process)
    output = process.communicate(timeout=120)[0]
    found = steps(tmp_path)
    assert {"step": "interrupted"} in found, output
    # The restore ran after the interruption, as a full run with its own run id, still under the lock.
    restore = restored(found)
    assert restore[0]["step"] == "hourly_global_bronze" and restore[-1]["step"] == "hourly_global_transform", output
    assert {s["run"] for s in restore} != {found[0]["run"]}
    assert all(s["locks"] >= 1 for s in restore), output
    assert "RESTORE hourly" in output and process.returncode != 0
    assert pending(databases) == []


async def test_sigint_to_the_group_runs_the_restore(rt: Runtime, databases, tmp_path: Path) -> None:
    await interrupted_replay(rt, databases, tmp_path, lambda p: os.killpg(p.pid, signal.SIGINT))


async def test_sigterm_to_the_top_process_runs_the_restore_under_the_lock(rt: Runtime, databases, tmp_path: Path) -> None:
    await interrupted_replay(rt, databases, tmp_path, lambda p: p.send_signal(signal.SIGTERM))


async def test_a_killed_replay_is_restored_by_the_next_run(rt: Runtime, databases, tmp_path: Path) -> None:
    shim(tmp_path)
    cycle = await closed_hourly(rt, databases)
    process = start(databases, tmp_path, "--cycle-id", cycle)
    found = until(tmp_path, replay_started)
    # A machine stop: everything dies at once, the fake dbt's own process group included.
    os.killpg(process.pid, signal.SIGKILL)
    os.killpg(os.getpgid(found[-1]["pid"]), signal.SIGKILL)
    process.communicate(timeout=30)
    assert not restored(steps(tmp_path))
    assert pending(databases) == [("core:hourly:global", cycle)]
    following = start(databases, tmp_path)
    output = following.communicate(timeout=120)[0]
    assert following.returncode == 0, output
    assert "RESTORE PENDING core:hourly:global" in output
    runs = [s["run"] for s in restored(steps(tmp_path))]
    # The pending restore's full run, then the scheduled run, each with its own run id.
    assert len(runs) >= 4 and len(dict.fromkeys(runs)) == 2
    assert pending(databases) == []


async def test_retry_and_restore_record_success_and_a_replay_does_not(
    rt: Runtime, databases, tmp_path: Path, monkeypatch
) -> None:
    """A Retry or restore that passes records its build success, which resolves the cycle's cadence
    alerts; a Replay records none. Only a scheduled build sends the heartbeat."""
    shim(tmp_path)
    successes = tmp_path / "success.log"
    monkeypatch.setenv("FAKE_SUCCESS_LOG", str(successes))
    monkeypatch.setenv("FAKE_DBT_FAST", "1")
    cycle = await closed_hourly(rt, databases)
    retry = start(databases, tmp_path, "--reason-category", "other")
    output = retry.communicate(timeout=120)[0]
    assert retry.returncode == 0, output
    retry_run = steps(tmp_path)[-1]["run"]
    assert successes.read_text().split() == [retry_run]
    assert "HEARTBEAT" not in output
    successes.unlink()
    replay = start(databases, tmp_path, "--cycle-id", cycle)
    output = replay.communicate(timeout=120)[0]
    assert replay.returncode == 0, output
    found = steps(tmp_path)
    replay_run = next(s["run"] for s in found if s.get("replay"))
    restore_run = restored(found)[-1]["run"]
    # One success: the restore's, never the Replay's.
    assert successes.read_text().split() == [restore_run] and restore_run != replay_run, output
    assert "HEARTBEAT" not in output
