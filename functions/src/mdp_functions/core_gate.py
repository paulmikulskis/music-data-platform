"""Core's due gate. Every core-runner machine wakes on a fuzzy hourly Fly tick; a tick
runs its cadence only when the job's declared dbt_job schedule is due and its period is not yet
credited. Only a closed scheduled cycle credits a period. A scheduled cycle that failed (left open, or
superseded) gets one retry, the next tick; after a second failure in the period the gate is not due
until the next period, and the runner opens a `cadence_failed` alert through the service, so a failing
cadence never re-collects its paid sources every hour. An hourly period is the last 45 minutes, not
the clock hour, so two fuzzy ticks in one clock hour never cost the next hour. A daily is due from
`due_hour` in the local day; a weekly is due from (`due_weekday`, `due_hour`) until the local ISO week
ends, so a missed Monday catches up. A tenant daily tick also waits for the global daily cycle of the
current UTC day to close, and a tenant weekly tick for the tenant's own daily cycle of the current local day
(the Monday call freeze reads that day's build); an inactive tenant's tick is never due. Every other
tick exits before
binding. A tick finds the runner lock free, or waits for it behind an operator's Retry or Replay; it
skips at once only behind this period's scheduled run (runner_lock.py, `holds_this_period`).

`python -m mdp_functions.core_gate <job_id>` prints DUE or NOT DUE with the reason and exits 0 or 3.
"""

import os
import sys
from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from mdp_functions.fetch.guard import transport_network
from mdp_functions.health_policy import scheduled_cycle_sql

NOT_DUE = 3
# (due_hour, due_weekday) a new registration starts with; the control plane owns them afterwards.
# The global daily job runs first; the tenant daily job is due early enough to finish before 09:00
# tenant time, for scheduled work. Weekly jobs are due on Monday (ISO 1).
DEFAULTS = {
    ("hourly", "global"): (None, None),
    ("daily", "global"): (2, None),
    ("weekly", "global"): (3, 1),
    ("hourly", "tenant"): (None, None),
    ("daily", "tenant"): (5, None),
    ("weekly", "tenant"): (5, 1),
}
SCHEDULED = scheduled_cycle_sql()
# Fly's hourly ticks drift a few minutes either way, so consecutive ticks are at least ~50 minutes apart.
HOURLY_CREDIT = timedelta(minutes=45)
# Failed scheduled cycles a period tolerates: the first failure's retry runs, the second stops it.
FAILED_CAP = 2
CADENCE_FAILED = "cadence_failed"


def defaults(cadence: str, scope: str) -> tuple[int | None, int | None]:
    return DEFAULTS[(cadence, "global" if scope == "global" else "tenant")]


def period_start(cadence: str, local: datetime) -> datetime:
    """The start of the tick's local day or local ISO week, as an aware local time."""
    day = datetime.combine(local.date(), time(), tzinfo=local.tzinfo)
    return day if cadence == "daily" else day - timedelta(days=local.isoweekday() - 1)


def credit_start(job: dict[str, Any], now: datetime) -> datetime:
    """Where the job's current period starts: the last 45 minutes, or the local day or ISO week."""
    if job["cadence"] == "hourly":
        return now - HOURLY_CREDIT
    return period_start(
        job["cadence"], now.astimezone(ZoneInfo(job.get("timezone") or "UTC"))
    )


def decide(
    job: dict[str, Any],
    now: datetime,
    last_scheduled: datetime | None,
    global_daily_closed: bool,
    tenant_active: bool = True,
    failed: Iterable[datetime] = (),
    tenant_daily_closed: bool = True,
) -> tuple[bool, str]:
    """Whether a tick at `now` (aware) is due for the job, and why. `last_scheduled` is the open time of
    the newest closed scheduled cycle of the job's (cadence, scope); `failed` holds the open times of
    its scheduled cycles that never closed; `tenant_daily_closed` says whether a scheduled daily cycle of
    the job's tenant scope that opened in the current local day has closed."""
    cadence, scope = job["cadence"], job["scope"]
    if not tenant_active:
        return False, "the tenant is inactive"
    local = now.astimezone(ZoneInfo(job.get("timezone") or "UTC"))
    # A row registered before these columns existed keeps nulls; it runs on the defaults.
    fallback = defaults(cadence, scope)
    due_hour = fallback[0] if job.get("due_hour") is None else job["due_hour"]
    due_weekday = fallback[1] if job.get("due_weekday") is None else job["due_weekday"]
    if cadence == "weekly" and (local.isoweekday(), local.hour) < (
        due_weekday or 1,
        due_hour or 0,
    ):
        return (
            False,
            (
                f"weekly job is due from ISO weekday {due_weekday or 1} {due_hour or 0:02d}:00 "
                f"{local.tzinfo}, now weekday {local.isoweekday()} {local:%H:%M}"
            ),
        )
    if cadence == "daily" and local.hour < (due_hour or 0):
        return False, f"due at {due_hour or 0:02d}:00 {local.tzinfo}, now {local:%H:%M}"
    start = credit_start(job, now)
    if last_scheduled is not None and last_scheduled >= start:
        return (
            False,
            (
                f"a scheduled {cadence} cycle that opened at {last_scheduled.isoformat()} closed "
                f"in the period starting {start.isoformat()}"
            ),
        )
    failures = [opened for opened in failed if opened >= start]
    if len(failures) >= FAILED_CAP:
        return (
            False,
            (
                f"{CADENCE_FAILED}: {len(failures)} scheduled {cadence} cycles failed in the period "
                f"starting {start.isoformat()}; the next period runs again"
            ),
        )
    if cadence == "daily" and scope != "global" and not global_daily_closed:
        return False, "waiting for today's global daily cycle to close"
    if cadence == "weekly" and scope != "global" and not tenant_daily_closed:
        return False, "waiting for today's tenant daily cycle to close"
    return True, f"{cadence} {scope} due in the period starting {start.isoformat()}"


