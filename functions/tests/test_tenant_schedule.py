"""Tests for tenant schedule."""

import importlib.util
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from mdp_functions.core_gate import check, decide, defaults
from mdp_functions.registry import discover
from mdp_functions.runs import Runtime
from tenant_schedule_fixture import ROOT, UTC, tenant


async def test_tenant_daily_cycles_freeze_the_local_weekday(
    rt: Runtime, databases
) -> None:
    _, scope = tenant(databases, tz="Asia/Tokyo")
    job = f"core-daily-{scope}"
    scheduled = await rt.cycles.bind_cycle(
        "daily", scope, "core:" + uuid4().hex, "scheduled", job, runner="core"
    )
    cycle = rt.db.one(
        "SELECT local_weekday,opened_at FROM control.cycle WHERE id=%s",
        (scheduled["cycle_id"],),
    )
    assert (
        cycle["local_weekday"]
        == cycle["opened_at"].astimezone(ZoneInfo("Asia/Tokyo")).isoweekday()
    )
    manual = await rt.cycles.bind_cycle(
        "daily", scope, "manual:" + uuid4().hex, "scheduled", job, runner="core"
    )
    assert (
        rt.db.one(
            "SELECT local_weekday FROM control.cycle WHERE id=%s", (manual["cycle_id"],)
        )["local_weekday"]
        is None
    )
    global_daily = await rt.cycles.bind_cycle(
        "daily",
        "global",
        "core:" + uuid4().hex,
        "scheduled",
        "local:daily",
        runner="core",
    )
    assert (
        rt.db.one(
            "SELECT local_weekday FROM control.cycle WHERE id=%s",
            (global_daily["cycle_id"],),
        )["local_weekday"]
        is None
    )


def test_due_gate_decisions() -> None:
    ny = {
        "cadence": "daily",
        "scope": "tenant:x",
        "due_hour": 5,
        "due_weekday": None,
        "timezone": "America/New_York",
    }
    before = datetime(2026, 9, 25, 8, 30, tzinfo=UTC)  # 04:30 in New York
    after = datetime(2026, 9, 25, 9, 30, tzinfo=UTC)  # 05:30 in New York
    assert decide(ny, before, None, True)[0] is False
    assert decide(ny, after, None, True) == (True, decide(ny, after, None, True)[1])
    assert decide(ny, after, None, False) == (
        False,
        "waiting for today's global daily cycle to close",
    )
    # A scheduled cycle opened earlier this local day makes every later tick a no-op.
    assert decide(ny, after, datetime(2026, 9, 25, 9, 5, tzinfo=UTC), True)[0] is False
    assert (
        decide(ny, after, datetime(2026, 9, 25, 3, 0, tzinfo=UTC), True)[0] is True
    )  # yesterday in New York
    weekly = {
        "cadence": "weekly",
        "scope": "global",
        "due_hour": 3,
        "due_weekday": 1,
        "timezone": "UTC",
    }
    monday = datetime(2026, 9, 28, 3, 10, tzinfo=UTC)
    assert decide(weekly, monday, None, False)[0] is True
    assert decide(weekly, monday - timedelta(hours=1), None, False)[0] is False
    # A missed Monday catches up later in the ISO week, unless that week already closed one.
    assert decide(weekly, monday + timedelta(days=1), None, False)[0] is True
    assert (
        decide(weekly, monday + timedelta(days=6), monday, False)[0] is False
    )  # Sunday, the week's cycle closed on Monday
    assert (
        decide(weekly, monday, monday - timedelta(minutes=1), False)[0] is False
    )  # this week already
    assert (
        decide(weekly, monday, monday - timedelta(days=1), False)[0] is True
    )  # Sunday: last week
    hourly = {
        "cadence": "hourly",
        "scope": "global",
        "due_hour": None,
        "due_weekday": None,
        "timezone": "UTC",
    }
    # An hourly period is the last 45 minutes, not the clock hour.
    assert decide(hourly, monday, monday.replace(minute=2), False)[0] is False
    assert decide(hourly, monday, monday - timedelta(minutes=44), False)[0] is False
    assert decide(hourly, monday, monday - timedelta(minutes=46), False)[0] is True
    assert defaults("daily", "tenant:x") == (5, None) and defaults(
        "weekly", "global"
    ) == (3, 1)


