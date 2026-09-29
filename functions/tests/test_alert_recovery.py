"""Recovery clears only warnings with matching scheduled evidence."""

from uuid import uuid4

import httpx
import psycopg
import pytest
from mdp_functions.alert_recovery import repeated_partial
from mdp_functions.api import create_app
from mdp_functions.cadence_health import fail_cycle
from mdp_functions.health_policy import AD_HOC_CYCLE_PREFIXES

RUN_CLASSES = (
    "partial_coverage", "vendor_4xx", "vendor_retryable", "envelope_mismatch",
    "accounting_mismatch", "invoke_timeout", "deadline_expired", "service_unreachable",
    "control_api_unavailable", "function_failed",
)


def run(conn, source, *, prefix="scheduled:", cadence="daily", scope="global", coverage="full", age=0,
        work_prefix="", fixture=False):
    cycle = conn.execute(
        "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at,manifest_mode) "
        "VALUES (%s,%s,%s,now()-%s*interval '1 hour','stamp') RETURNING id",
        (cadence, scope, prefix + uuid4().hex, age),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO control.run(kind,work_key,cycle_id,scope,streamline_id,warehouse_id,status,coverage,rows_written,created_at,resolved_config) "
        "SELECT 'invoke',%s,%s,%s,id,(SELECT id FROM control.warehouse WHERE is_production),'succeeded',%s,1,"
        "now()-%s*interval '1 hour',jsonb_build_object('fixture',%s::boolean) "
        "FROM control.streamline WHERE source_key=%s RETURNING id,cycle_id",
        (work_prefix + uuid4().hex, cycle, scope, coverage, age, fixture, source),
    ).fetchone()


def warning(conn, kind, old, *, subject_type="run", subject=None, severity="warning"):
    return conn.execute(
        "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,opened_at,updated_at,acknowledged_by) "
        "VALUES (%s,%s,%s,%s,%s,now()-interval '1 hour',now()-interval '1 hour','fixture-operator') RETURNING id",
        (kind, severity, subject_type, str(subject or old), old),
    ).fetchone()[0]


@pytest.mark.parametrize("kind", RUN_CLASSES)
def test_each_run_warning_resolves_once_with_actor_and_reason(rt, databases, kind):
    with psycopg.connect(databases["admin_control"]) as conn:
        old, _ = run(conn, "fixture_accounts", coverage="partial", age=2)
        alert_id = warning(conn, kind, old)
        recovered, _ = run(conn, "fixture_accounts")
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    alert = rt.db.one("SELECT * FROM control.alert WHERE id=%s", (alert_id,))
    assert alert["resolved_at"] and alert["resolved_by"] == "system:recovery"
    assert str(recovered) in alert["resolution_reason"]
    audits = rt.db.all("SELECT * FROM control.audit_log WHERE subject=%s", (str(alert_id),))
    assert len(audits) == 1
    assert audits[0]["actor"] == alert["resolved_by"]
    assert audits[0]["after"]["reason"] == alert["resolution_reason"]


@pytest.mark.parametrize("change", [
    {"prefix": prefix} for prefix in AD_HOC_CYCLE_PREFIXES
] + [
    {"cadence": "weekly"}, {"scope": "tenant:fixture"}, {"coverage": "partial"},
    {"coverage": "empty"}, {"work_prefix": "manual:"}, {"fixture": True},
])
def test_other_work_cannot_clear_scheduled_warnings(rt, databases, change):
    with psycopg.connect(databases["admin_control"]) as conn:
        old, _ = run(conn, "fixture_accounts", coverage="partial", age=2)
        alert_id = warning(conn, "partial_coverage", old)
        recovered, _ = run(conn, "fixture_accounts", **change)
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"] is None


