"""One failure alert per cycle, from runner exits and the recovery sweep."""

import json
import os
import sys
from pathlib import Path

import httpx

from mdp_functions.control_db import event
from mdp_functions.errors import ServiceError
from mdp_functions.health_policy import cadence_overdue_sql, scheduled_cycle_sql


def fail_cycle(conn, cycle_id, model="runner", error="cadence interval exceeded"):
    cycle = conn.execute("SELECT * FROM control.cycle WHERE id=%s FOR UPDATE", (cycle_id,)).fetchone()
    if not cycle:
        raise ServiceError("cycle_not_found", "Cycle does not exist", 404)
    found = conn.execute(
        "SELECT id FROM control.alert WHERE class='cadence_failed' AND subject_type='cycle' AND subject_id=%s AND resolved_at IS NULL",
        (str(cycle_id),),
    ).fetchone()
    if found:
        return str(found["id"])
    failed = conn.execute(
        "SELECT r.id,s.source_key,r.error_class FROM control.run r LEFT JOIN control.streamline s ON s.id=r.streamline_id "
        "WHERE r.cycle_id=%s AND r.error_class IS NOT NULL ORDER BY r.updated_at DESC LIMIT 1", (cycle_id,),
    ).fetchone()
    if failed:
        if model == "runner":
            model = failed["source_key"] or model
        if error == "cadence interval exceeded":
            error += ": " + failed["error_class"]
    record = conn.execute(
        "INSERT INTO control.run(kind,work_key,cycle_id,scope,warehouse_id,status,error_class,error_message) "
        "SELECT 'dbt',%s,%s,%s,id,'failed','cadence_failed',%s FROM control.warehouse WHERE is_production "
        "ON CONFLICT(work_key) DO UPDATE SET error_message=EXCLUDED.error_message,updated_at=now() "
        "RETURNING id", ("cadence-failure:" + str(cycle_id), cycle_id, cycle["scope"], f"{model}: {error}"),
    ).fetchone()
    event(conn, record["id"], "cadence_failed", f"{model}: {error}",
          {"cycle_id": str(cycle_id), "model": model, "error": error}, "error")
    opened = conn.execute(
        "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug) "
        "VALUES ('cadence_failed','critical','cycle',%s,%s,'cadence-failed') RETURNING id",
        (str(cycle_id), record["id"]),
    ).fetchone()
    return str(opened["id"])


def fail_runner(conn, run_id, job_id, model, error):
    binding = conn.execute("SELECT cycle_id FROM control.cycle_attempt WHERE dbt_run_id=%s", (run_id,)).fetchone()
    if binding:
        return fail_cycle(conn, binding["cycle_id"], model, error)
    # Errors before bind still have a durable run and alert, keyed by the runner invocation.
    subject = job_id or run_id
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("runner-failure:" + run_id,))
    found = conn.execute("SELECT id FROM control.run WHERE work_key=%s", ("runner-failure:" + run_id,)).fetchone()
    if found:
        return str(conn.execute("SELECT id FROM control.alert WHERE run_id=%s", (found["id"],)).fetchone()["id"])
    record = conn.execute(
        "INSERT INTO control.run(kind,work_key,scope,warehouse_id,status,error_class,error_message) "
        "SELECT 'dbt',%s,coalesce((SELECT scope FROM control.dbt_job WHERE job_id=%s),'global'),id,"
        "'failed','cadence_failed',%s FROM control.warehouse WHERE is_production RETURNING id",
        ("runner-failure:" + run_id, job_id, f"{model}: {error}"),
    ).fetchone()
    event(conn, record["id"], "cadence_failed", f"{model}: {error}", {"dbt_run_id": run_id}, "error")
    return str(conn.execute(
        "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug) "
        "VALUES ('cadence_failed','critical','dbt_job',%s,%s,'cadence-failed') RETURNING id", (subject, record["id"]),
    ).fetchone()["id"])


def sweep(conn):
    overdue = conn.execute(
        f"SELECT id FROM control.cycle WHERE status='open' AND {scheduled_cycle_sql()} AND opened_at < now() - "
        + cadence_overdue_sql("cadence")
    ).fetchall()
    for cycle in overdue:
        fail_cycle(conn, cycle["id"])


def report(run_id, directory, exit_code):
    model, error = "runner", f"exit {exit_code}"
    path = Path(directory) / "run_results.json"
    if path.exists():
        try:
            for result in json.loads(path.read_text()).get("results", []):
                if result.get("status") in {"error", "fail"}:
                    model = result.get("unique_id", model)
                    error = str(result.get("message") or error)[:2000]
                    break
        except (ValueError, OSError):
            pass
    response = httpx.post(os.environ["MDP_SERVICE_URL"].rstrip("/") + "/v1/alerts/runner_failed",
                          headers={"Authorization": "Bearer " + os.environ["MDP_SERVICE_TOKEN"]},
                          json={"dbt_run_id": run_id, "job_id": os.environ.get("DBT_CLOUD_JOB_ID"), "model": model, "error": error}, timeout=30)
    response.raise_for_status()
    print("ALERT cadence_failed recorded")


def report_success(run_id):
    response = httpx.post(
        os.environ["MDP_SERVICE_URL"].rstrip("/") + "/v1/dbt/webhook",
        headers={"Authorization": "Bearer " + os.environ["MDP_SERVICE_TOKEN"]},
        json={"event_id": "core-success:" + run_id, "run_id": run_id,
              "job_id": os.environ["DBT_CLOUD_JOB_ID"], "status": "succeeded"}, timeout=30,
    )
    response.raise_for_status()
    print("BUILD success recorded. Check /ops.")


if __name__ == "__main__":
    if sys.argv[1] == "--succeeded":
        report_success(sys.argv[2])
    else:
        report(*sys.argv[1:])
