"""Tests for tenant schedule edges."""

import zoneinfo
from datetime import datetime, timedelta, timezone
from importlib import metadata
from uuid import uuid4

import psycopg
import pytest
import tomllib
from mdp_functions.core_gate import check, decide
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO

UTC = timezone.utc
from tenant_schedule_fixture import tenant


def test_tenant_timezones_load_without_system_tzdata() -> None:
    """The slim runner image has no legacy or backward-link zone files: the tzdata package supplies them,
    for every platform."""
    lock = tomllib.loads((REPO / "functions/uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "mdp-functions")
    assert {"name": "tzdata"} in package["dependencies"]
    assert metadata.version("tzdata")
    zoneinfo.reset_tzpath(to=[])
    try:
        for name in ("Asia/Kolkata", "Europe/Kyiv", "Asia/Calcutta", "Europe/Kiev"):
            assert zoneinfo.ZoneInfo.no_cache(name).key == name
    finally:
        zoneinfo.reset_tzpath()
    for zone, before, after in (
        # 05:00 local is 23:30 UTC the day before in Kolkata (+05:30) and 02:00 UTC in Kyiv (+03:00).
        (
            "Asia/Kolkata",
            datetime(2026, 9, 24, 23, 0, tzinfo=UTC),
            datetime(2026, 9, 24, 23, 40, tzinfo=UTC),
        ),
        (
            "Europe/Kyiv",
            datetime(2026, 9, 25, 1, 50, tzinfo=UTC),
            datetime(2026, 9, 25, 2, 10, tzinfo=UTC),
        ),
    ):
        job = {
            "cadence": "daily",
            "scope": "tenant:x",
            "due_hour": 5,
            "due_weekday": None,
            "timezone": zone,
        }
        assert decide(job, before, None, True)[0] is False
        assert decide(job, after, None, True)[0] is True


def test_the_gate_uses_the_defaults_for_null_schedule_fields() -> None:
    """A job registered before the schedule columns keeps nulls; it runs on the declared defaults."""
    daily = {
        "cadence": "daily",
        "scope": "global",
        "due_hour": None,
        "due_weekday": None,
        "timezone": "UTC",
    }
    assert (
        decide(daily, datetime(2026, 9, 25, 1, 30, tzinfo=UTC), None, False)[0] is False
    )
    assert (
        decide(daily, datetime(2026, 9, 25, 2, 30, tzinfo=UTC), None, False)[0] is True
    )
    weekly = {**daily, "cadence": "weekly"}
    monday = datetime(2026, 9, 28, tzinfo=UTC)
    assert decide(weekly, monday.replace(hour=0, minute=30), None, False)[0] is False
    assert decide(weekly, monday.replace(hour=3, minute=10), None, False)[0] is True
    # A weekly that missed Monday catches up on Tuesday.
    assert (
        decide(weekly, monday.replace(hour=3) + timedelta(days=1), None, False)[0]
        is True
    )


@pytest.mark.docker
async def test_an_inactive_tenant_is_never_due_and_never_sent(
    rt: Runtime, databases, tmp_path, monkeypatch
) -> None:
    tenant_id, scope = tenant(databases, slug="gone-quiet", tz="UTC")
    job = f"core-daily-{scope}"
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
        conn.execute("UPDATE control.dbt_job SET due_hour=0 WHERE job_id=%s", (job,))
        assert check(conn, job)[0] is True
        conn.execute(
            "UPDATE control.tenant SET status='inactive' WHERE id=%s", (tenant_id,)
        )
        assert check(conn, job) == (False, "the tenant is inactive")