def test_recovery_leaves_other_source_target_and_critical_alerts(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        other, _ = run(conn, "billboard_hot100", age=2)
        old, _ = run(conn, "fixture_accounts", age=2)
        ids = [warning(conn, "partial_coverage", other),
               warning(conn, "target_zero_yield", old, subject_type="target"),
               warning(conn, "surface_drift", old, severity="critical"),
               warning(conn, "inputs_parked", old)]
        recovered, _ = run(conn, "fixture_accounts")
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert all(a["resolved_at"] is None for a in rt.db.all("SELECT resolved_at FROM control.alert WHERE id=ANY(%s)", (ids,)))


@pytest.mark.parametrize("outcomes,clears", [([], False), (["failed"], False), (["skipped"], False),
    (["not_due"], False), (["passed", "failed"], False), (["passed"], True)])
def test_canary_warning_needs_latest_passing_probe_and_full_scheduled_run(rt, databases, outcomes, clears):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.audit_log WHERE action='source.canary' AND subject='fixture_accounts:global'")
        alert_id = warning(conn, "source_canary_failed", None, subject_type="streamline", subject="fixture_accounts:global")
        for index, outcome in enumerate(outcomes):
            conn.execute("INSERT INTO control.audit_log(actor,action,subject,after,at) "
                         "VALUES ('deploy','source.canary','fixture_accounts:global',jsonb_build_object('status',%s::text),"
                         "now()-interval '30 minutes'+%s*interval '1 second')", (outcome, index))
        recovered, _ = run(conn, "fixture_accounts")
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert bool(rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"]) is clears


@pytest.mark.parametrize("prefix", ["scheduled:", *AD_HOC_CYCLE_PREFIXES])
async def test_cycle_close_clears_matching_cadence_and_job_alerts(rt, databases, prefix):
    with psycopg.connect(databases["admin_control"]) as conn:
        old, old_cycle = run(conn, "fixture_accounts", age=2)
        ids = [warning(conn, "cadence_failed", old, subject_type="cycle", subject=old_cycle, severity="critical")]
        conn.execute("INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('recovery-daily','core','daily','global')")
        for subject in ("recovery-daily", "recovery-daily@2026-01-01T00:00:00+00:00"):
            ids.append(warning(conn, "cadence_failed", None, subject_type="dbt_job", subject=subject, severity="critical"))
        _, other_cycle = run(conn, "fixture_accounts", cadence="weekly", age=2)
        untouched = warning(conn, "cadence_failed", None, subject_type="cycle", subject=other_cycle, severity="critical")
        _, recovered = run(conn, "fixture_accounts", prefix=prefix)
    rt.cycles.close(recovered)
    rt.cycles.close(recovered)
    await complete(rt, databases, recovered)
    for alert in rt.db.all("SELECT resolved_at,resolved_by FROM control.alert WHERE id=ANY(%s)", (ids,)):
        assert bool(alert["resolved_at"]) is (prefix == "scheduled:")
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (untouched,))["resolved_at"] is None


@pytest.mark.parametrize("intervening", [None, "manual:", "canary:", "backfill:", "scheduled:"])
def test_consecutive_partial_ignores_ad_hoc_runs_but_full_scheduled_breaks_streak(rt, databases, intervening):
    with psycopg.connect(databases["admin_control"]) as conn:
        run(conn, "fixture_accounts", coverage="partial", age=3)
        if intervening:
            run(conn, "fixture_accounts", prefix=intervening, age=2)
        current, _ = run(conn, "fixture_accounts", coverage="partial")
    with rt.db.transaction() as conn:
        assert repeated_partial(conn, {"id": current}) is (intervening != "scheduled:")


def test_runtime_cannot_directly_resolve_or_forge_an_audit(rt):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rt.db.execute("UPDATE control.alert SET resolved_by='forged'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rt.db.execute("INSERT INTO control.audit_log(actor,action,subject) VALUES ('forged','alerts.resolve','x')")


def test_a_fresh_successful_attempt_resolves_its_own_earlier_warning(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        recovered, _ = run(conn, "fixture_accounts", age=2)
        alert_id = warning(conn, "vendor_retryable", recovered)
        conn.execute("INSERT INTO control.run_attempt(run_id,attempt_no,deadline_at,status) "
                     "VALUES (%s,2,now()+interval '1 hour','succeeded')", (recovered,))
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"]


async def complete(rt, databases, cycle, status="succeeded", run_id=None, reason="scheduled"):
    with psycopg.connect(databases["admin_control"]) as conn:
        opener = conn.execute("SELECT opened_by_dbt_run_id FROM control.cycle WHERE id=%s", (cycle,)).fetchone()[0]
        run_id = run_id or opener
        conn.execute("INSERT INTO control.dbt_job(job_id,runner,cadence,scope) "
                     "SELECT 'recovery-'||cadence,'core',cadence,scope FROM control.cycle WHERE id=%s "
                     "ON CONFLICT(job_id) DO NOTHING", (cycle,))
        job = conn.execute("SELECT 'recovery-'||cadence FROM control.cycle WHERE id=%s", (cycle,)).fetchone()[0]
        conn.execute("INSERT INTO control.cycle_attempt(cycle_id,dbt_run_id,job_id,reason_category,runner) "
                     "VALUES (%s,%s,%s,%s,'core') ON CONFLICT DO NOTHING", (cycle, run_id, job, reason))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
                                 base_url="http://test", headers={"Authorization": "Bearer " + rt.settings.service_token}) as client:
        result = await client.post("/v1/dbt/webhook", json={"event_id": run_id + status, "run_id": run_id,
                                   "job_id": job, "status": status})
    assert result.status_code == 200, result.text


async def test_bronze_close_does_not_hide_a_later_transform_failure(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        _, cycle = run(conn, "fixture_accounts")
    with rt.db.transaction() as conn:
        overdue = fail_cycle(conn, cycle)
    rt.cycles.close(cycle)
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (overdue,))["resolved_at"] is None
    with rt.db.transaction() as conn:
        assert fail_cycle(conn, cycle, "transform", "build failed") == overdue
    await complete(rt, databases, cycle, "failed")
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (overdue,))["resolved_at"] is None
    with psycopg.connect(databases["admin_control"]) as conn:
        _, next_cycle = run(conn, "fixture_accounts")
    rt.cycles.close(next_cycle)
    await complete(rt, databases, next_cycle)
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (overdue,))["resolved_at"]


