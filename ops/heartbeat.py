"""Send a bounded external heartbeat only for a closed scheduled cycle."""

import os
import signal
import sys

import httpx
import psycopg
from mdp_functions.health_policy import scheduled_cycle_sql
from psycopg.types.json import Jsonb

HEARTBEAT_TIMEOUT_S = 10


def bounded_get(url):
    # httpx timeouts restart on every byte. The alarm bounds the whole request, so a trickling
    # reply cannot hold the cadence lock. Signals need the main thread, which a script run has.
    def expired(signum, frame):
        raise TimeoutError("Heartbeat deadline reached")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, HEARTBEAT_TIMEOUT_S)
    try:
        return httpx.get(url, timeout=HEARTBEAT_TIMEOUT_S, follow_redirects=False, trust_env=False)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def ping(run_id, environ=os.environ):
    url = environ.get("MDP_HEARTBEAT_URL", "").strip()
    try:
        with psycopg.connect(environ["MDP_CONTROL_RT_URL"], connect_timeout=5,
                             options="-c statement_timeout=5000") as conn:
            closed = conn.execute(
                f"""SELECT c.cadence,c.scope FROM control.cycle_attempt a JOIN control.cycle c ON c.id=a.cycle_id
                JOIN control.scope_close sc ON sc.scope=c.scope
                WHERE a.dbt_run_id=%s AND a.reason_category='scheduled'
                  AND c.status='closed' AND sc.mirrored_close_no>=c.close_no
                  AND {scheduled_cycle_sql('c.opened_by_dbt_run_id')}""", (run_id,),
            ).fetchone()
            if not closed:
                print("HEARTBEAT skipped: no closed scheduled cycle. Check /ops.")
                return
            # One durable result per build, including a repeated invocation of this script.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("heartbeat:" + run_id,))
            if conn.execute(
                "SELECT 1 FROM control.audit_log WHERE subject=%s AND action IN "
                "('heartbeat.sent','heartbeat.skipped','heartbeat.failed')", (run_id,),
            ).fetchone():
                print("HEARTBEAT already recorded. Check /ops.")
                return
            action = "heartbeat.skipped"
            if url:
                try:
                    # No redirects, retries or secret URL in logs or audit rows.
                    bounded_get(url).raise_for_status()
                    action = "heartbeat.sent"
                except (httpx.HTTPError, httpx.InvalidURL, ValueError, TimeoutError):
                    action = "heartbeat.failed"
            conn.execute(
                'INSERT INTO control.audit_log(actor,action,subject,"after") VALUES (%s,%s,%s,%s)',
                ("system:runner", action, run_id,
                 Jsonb({"cadence": closed[0], "scope": closed[1], "configured": bool(url)})),
            )
    except (psycopg.Error, KeyError, ValueError):
        print("HEARTBEAT result unavailable. Check MDP_CONTROL_RT_URL and /ops.")
        return
    state = action.removeprefix("heartbeat.")
    detail = " MDP_HEARTBEAT_URL is unset." if not url else ""
    print(f"HEARTBEAT {state}.{detail} See ops/runbooks/service_unreachable.md#external-heartbeat.")


if __name__ == "__main__":
    ping(sys.argv[1])
