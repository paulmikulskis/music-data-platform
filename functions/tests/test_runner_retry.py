"""Tests for runner retry."""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import psycopg
from mdp_functions import core_gate
from mdp_functions.api import create_app
from mdp_functions.core_gate import check, decide
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO
from test_runner_schedule import dbt_steps, run_sh, tick_env

UTC = timezone.utc
DAILY = {
    "cadence": "daily",
    "scope": "global",
    "due_hour": 2,
    "due_weekday": None,
    "timezone": "UTC",
}


def test_a_period_gets_one_retry_after_a_failed_cycle() -> None:
    now = datetime(2026, 9, 25, 5, 10, tzinfo=UTC)
    first, retry = now.replace(hour=2, minute=5), now.replace(hour=3, minute=5)
    # The first failure is retried by the next tick.
    assert decide(DAILY, now, None, False, failed=[first])[0] is True
    # A second failure stops the day, with the cadence_failed reason the runner alerts on.
    due, reason = decide(DAILY, now, None, False, failed=[first, retry])
    assert due is False and reason.startswith(
        "cadence_failed: 2 scheduled daily cycles failed in the period starting 2026-09-25T00:00:00+00:00"
    )
    # Yesterday's failures never count against today, and the next day runs again.
    assert (
        decide(
            DAILY,
            now,
            None,
            False,
            failed=[first - timedelta(days=1), retry - timedelta(days=1)],
        )[0]
        is True
    )
    assert (
        decide(DAILY, now + timedelta(days=1), None, False, failed=[first, retry])[0]
        is True
    )
    # A weekly is capped for its ISO week; an hourly period is 45 minutes, so every tick is its own try.
    weekly = {**DAILY, "cadence": "weekly", "due_hour": 3, "due_weekday": 1}
    monday = datetime(2026, 9, 28, 3, 5, tzinfo=UTC)
    tuesday = monday + timedelta(days=1)
    assert decide(weekly, tuesday, None, False, failed=[monday])[0] is True
    assert (
        decide(
            weekly, tuesday + timedelta(hours=1), None, False, failed=[monday, tuesday]
        )[0]
        is False
    )
    hourly = {**DAILY, "cadence": "hourly", "due_hour": None}
    assert (
        decide(
            hourly,
            now,
            None,
            False,
            failed=[now - timedelta(hours=2), now - timedelta(hours=1)],
        )[0]
        is True
    )


async def test_the_gate_stops_after_the_second_failed_cycle_and_opens_one_alert(
    rt: Runtime, databases, monkeypatch, capsys
) -> None:
    job = "core-daily-global"
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,due_hour,timezone) VALUES (%s,'core','daily','global',0,'UTC')",
            (job,),
        )
    # A scheduled daily fails (its cycle stays open); the retry's bind supersedes it and fails too.
    for _ in range(2):
        await rt.cycles.bind_cycle(
            "daily", "global", "core:" + uuid4().hex, "scheduled", job, runner="core"
        )
        with psycopg.connect(databases["admin_control"]) as conn:
            if _ == 0:
                assert check(conn, job)[0] is True
    with psycopg.connect(databases["admin_control"]) as conn:
        due, reason = check(conn, job)
    assert due is False and reason.startswith(
        "cadence_failed: 2 scheduled daily cycles"
    )
    # The service opens one alert for the period, however often the runner asks.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            create_app(rt.settings, rt, recover=False), raise_app_exceptions=False
        ),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        body = {"job_id": job, "period_start": start.isoformat()}
        first = (await client.post("/v1/alerts/cadence_failed", json=body)).json()
        again = (await client.post("/v1/alerts/cadence_failed", json=body)).json()
    assert first["opened"] is True and again == {
        "alert_id": first["alert_id"],
        "opened": False,
    }
    alert = rt.db.one(
        "SELECT class,severity::text,subject_type,subject_id,runbook_slug FROM control.alert WHERE id=%s",
        (first["alert_id"],),
    )
    assert alert == {
        "class": "cadence_failed",
        "severity": "critical",
        "subject_type": "dbt_job",
        "subject_id": f"{job}@{start.isoformat()}",
        "runbook_slug": "cadence-failed",
    }
    # The runner's gate prints the refusal and asks the service for the alert.
    sent = []

    def post(url, json, headers, timeout):
        sent.append((url, json))
        return httpx.Response(
            200,
            json={"alert_id": first["alert_id"], "opened": False},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("MDP_CONTROL_RT_URL", databases["admin_control"])
    monkeypatch.setenv("MDP_SERVICE_URL", "http://service.internal:8080")
    monkeypatch.setenv("MDP_SERVICE_TOKEN", "token")
    monkeypatch.setattr(sys, "argv", ["core_gate", job])
    assert core_gate.main() == core_gate.NOT_DUE
    printed = capsys.readouterr().out
    assert "NOT DUE: cadence_failed: 2 scheduled daily cycles" in printed
    assert f"ALERT cadence_failed {first['alert_id']}" in printed
    assert sent == [
        (
            "http://service.internal:8080/v1/alerts/cadence_failed",
            {"job_id": job, "period_start": start.isoformat()},
        )
    ]


def hold(databases, tmp_path, kind, seconds):
    """A real runner_lock.py holder of core:hourly:global that names itself with `kind`."""
    env = os.environ | {
        "MDP_CONTROL_RT_URL": databases["admin_control"],
        "MDP_RUNNER_KIND": kind,
        "DBT_MDP_CADENCE": "hourly",
        "DBT_MDP_SCOPE": "global",
    }
    for name in ("MDP_RUNNER_GATED", "MDP_RUNNER_LOCKED"):
        env.pop(name, None)
    holder = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "ops/runner_lock.py"),
            "core:hourly:global",
            sys.executable,
            "-c",
            f"import time; time.sleep({seconds})",
        ],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "RUNNER LOCK core:hourly:global"
    return holder