async def test_a_failure_after_resolution_opens_a_fresh_warning(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        _, cycle = run(conn, "fixture_accounts")
    with rt.db.transaction() as conn:
        first = fail_cycle(conn, cycle)
    rt.cycles.close(cycle)
    await complete(rt, databases, cycle)
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (first,))["resolved_at"]
    with rt.db.transaction() as conn:
        second = fail_cycle(conn, cycle, "transform", "later failure")
        assert fail_cycle(conn, cycle) == second
    assert second != first
    await complete(rt, databases, cycle)  # A duplicate old success cannot erase a new failure.
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (second,))["resolved_at"] is None


@pytest.mark.parametrize("change", [{"fixture": True}, {"work_prefix": "manual:"}, {"prefix": "local:", "fixture": True}])
async def test_fixture_builds_never_resolve_real_cadence_failures(rt, databases, change):
    # A scheduled-looking cycle whose only runs are fixtures (or manual) proves nothing about the cadence.
    with psycopg.connect(databases["admin_control"]) as conn:
        _, old = run(conn, "fixture_accounts", age=2)
        alert_id = warning(conn, "cadence_failed", None, subject_type="cycle", subject=old, severity="critical")
        _, cycle = run(conn, "fixture_accounts", **change)
    rt.cycles.close(cycle)
    await complete(rt, databases, cycle)
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"] is None
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.run(kind,work_key,cycle_id,scope,streamline_id,warehouse_id,status,coverage,rows_written,resolved_config) "
            "SELECT 'invoke',%s,%s,'global',id,(SELECT id FROM control.warehouse WHERE is_production),'succeeded','full',1,'{}' "
            "FROM control.streamline WHERE source_key='fixture_accounts'", (uuid4().hex, cycle))
    await complete(rt, databases, cycle)  # One real run in the same cycle is enough.
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"]


