"""Core runs of one (cadence, scope) serialize on core:<cadence>:<scope>, and Run Now
binds only while it can take that lock; bind's own cycle:<cadence>:<scope> lock never waits on it."""

import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.settings import REPO

LOCK = REPO / "ops/runner_lock.py"


def start(databases, key, marker, hold_s=0.0, wait_s="30"):
    """ops/runner_lock.py running a command that stamps its start and end times into `marker`."""
    command = (
        "import sys,time;"
        "open(sys.argv[1],'a').write(f'start {time.time()}\\n');"
        f"time.sleep({hold_s});"
        "open(sys.argv[1],'a').write(f'end {time.time()}\\n')"
    )
    return subprocess.Popen(
        [sys.executable, str(LOCK), key, sys.executable, "-c", command, str(marker)],
        env=os.environ | {"MDP_CONTROL_RT_URL": databases["admin_control"], "MDP_RUNNER_LOCK_WAIT_S": wait_s},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def times(marker):
    return [float(line.split()[1]) for line in marker.read_text().splitlines()]


def locked(process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line.startswith("RUNNER LOCK core:"):
            return
    raise AssertionError("runner lock was never taken")


def test_overlapping_runs_of_one_pair_serialize(databases, tmp_path):
    key = f"core:hourly:global:{uuid4().hex}"
    first, second = tmp_path / "first", tmp_path / "second"
    running = start(databases, key, first, hold_s=3)
    locked(running)
    waiting = start(databases, key, second)
    assert "RUNNER LOCK WAIT" in waiting.stdout.readline()
    assert running.wait(20) == 0 and waiting.wait(20) == 0
    # The second run started only after the first ended.
    assert times(second)[0] >= times(first)[1]


def test_a_second_run_without_patience_exits_with_runner_busy(databases, tmp_path):
    key = f"core:daily:global:{uuid4().hex}"
    running = start(databases, key, tmp_path / "first", hold_s=3)
    locked(running)
    refused = start(databases, key, tmp_path / "second", wait_s="0")
    assert refused.wait(20) == 75
    assert "runner_busy" in refused.stderr.read()
    assert not (tmp_path / "second").exists()
    assert running.wait(20) == 0


async def test_run_now_binds_only_while_the_runner_lock_is_free(rt, databases, tmp_path):
    running = start(databases, "core:hourly:global", tmp_path / "held", hold_s=4)
    locked(running)
    try:
        with pytest.raises(ServiceError) as caught:
            await rt.cycles.bind_cycle(
                "hourly", "global", "manual:" + uuid4().hex, "scheduled", "local:hourly",
                runner="core", lock_runner=True,
            )
        assert caught.value.error_class == "core_run_in_progress" and caught.value.status_code == 409
        # The run's own bind takes only cycle:<cadence>:<scope> and never waits on the runner lock.
        bound = await rt.cycles.bind_cycle(
            "hourly", "global", "core:" + uuid4().hex, "scheduled", "local:hourly", runner="core"
        )
        assert bound["cycle_id"]
    finally:
        assert running.wait(20) == 0
    assert await rt.cycles.bind_cycle(
        "hourly", "global", "manual:" + uuid4().hex, "scheduled", "local:hourly", runner="core", lock_runner=True,
    )


@pytest.mark.parametrize("prefix", ["manual:", "backfill:", "canary:"])
async def test_a_retry_attaches_past_a_cycle_that_run_now_opened(rt, prefix):
    scheduled = await rt.cycles.bind_cycle(
        "hourly", "global", "core:" + uuid4().hex, "scheduled", "local:hourly", runner="core"
    )
    rt.cycles.close(scheduled["cycle_id"])
    run_now = await rt.cycles.bind_cycle(
        "hourly", "global", prefix + uuid4().hex, "scheduled", "local:hourly", runner="core", lock_runner=True
    )
    assert run_now["cycle_id"] != scheduled["cycle_id"]
    # A Retry or the Core restore attaches to the newest scheduled cycle, like it skips backfills.
    retry = await rt.cycles.bind_cycle(
        "hourly", "global", "core:" + uuid4().hex, "other", "local:hourly", runner="core"
    )
    assert retry["cycle_id"] == scheduled["cycle_id"]
