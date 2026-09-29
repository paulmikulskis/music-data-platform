"""The service reads Drizzle's schema and never creates control tables."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from mdp_functions.errors import LeaseLost
from mdp_functions.fetch.guard import transport_network

Row = dict[str, Any]
Connection = psycopg.Connection[Row]


class ControlDB:
    @transport_network()
    def __init__(self, url: str) -> None:
        self.pool = ConnectionPool(
            url,
            min_size=1,
            max_size=12,
            open=True,
            kwargs={"row_factory": dict_row, "connect_timeout": 5},
            timeout=10,
        )

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with transport_network(), self.pool.connection() as conn, conn.transaction():
            yield conn

    def all(self, query: str, params: Sequence[Any] = ()) -> list[Row]:
        with self.transaction() as conn:
            return conn.execute(query, params).fetchall()

    def one(self, query: str, params: Sequence[Any] = ()) -> Row | None:
        with self.transaction() as conn:
            return conn.execute(query, params).fetchone()

    def execute(self, query: str, params: Sequence[Any] = ()) -> None:
        with self.transaction() as conn:
            conn.execute(query, params)

    @transport_network()
    def close(self) -> None:
        self.pool.close()


def fence(conn: Connection, batch: Row) -> Row:
    owner = conn.execute(
        "SELECT run_id FROM control.batch WHERE id=%s", (batch["id"],)
    ).fetchone()
    if not owner:
        raise LeaseLost()
    conn.execute(
        "SELECT id FROM control.run WHERE id=%s FOR UPDATE", (owner["run_id"],)
    )
    current = conn.execute(
        "SELECT id,status,deadline_at>now() AS alive FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
        (owner["run_id"],),
    ).fetchone()
    if (
        not current
        or current["id"] != batch["attempt_id"]
        or current["status"] != "running"
        or not current["alive"]
    ):
        raise LeaseLost()
    row = conn.execute(
        "SELECT * FROM control.batch WHERE id=%s FOR UPDATE", (batch["id"],)
    ).fetchone()
    if (
        not row
        or row["lease_token"] != batch["lease_token"]
        or row["attempt_id"] != batch["attempt_id"]
        or row["status"] not in ("running", "draining")
    ):
        raise LeaseLost()
    return row


def event(
    conn: Connection,
    run_id: Any,
    kind: str,
    message: str,
    attrs: Row | None = None,
    level: str = "info",
) -> None:
    conn.execute(
        "INSERT INTO control.run_event(run_id,level,event_type,message,attrs) "
        "VALUES (%s,%s,%s,%s,%s)",
        (run_id, level, kind, message, Jsonb(attrs or {})),
    )


def alert(
    conn: Connection, run_id: Any, kind: str, subject: str, severity: str = "warning"
) -> None:
    """Open a class once per run attempt, even when its earlier alert is resolved."""
    # Zero identifies alerts before the first attempt. The unique index also arbitrates
    # concurrent inserts without upgrading the run's foreign-key KEY SHARE lock.
    conn.execute(
        "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug,attempt_no) "
        "SELECT %s,%s,'run',%s,%s,%s,coalesce(max(attempt_no),0) "
        "FROM control.run_attempt WHERE run_id=%s "
        "ON CONFLICT (run_id,attempt_no,class) WHERE subject_type='run' AND attempt_no IS NOT NULL "
        "DO NOTHING",
        (kind, severity, subject, run_id, kind.replace("_", "-"), run_id),
    )


def open_alerts(conn: Connection, run_id: Any, alerts: list[dict[str, Any]]) -> None:
    """The alerts a function body opened (Ctx.alert), each with its run event, on the target it names or
    on the run. Run alerts open once per attempt. A target's `once` alert opens only while
    that target holds no unresolved alert of its class."""
    for item in alerts:
        subject_type = "target" if item["target_id"] else "run"
        subject_id = str(item["target_id"] or run_id)
        event(conn, run_id, item["kind"], item["message"], {**item["attrs"], "target_id": item["target_id"]}, "warning")
        if subject_type == "run":
            alert(conn, run_id, item["kind"], subject_id)
            continue
        if item.get("once"):
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"alert:{item['kind']}:{subject_id}",))
            if conn.execute(
                "SELECT 1 FROM control.alert WHERE class=%s AND subject_type=%s AND subject_id=%s AND resolved_at IS NULL",
                (item["kind"], subject_type, subject_id),
            ).fetchone():
                continue
        conn.execute(
            "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug) "
            "VALUES (%s,'warning',%s,%s,%s,%s)",
            (item["kind"], subject_type, subject_id, run_id, item["kind"].replace("_", "-")),
        )


def envelope_miss(
    db: ControlDB,
    run_id: str,
    target_id: str | None,
    surface: str,
    path: str,
    decide: bool = True,
) -> None:
    """Durable distinct-target count across batches and attempts, serialized per run. With
    `decide` false (a gold input) only the event is recorded: the derived path decides drift."""
    with db.transaction() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", ("envelope:" + run_id,)
        )
        event(
            conn,
            run_id,
            "envelope_mismatch",
            "Required envelope absent",
            {"target_id": target_id, "surface": surface, "path": path},
        )
        if not decide:
            return
        alert(conn, run_id, "envelope_mismatch", target_id or run_id)
        misses = conn.execute(
            "SELECT count(DISTINCT attrs->>'target_id') AS n FROM control.run_event WHERE run_id=%s AND event_type='envelope_mismatch'",
            (run_id,),
        ).fetchone()["n"]
        if misses >= 3:
            alert(conn, run_id, "surface_drift", run_id, severity="critical")


def block_host(
    db: ControlDB, run_id: str, host: str, signature: str, seconds: float = 3600
) -> None:
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO control.host_health(host,blocked_until,last_signature)
            VALUES (%s,now()+(%s * interval '1 second'),%s)
            ON CONFLICT(host) DO UPDATE SET blocked_until=EXCLUDED.blocked_until,
            last_signature=EXCLUDED.last_signature,updated_at=now()""",
            (host, seconds, signature),
        )
        alert(conn, run_id, "scrape_blocked", host)