async def test_success_needs_the_close_mirror_before_recovery(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        _, cycle = run(conn, "fixture_accounts")
    with rt.db.transaction() as conn:
        alert_id = fail_cycle(conn, cycle)
    rt.cycles.close(cycle)
    rt.db.execute("UPDATE control.scope_close SET mirrored_close_no=-1 WHERE scope='global'")
    await complete(rt, databases, cycle)
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"] is None
    rt.cycles.close(cycle)
    await complete(rt, databases, cycle)
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"]


async def test_failed_canary_notification_blocks_recovery_before_its_audit(rt, databases):
    subject = "fixture_accounts:global"
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.audit_log WHERE action='source.canary' AND subject=%s", (subject,))
        alert_id = warning(conn, "source_canary_failed", None, subject_type="streamline", subject=subject)
        conn.execute("INSERT INTO control.audit_log(actor,action,subject,after,at) "
                     "VALUES ('deploy','source.canary',%s,'{\"status\":\"passed\"}',now()-interval '30 minutes')", (subject,))
        recovered, _ = run(conn, "fixture_accounts")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
                                 base_url="http://test", headers={"Authorization": "Bearer " + rt.settings.service_token}) as client:
        response = await client.post("/v1/alerts/canary_failed", json={"source_key": "fixture_accounts", "scope": "global"})
    assert response.status_code == 200
    # The failed probe's notification committed, but its audit has not been written yet.
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"] is None
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.audit_log(actor,action,subject,after) "
                     "VALUES ('deploy','source.canary',%s,'{\"status\":\"failed\"}')", (subject,))
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"] is None
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.audit_log(actor,action,subject,after) "
                     "VALUES ('deploy','source.canary',%s,'{\"status\":\"passed\"}')", (subject,))
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"]


async def test_retry_build_that_closes_the_cycle_resolves_its_cadence_alerts(rt, databases):
    """The scheduled build failed and the next bind superseded nothing yet; a Retry closed the current
    cycle and passed. Its success resolves the current cycle's alert and the superseded cycle's alert."""
    with psycopg.connect(databases["admin_control"]) as conn:
        _, superseded = run(conn, "fixture_accounts", age=2)
        conn.execute("UPDATE control.cycle SET status='superseded' WHERE id=%s", (superseded,))
        _, current = run(conn, "fixture_accounts", age=1)
    with rt.db.transaction() as conn:
        ids = [fail_cycle(conn, superseded, "bronze_invoke__fixture_accounts", "invoke_timeout"),
               fail_cycle(conn, current, "bronze_invoke__fixture_accounts", "invoke_timeout")]
    await complete(rt, databases, current, "failed")
    rt.cycles.close(current)
    await complete(rt, databases, current, run_id="core:" + uuid4().hex, reason="other")
    for alert in rt.db.all("SELECT resolved_at,resolved_by FROM control.alert WHERE id=ANY(%s)", (ids,)):
        assert alert["resolved_at"] and alert["resolved_by"] == "system:recovery"


async def test_replay_of_an_older_cycle_resolves_nothing(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        _, older = run(conn, "fixture_accounts", age=2)
        run(conn, "fixture_accounts", age=1)  # the current cycle, newer than the replayed one
    with rt.db.transaction() as conn:
        alert_id = fail_cycle(conn, older, "bronze_invoke__fixture_accounts", "invoke_timeout")
    rt.cycles.close(older)
    await complete(rt, databases, older, "failed")
    await complete(rt, databases, older, run_id="core:" + uuid4().hex, reason="other")
    assert rt.db.one("SELECT resolved_at FROM control.alert WHERE id=%s", (alert_id,))["resolved_at"] is None
