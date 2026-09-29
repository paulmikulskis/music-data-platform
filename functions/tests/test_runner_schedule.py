"""Tests for runner schedule."""

import json
import os
import random
import subprocess
from datetime import datetime, time, timedelta, timezone
from itertools import pairwise

import psycopg
import pytest
from mdp_functions.core_gate import decide
from mdp_functions.settings import REPO

UTC = timezone.utc
HOURLY = {
    "cadence": "hourly",
    "scope": "global",
    "due_hour": None,
    "due_weekday": None,
    "timezone": "UTC",
}


def clock_hour_rule(now, last):
    """The previous hourly rule: one scheduled cycle per clock hour, credited by its open time."""
    return last is None or last < now.replace(minute=0, second=0, microsecond=0)


def simulate(due, phase_s, fuzz_s, bind_s, seed, hours=13 * 24):
    """Hourly Fly ticks at `phase_s` past each hour, each moved by up to ±fuzz_s, for 13 days. A due tick
    opens its cycle `bind_s` later and closes it 90 s after that. Returns the refused ticks and the
    longest gap between consecutive cycle opens."""
    rng = random.Random(seed)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    ticks = sorted(
        start + timedelta(seconds=h * 3600 + phase_s + rng.uniform(-fuzz_s, fuzz_s))
        for h in range(hours)
    )
    last, refused, opens = None, 0, []
    for tick in ticks:
        if due(tick, last):
            opened = tick + timedelta(seconds=bind_s)
            opens.append(opened)
            last = opened  # it closes 90 s later, before the next tick
        else:
            refused += 1
    gaps = [(b - a).total_seconds() for a, b in pairwise(opens)]
    return refused, max(gaps)


@pytest.mark.parametrize(
    ("fuzz_s", "bind_s"), [(120, 8), (120, 60), (300, 8), (300, 60)]
)
def test_the_hourly_gate_loses_no_hour_to_fuzzy_ticks(fuzz_s, bind_s) -> None:
    """The reviewer's simulation: 120 tick phases, 13 days each. No tick is refused, so no hour is lost,
    and consecutive cycles are never further apart than the tick spacing allows."""

    def gate(tick, last):
        return decide(HOURLY, tick, last, False)[0]

    old_losses = 0
    for phase in range(0, 3600, 30):
        refused, gap = simulate(gate, phase, fuzz_s, bind_s, seed=phase)
        assert refused == 0, (phase, refused)
        assert gap <= 3600 + 2 * fuzz_s, (phase, gap)
        old_losses += simulate(clock_hour_rule, phase, fuzz_s, bind_s, seed=phase)[0]
    # The clock-hour rule loses hours on the same ticks, so the simulation would catch a regression.
    assert old_losses > 0


def test_the_weekly_catches_up_until_its_local_iso_week_ends() -> None:
    weekly = {
        "cadence": "weekly",
        "scope": "tenant:x",
        "due_hour": 5,
        "due_weekday": 1,
        "timezone": "America/Los_Angeles",
    }
    # Monday 2026-09-28 05:00 in Los Angeles is 12:00 UTC.
    monday = datetime(2026, 9, 28, 12, 10, tzinfo=UTC)
    assert decide(weekly, monday - timedelta(minutes=20), None, False)[0] is False
    assert decide(weekly, monday, None, False)[0] is True
    # Every Monday tick failed: Wednesday and Sunday evening (local) still run it.
    for later in (timedelta(days=2), timedelta(days=6, hours=10)):
        assert decide(weekly, monday + later, None, False)[0] is True
    # Once that week's weekly closed, no later tick in the week is due; the next Monday is.
    assert (
        decide(weekly, monday + timedelta(days=3), monday + timedelta(days=2), False)[0]
        is False
    )
    next_monday = monday + timedelta(days=7)
    assert decide(weekly, next_monday, monday + timedelta(days=2), False)[0] is True
    # Sunday 23:00 UTC is still Sunday 16:00 in Los Angeles: the local week decides.
    sunday_utc = datetime.combine(
        (monday + timedelta(days=6)).date(), time(23), tzinfo=UTC
    )
    assert decide(weekly, sunday_utc, None, False)[0] is True


def tick_env(databases, tmp_path, **extra):
    env = os.environ | {
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "MDP_CONTROL_RT_URL": databases["admin_control"],
        "FAKE_DBT_LOG": str(tmp_path / "dbt.log"),
        "DBT_MDP_SCOPE": "global",
        **extra,
    }
    for name in ("MDP_RUN_ID", "MDP_RUNNER_LOCKED", "MDP_RESTORE", "MDP_CORE_GATE"):
        if name not in extra:
            env.pop(name, None)
    return env


