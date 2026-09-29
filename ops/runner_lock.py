"""Run a command while holding the Core runner's session lock `core:<cadence>:<scope>`.

Scheduled runs, Retries and Replays of one (cadence, scope) serialize, as runs of one dbt Cloud job
do. The key differs from the `cycle:<cadence>:<scope>` transaction lock that bind and admission take,
so the run's own on-run-start bind never waits on it. A second invocation waits, saying so, for up
to MDP_RUNNER_LOCK_WAIT_S seconds (default 3600), then exits 75 with `runner_busy`.

The holder names itself in its session's application_name (`mdp-runner:<lock id>:<kind>:<epoch>`,
kind `scheduled` or `other` from MDP_RUNNER_KIND). A gated tick (MDP_RUNNER_GATED=1) behind a scheduled
run of the current period exits 0 at once, since that run is this period's; behind anything else (an
operator's Retry, Replay or restore, the deploy's rebuild, a previous period's run) it waits like any
run, then the gate decides.

SIGINT and SIGTERM go to the child, and the lock is held until the child exits, so a Replay's trap
runs its restore under the lock. The holder never kills the child.
"""

import os
import signal
import subprocess
import sys
import time

import psycopg
from httpx import HTTPError


def this_periods_run(conn: psycopg.Connection, label: str) -> bool:
    """Whether the lock holder is a scheduled run that took the lock in the current period."""
    from datetime import datetime, timezone

    from mdp_functions.core_gate import holds_this_period

    held = conn.execute(
        "SELECT application_name FROM pg_stat_activity WHERE application_name LIKE %s AND pid<>pg_backend_pid()",
        (label + "%",),
    ).fetchone()
    parts = held[0].removeprefix(label).split(":") if held else []
    if len(parts) != 2 or parts[0] != "scheduled" or not parts[1].isdigit():
        return False
    return holds_this_period(
        conn,
        os.environ["DBT_MDP_CADENCE"],
        os.environ["DBT_MDP_SCOPE"],
        datetime.fromtimestamp(int(parts[1]), timezone.utc),
        datetime.now(timezone.utc),
    )


def main() -> int:
    key, command = sys.argv[1], sys.argv[2:]
    wait_s = float(os.environ.get("MDP_RUNNER_LOCK_WAIT_S", "3600"))
    gated = os.environ.get("MDP_RUNNER_GATED") == "1"
    with psycopg.connect(
        os.environ["MDP_CONTROL_RT_URL"], autocommit=True, keepalives=1, keepalives_idle=30
    ) as conn:
        label = f"mdp-runner:{conn.execute('SELECT hashtext(%s)', (key,)).fetchone()[0]}:"
        deadline = time.monotonic() + wait_s
        waiting = False
        while not conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (key,)).fetchone()[0]:
            if gated and not waiting and this_periods_run(conn, label):
                print(f"GATE {key}: NOT DUE: this period's scheduled run is in flight", flush=True)
                return 0
            if time.monotonic() >= deadline:
                print(f"runner_busy: another Core run holds {key}; rerun after it finishes", file=sys.stderr)
                return 75
            if not waiting:
                print(f"RUNNER LOCK WAIT {key}: another Core run of this cadence and scope is running; "
                      f"waiting up to {wait_s:.0f} s", flush=True)
                waiting = True
            time.sleep(2)
        kind = "scheduled" if os.environ.get("MDP_RUNNER_KIND") == "scheduled" else "other"
        conn.execute("SELECT set_config('application_name', %s, false)", (f"{label}{kind}:{int(time.time())}",))
        print(f"RUNNER LOCK {key}", flush=True)
        child: subprocess.Popen | None = None
        early: list[int] = []

        def forward(signum: int, _frame: object) -> None:
            if child is None:
                early.append(signum)
            elif child.poll() is None:
                child.send_signal(signum)

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, forward)
        child = subprocess.Popen(command, env=os.environ | {"MDP_RUNNER_LOCKED": key})
        for signum in early:
            child.send_signal(signum)
        # wait() resumes after each forwarded signal (PEP 475). Closing the session afterwards releases
        # the lock, also when the command fails.
        status = child.wait()
        return 128 - status if status < 0 else status


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        if code and os.environ.get("MDP_SERVICE_URL") and os.environ.get("DBT_CLOUD_RUN_ID"):
            try:
                from mdp_functions.cadence_health import report

                report(os.environ["DBT_CLOUD_RUN_ID"], "dbt/target", str(code))
            except (OSError, ValueError, KeyError, HTTPError) as exc:
                print(f"ALERT cadence_failed delivery failed ({type(exc).__name__})", file=sys.stderr)
    sys.exit(code)