async def test_due_gate_reads_the_registered_schedule_and_the_global_close(
    rt: Runtime, databases
) -> None:
    _, scope = tenant(databases, tz="UTC")
    job = f"core-daily-{scope}"
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.dbt_job SET due_hour=0 WHERE job_id=%s", (job,))
        assert check(conn, job) == (
            False,
            "waiting for today's global daily cycle to close",
        )
    binding = await rt.cycles.bind_cycle(
        "daily",
        "global",
        "core:" + uuid4().hex,
        "scheduled",
        "local:daily",
        runner="core",
    )
    rt.db.execute(
        "UPDATE control.cycle SET status='closed',closed_at=now() WHERE id=%s",
        (binding["cycle_id"],),
    )
    with psycopg.connect(databases["admin_control"]) as conn:
        assert check(conn, job)[0] is True
    tenant_cycle = await rt.cycles.bind_cycle(
        "daily", scope, "core:" + uuid4().hex, "scheduled", job, runner="core"
    )
    with psycopg.connect(databases["admin_control"]) as conn:
        # An open (failed or unfinished) cycle credits nothing; only its close does.
        assert check(conn, job)[0] is True
    rt.db.execute(
        "UPDATE control.cycle SET status='closed',closed_at=now() WHERE id=%s",
        (tenant_cycle["cycle_id"],),
    )
    with psycopg.connect(databases["admin_control"]) as conn:
        assert check(conn, job)[0] is False
        assert check(conn, "core-daily-missing") == (
            False,
            "job core-daily-missing is not registered",
        )


def test_core_runner_machines_per_cadence_and_tenant() -> None:
    spec = importlib.util.spec_from_file_location(
        "machines", ROOT / "ops/fly/core-runner/machines.py"
    )
    machines = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(machines)
    # The tenant weekly job has transform models from (the weekly call record).
    assert machines.tenant_cadences(ROOT) == []
    # One rule: the exporter's tenant cadences (its tenant export and close) are the machines' cadences.
    from mdp_functions.exporter import tenant_cadences

    assert sorted(tenant_cadences(ROOT, discover()), key=machines.CADENCES.index) == machines.tenant_cadences(ROOT)
    found = machines.machines([{"id": "t-1", "slug": "acme"}], ROOT)
    assert [m["name"] for m in found] == [
        "mdp-hourly",
        "mdp-daily",
        "mdp-weekly",
    ]
    with pytest.raises(SystemExit):
        machines.machines([{"id": "t-2", "slug": "Bad Slug"}], ROOT)


def test_a_scheduled_machine_tick_runs_only_when_its_schedule_is_due(
    rt: Runtime, databases, tmp_path
) -> None:
    """The real ops/run.sh under MDP_CORE_GATE=1: a tick off the declared weekday exits before binding,
    registration keeps the control-written schedule, and a due tick runs its phases."""
    import subprocess

    from mdp_functions.settings import REPO
    from test_runner_signals import shim

    shim(tmp_path)
    today = datetime.now(UTC).isoweekday()
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,due_hour,due_weekday,timezone) "
            "VALUES ('core-weekly-global','core','weekly','global',0,%s,'UTC')",
            (today,),
        )
        # This ISO week's weekly already closed.
        earlier = conn.execute(
            "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at,manifest_mode) "
            "VALUES ('weekly','global','core:earlier','closed',now(),'stamp') RETURNING id"
        ).fetchone()[0]
    env = os.environ | {
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "MDP_CONTROL_RT_URL": databases["admin_control"],
        "FAKE_DBT_LOG": str(tmp_path / "dbt.log"),
        "MDP_CORE_GATE": "1",
        "MDP_RUN_REASON_CATEGORY": "scheduled",
        "DBT_MDP_SCOPE": "global",
    }
    for name in ("MDP_RUN_ID", "MDP_RUNNER_LOCKED", "MDP_RESTORE"):
        env.pop(name, None)

    def tick():
        return subprocess.run(
            ["bash", str(REPO / "ops/run.sh"), "weekly"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    idle = tick()
    assert idle.returncode == 0, idle.stdout + idle.stderr
    assert (
        "GATE core-weekly-global: NOT DUE: a scheduled weekly cycle that opened at"
        in idle.stdout
    )
    assert not (tmp_path / "dbt.log").exists()
    with psycopg.connect(databases["admin_control"]) as conn:
        assert (
            conn.execute(
                "SELECT due_weekday FROM control.dbt_job WHERE job_id='core-weekly-global'"
            ).fetchone()[0]
            == today
        )
        # A cycle that never closed (a failed run) credits nothing.
        conn.execute(
            "UPDATE control.cycle SET status='open',closed_at=NULL WHERE id=%s",
            (earlier,),
        )
    due = tick()
    assert due.returncode == 0, due.stdout + due.stderr
    assert "GATE core-weekly-global: DUE weekly global" in due.stdout
    ran = [
        json.loads(line)["step"]
        for line in (tmp_path / "dbt.log").read_text().splitlines()
    ]
    assert ran[0] == "weekly_global_bronze" and ran[-1] == "weekly_global_transform"