def run_sh(env, *args):
    return subprocess.run(
        ["bash", str(REPO / "ops/run.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def dbt_steps(tmp_path):
    path = tmp_path / "dbt.log"
    return (
        [json.loads(line)["step"] for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def test_a_gated_tick_waits_behind_an_unnamed_lock_holder(
    rt, databases, tmp_path
) -> None:
    """A holder that does not name itself (a previous image's run) is not this period's scheduled run: a
    gated tick waits for it, bounded, and exits runner_busy with no dbt step; so does an ungated run."""
    from test_runner_signals import shim

    shim(tmp_path)
    with psycopg.connect(databases["admin_control"], autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtext('core:hourly:global'))")
        for extra in (
            {
                "MDP_CORE_GATE": "1",
                "MDP_RUN_REASON_CATEGORY": "scheduled",
                "MDP_RUNNER_LOCK_WAIT_S": "2",
            },
            {"MDP_RUNNER_LOCK_WAIT_S": "0"},
        ):
            waited = run_sh(tick_env(databases, tmp_path, **extra), "hourly")
            assert waited.returncode == 75 and "runner_busy" in waited.stderr, (
                waited.stdout + waited.stderr
            )
            assert "NOT DUE" not in waited.stdout
    assert not dbt_steps(tmp_path)


def test_the_restore_run_rebuilds_the_newest_scheduled_cycle_and_skips_when_there_is_none(
    rt, databases, tmp_path
) -> None:
    """The deploy's rebuild machine runs `run.sh <cadence>` with MDP_RESTORE=1 and reason other: with no
    scheduled cycle the previous release built nothing, so it exits 0 at once; with one, it runs the full
    job ungated (bronze attaches, transform rebuilds)."""
    from test_runner_signals import shim

    shim(tmp_path)
    env = tick_env(
        databases,
        tmp_path,
        MDP_RESTORE="1",
        MDP_RUN_REASON_CATEGORY="other",
        MDP_CORE_GATE="1",
    )
    with psycopg.connect(databases["admin_control"]) as conn:
        for prefix in ("manual:", "backfill:", "canary:"):
            conn.execute(
                "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at,manifest_mode) "
                "VALUES ('weekly','global',%s,'closed',now(),'stamp')", (prefix + "fixture",),
            )
    empty = run_sh(env, "weekly")
    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert (
        "RESTORE SKIPPED weekly; scope=global: no scheduled cycle to rebuild"
        in empty.stdout
    )
    assert not dbt_steps(tmp_path)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at,manifest_mode) "
            "VALUES ('weekly','global','core:main-weekly','closed',now(),'stamp')"
        )
    rebuilt = run_sh(env, "weekly")
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    # Ungated even with MDP_CORE_GATE=1: a restore never asks the gate.
    assert "GATE" not in rebuilt.stdout
    ran = dbt_steps(tmp_path)
    assert ran[0] == "weekly_global_bronze" and ran[-1] == "weekly_global_transform"


@pytest.mark.parametrize("failed_phase", [None, "bronze", "transform"])
def test_success_notification_requires_every_build_phase(databases, tmp_path, failed_phase):
    from test_runner_signals import shim

    shim(tmp_path)
    fake = tmp_path / "bin/fake_dbt.py"
    fake.write_text(fake.read_text() + "\nif os.environ.get('FAIL_PHASE') and step.endswith('_' + os.environ['FAIL_PHASE']):\n    sys.exit(1)\n")
    success = tmp_path / "success.log"
    result = run_sh(tick_env(databases, tmp_path, FAKE_SUCCESS_LOG=str(success), FAIL_PHASE=failed_phase or ""), "daily")
    assert (result.returncode == 0) is (failed_phase is None), result.stdout + result.stderr
    assert success.exists() is (failed_phase is None)
    if success.exists():
        assert len(success.read_text().splitlines()) == 1


def test_a_lost_success_notice_does_not_fail_a_passed_build(databases, tmp_path):
    from test_runner_signals import shim

    shim(tmp_path)
    success = tmp_path / "success.log"
    result = run_sh(tick_env(databases, tmp_path, FAKE_SUCCESS_LOG=str(success), FAKE_SUCCESS_EXIT="1"), "daily")
    assert result.returncode == 0, result.stdout + result.stderr
    assert success.exists()
    assert "BUILD success not recorded" in result.stderr
    assert "cadence_failed" not in result.stdout + result.stderr