def gated_tick(databases, tmp_path, **extra):
    return tick_env(
        databases,
        tmp_path,
        MDP_CORE_GATE="1",
        MDP_RUN_REASON_CATEGORY="scheduled",
        **extra,
    )


def test_a_gated_tick_skips_only_this_periods_scheduled_run(
    rt, databases, tmp_path
) -> None:
    from test_runner_signals import shim

    shim(tmp_path)
    holder = hold(databases, tmp_path, "scheduled", 30)
    try:
        started = time.monotonic()
        skipped = run_sh(gated_tick(databases, tmp_path), "hourly")
        assert skipped.returncode == 0, skipped.stdout + skipped.stderr
        assert (
            "GATE core:hourly:global: NOT DUE: this period's scheduled run is in flight"
            in skipped.stdout
        )
        assert time.monotonic() - started < 25 and not dbt_steps(tmp_path)
    finally:
        holder.kill()
        holder.wait()


def test_a_gated_tick_waits_behind_an_operator_run_then_the_gate_decides(
    rt, databases, tmp_path
) -> None:
    """An operator's Retry or Replay holds the lock: the tick waits for it (bounded), then the gate finds
    the hour due and the run goes ahead, so the hour keeps its scheduled cycle."""
    from test_runner_signals import shim

    shim(tmp_path)
    holder = hold(databases, tmp_path, "other", 6)
    started = time.monotonic()
    ran = run_sh(
        gated_tick(databases, tmp_path, MDP_RUNNER_LOCK_WAIT_S="120"), "hourly"
    )
    holder.wait()
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "RUNNER LOCK WAIT core:hourly:global" in ran.stdout
    assert "GATE core-hourly-global: DUE hourly global" in ran.stdout
    assert time.monotonic() - started >= 4
    steps = dbt_steps(tmp_path)
    assert steps[0] == "hourly_global_bronze" and steps[-1] == "hourly_global_transform"


def test_a_previous_periods_scheduled_holder_is_waited_for(
    rt, databases, tmp_path
) -> None:
    """A scheduled holder that took the lock before the current period (a run stuck past its hour) is
    not this period's: the tick waits, bounded, and exits runner_busy when the bound passes."""
    from test_runner_signals import shim

    shim(tmp_path)
    with psycopg.connect(databases["admin_control"], autocommit=True) as holder:
        lock_id = holder.execute("SELECT hashtext('core:hourly:global')").fetchone()[0]
        holder.execute("SELECT pg_advisory_lock(%s::bigint)", (lock_id,))
        taken = int(time.time()) - 50 * 60
        holder.execute(
            "SELECT set_config('application_name', %s, false)",
            (f"mdp-runner:{lock_id}:scheduled:{taken}",),
        )
        waited = run_sh(
            gated_tick(databases, tmp_path, MDP_RUNNER_LOCK_WAIT_S="2"), "hourly"
        )
    assert waited.returncode == 75 and "runner_busy" in waited.stderr, (
        waited.stdout + waited.stderr
    )
    assert "NOT DUE" not in waited.stdout and not dbt_steps(tmp_path)