def load_job(conn: Any, job_id: str) -> dict[str, Any] | None:
    job = conn.execute(
        "SELECT cadence,scope,due_hour,due_weekday,timezone FROM control.dbt_job WHERE job_id=%s",
        (job_id,),
    ).fetchone()
    return (
        None
        if job is None
        else dict(
            zip(
                ("cadence", "scope", "due_hour", "due_weekday", "timezone"),
                job,
                strict=True,
            )
        )
    )


def holds_this_period(
    conn: Any, cadence: str, scope: str, started_at: datetime, now: datetime
) -> bool:
    """Whether a scheduled run that took the runner lock at `started_at` runs the current period."""
    job = load_job(conn, f"core-{cadence}-{scope}") or {"cadence": cadence}
    return started_at >= credit_start(job, now)


def check(conn: Any, job_id: str, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    job = load_job(conn, job_id)
    if job is None:
        return False, f"job {job_id} is not registered"
    last = conn.execute(
        f"SELECT max(opened_at) FROM control.cycle WHERE cadence=%s AND scope=%s AND status='closed' AND {SCHEDULED}",
        (job["cadence"], job["scope"]),
    ).fetchone()[0]
    failed = [
        row[0]
        for row in conn.execute(
            f"SELECT opened_at FROM control.cycle WHERE cadence=%s AND scope=%s AND status<>'closed' "
            f"AND opened_at>=%s AND {SCHEDULED}",
            (job["cadence"], job["scope"], now - timedelta(days=8)),
        ).fetchall()
    ]
    utc_day = datetime.combine(
        now.astimezone(timezone.utc).date(), time(), tzinfo=timezone.utc
    )
    closed = conn.execute(
        f"SELECT count(*) FROM control.cycle WHERE cadence='daily' AND scope='global' AND status='closed' "
        f"AND opened_at>=%s AND {SCHEDULED}",
        (utc_day,),
    ).fetchone()[0]
    local_day = period_start("daily", now.astimezone(ZoneInfo(job.get("timezone") or "UTC")))
    tenant_daily = conn.execute(
        f"SELECT count(*) FROM control.cycle WHERE cadence='daily' AND scope=%s AND status='closed' "
        f"AND opened_at>=%s AND {SCHEDULED}",
        (job["scope"], local_day),
    ).fetchone()[0]
    active = job["scope"] == "global" or bool(
        conn.execute(
            "SELECT 1 FROM control.tenant WHERE 'tenant:'||id::text=%s AND status='active'",
            (job["scope"],),
        ).fetchone()
    )
    return decide(job, now, last, closed > 0, active, failed, tenant_daily > 0)


def open_cadence_failed(
    conn: Any, job_id: str, period_start: datetime
) -> tuple[str, bool]:
    """The period's one `cadence_failed` alert, opened by the service as functions_rt.
    Returns its id and whether this call opened it."""
    cycle_alert = conn.execute(
        "SELECT a.id FROM control.alert a JOIN control.cycle c ON a.subject_type='cycle' AND a.subject_id=c.id::text "
        "JOIN control.dbt_job j ON j.cadence=c.cadence AND j.scope=c.scope "
        "WHERE a.class='cadence_failed' AND j.job_id=%s AND c.opened_at>=%s ORDER BY c.opened_at DESC LIMIT 1",
        (job_id, period_start),
    ).fetchone()
    if cycle_alert:
        return str(cycle_alert["id"]), False
    subject = f"{job_id}@{period_start.isoformat()}"
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{CADENCE_FAILED}:{subject}",)
    )
    found = conn.execute(
        "SELECT id FROM control.alert WHERE class=%s AND subject_type='dbt_job' AND subject_id=%s",
        (CADENCE_FAILED, subject),
    ).fetchone()
    if found:
        return str(found["id"]), False
    opened = conn.execute(
        "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) "
        "VALUES (%s,'critical','dbt_job',%s,'cadence-failed') RETURNING id",
        (CADENCE_FAILED, subject),
    ).fetchone()
    return str(opened["id"]), True


@transport_network()
def report_cadence_failed(job_id: str, period_start: datetime) -> str:
    """Ask the service to open the period's alert; a failure is printed, and the next tick asks again."""
    import httpx

    url, token = os.environ.get("MDP_SERVICE_URL"), os.environ.get("MDP_SERVICE_TOKEN")
    if not url or not token:
        return (
            "ALERT cadence_failed not sent: set MDP_SERVICE_URL and MDP_SERVICE_TOKEN"
        )
    try:
        response = httpx.post(
            f"{url.rstrip('/')}/v1/alerts/cadence_failed",
            json={"job_id": job_id, "period_start": period_start.isoformat()},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        return f"ALERT cadence_failed not sent ({error.__class__.__name__}); the next tick sends it"
    return f"ALERT cadence_failed {response.json()['alert_id']}"


def main() -> int:
    import psycopg

    now = datetime.now(timezone.utc)
    with psycopg.connect(os.environ["MDP_CONTROL_RT_URL"]) as conn:
        due, reason = check(conn, sys.argv[1], now)
        job = load_job(conn, sys.argv[1])
    print(("DUE " if due else "NOT DUE: ") + reason)
    if not due and job and reason.startswith(CADENCE_FAILED):
        print(report_cadence_failed(sys.argv[1], credit_start(job, now)))
    return 0 if due else NOT_DUE


if __name__ == "__main__":
    raise SystemExit(main())
