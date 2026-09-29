"""Reservations serialize under a per-budget advisory lock; absent budgets are explicitly unlimited.
A vendor provider's request cap is the exception: a function naming a provider fails closed
without a budget row that caps its requests and pauses at the cap."""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from mdp_functions.control_db import Connection, ControlDB, event
from mdp_functions.errors import ServiceError

PERIODS = {"hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month"}


def vendor_scope_id(name: str) -> str:
    """A vendor provider budget's scope_id, apart from the proxy rows' `mdp:proxy:<name>`."""
    return str(uuid5(NAMESPACE_URL, "mdp:provider:" + name))


def provider_cap(db: ControlDB, run: dict[str, Any], provider: str) -> None:
    """Before every attempt of a function declaring `provider` (every tier). The provider fails closed: it
    needs budget rows, each with a `cap_requests` and `hard_action='pause'`, and this period's current
    request rows in cost_ledger under that vendor below every cap. Otherwise no request is sent: the
    attempt stops with cost_cap_hit and a `cost_cap_hit` alert on the provider (one while it stays
    unresolved). A row with no request cap, or one that only warns or degrades, counts as no row. At
    `soft_pct` of a cap one `cost_cap_soft` alert opens per period and provider."""
    budgets = db.all(
        "SELECT * FROM control.budget WHERE scope='provider' AND scope_id=%s ORDER BY id",
        (vendor_scope_id(provider),),
    )
    if not budgets:
        stop(db, run, provider, f"no budget row for provider {provider}")
    if any(b.get("cap_requests") is None or b["hard_action"] != "pause" for b in budgets):
        stop(
            db,
            run,
            provider,
            f"provider {provider} has a budget row without cap_requests or hard_action pause",
        )
    for budget in budgets:
        cap = budget["cap_requests"]
        period = PERIODS.get(budget["period"], "month")
        used = db.one(
            "SELECT count(*) AS n FROM control.cost_ledger WHERE vendor=%s AND unit='request' AND is_current "
            "AND occurred_at>=date_trunc(%s,now())",
            (provider, period),
        )["n"]
        if used >= cap:
            stop(db, run, provider, f"Provider {provider} reached its request cap ({cap}) for the {period}")
        if used >= cap * budget["soft_pct"] / 100:
            once(db, run, provider, "cost_cap_soft", "warning", period)


def once(db: ControlDB, run: dict[str, Any], provider: str, kind: str, severity: str, period: str | None) -> None:
    """One `kind` alert on the provider: per period when `period` is given, else while one stays unresolved.
    The advisory lock serializes concurrent attempts on the same check."""
    with db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (kind + ":" + provider,))
        since = "opened_at>=date_trunc(%s,now())" if period else "resolved_at IS NULL"
        if not conn.execute(
            f"SELECT 1 FROM control.alert WHERE class=%s AND subject_type='provider' AND subject_id=%s AND {since}",
            (kind, provider, *([period] if period else [])),
        ).fetchone():
            conn.execute(
                "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug) "
                "VALUES (%s,%s,'provider',%s,%s,%s)",
                (kind, severity, provider, run["id"], kind.replace("_", "-")),
            )


def stop(db: ControlDB, run: dict[str, Any], provider: str, message: str) -> None:
    once(db, run, provider, "cost_cap_hit", "critical", None)
    raise ServiceError("cost_cap_hit", message)


def reserve(conn: Connection, run: dict[str, Any], estimate: int = 0) -> None:
    budgets = conn.execute(
        """SELECT * FROM control.budget WHERE scope='global'
        OR (scope='streamline' AND scope_id=%s) OR (scope='tenant' AND scope_id=%s)
        OR (scope='llm_step' AND (scope_id IN (SELECT id FROM control.llm_step WHERE id=(SELECT (resolved_config->'llm_step'->>'id')::uuid FROM control.run WHERE id=%s)) OR id IN (SELECT budget_id FROM control.llm_step WHERE id=(SELECT (resolved_config->'llm_step'->>'id')::uuid FROM control.run WHERE id=%s))))
        ORDER BY id""",
        (run["streamline_id"], run["tenant_id"], run["id"], run["id"]),
    ).fetchall()
    if not budgets:
        event(
            conn,
            run["id"],
            "budget_unlimited",
            "No budget row; invocation reservation is unlimited",
        )
    for budget in budgets:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("budget:" + str(budget["id"]),),
        )
        budget = conn.execute(
            "SELECT * FROM control.budget WHERE id=%s", (budget["id"],)
        ).fetchone()
        period = {
            "hourly": "hour",
            "daily": "day",
            "weekly": "week",
            "monthly": "month",
        }.get(budget["period"], "month")
        used = conn.execute(
            """SELECT coalesce(sum(greatest(reserved_cents,consumed_cents)),0) AS n
            FROM control.budget_reservation WHERE budget_id=%s AND created_at>=date_trunc(%s,now())""",
            (budget["id"], period),
        ).fetchone()["n"]
        if used + estimate > budget["cap_cents"] and budget["hard_action"] != "warn":
            raise ServiceError(
                "cost_cap_hit", "Invocation would exceed the configured budget"
            )
        conn.execute(
            """INSERT INTO control.budget_reservation(budget_id,run_id,reserved_cents,policy_snapshot)
            VALUES (%s,%s,%s,%s)""",
            (
                budget["id"],
                run["id"],
                estimate,
                Jsonb(
                    {
                        "cap_cents": budget["cap_cents"],
                        "hard_action": budget["hard_action"],
                        "period": period,
                    }
                ),
            ),
        )


def draw(
    db: ControlDB, run: dict[str, Any], vendor: str, request_id: str, estimate: int = 0
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("call:" + vendor + ":" + request_id,),
        )
        if conn.execute(
            "SELECT id FROM control.cost_ledger WHERE vendor=%s AND provider_request_id=%s AND origin='estimate'",
            (vendor, request_id),
        ).fetchone():
            return
        reservations = conn.execute(
            "SELECT * FROM control.budget_reservation WHERE run_id=%s ORDER BY budget_id FOR UPDATE",
            (run["id"],),
        ).fetchall()
        for reservation in reservations:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("budget:" + str(reservation["budget_id"]),),
            )
            budget = conn.execute(
                "SELECT * FROM control.budget WHERE id=%s", (reservation["budget_id"],)
            ).fetchone()
            used = conn.execute(
                """SELECT coalesce(sum(greatest(reserved_cents,consumed_cents)),0) AS n
                FROM control.budget_reservation WHERE budget_id=%s AND created_at>=date_trunc(%s,now())""",
                (budget["id"], reservation["policy_snapshot"]["period"]),
            ).fetchone()["n"]
            unreserved = max(
                reservation["reserved_cents"], reservation["consumed_cents"] + estimate
            ) - max(reservation["reserved_cents"], reservation["consumed_cents"])
            if (
                used + unreserved > budget["cap_cents"]
                and budget["hard_action"] != "warn"
            ):
                raise ServiceError(
                    "cost_cap_hit", "Vendor call would exceed the configured budget"
                )
            if (
                used + unreserved > budget["cap_cents"]
                and budget["hard_action"] == "warn"
            ):
                event(
                    conn,
                    run["id"],
                    "llm_budget_warning",
                    "Budget exceeded under warn policy",
                    {"budget_id": str(budget["id"]), "hard_action": "warn"},
                )
            conn.execute(
                "UPDATE control.budget_reservation SET consumed_cents=consumed_cents+%s WHERE id=%s",
                (estimate, reservation["id"]),
            )
        conn.execute(
            "UPDATE control.run SET cost_cents=cost_cents+%s WHERE id=%s",
            (estimate, run["id"]),
        )
        conn.execute(
            """INSERT INTO control.cost_ledger
            (run_id,streamline_id,tenant_id,vendor,provider_request_id,unit,quantity,cost_cents,origin)
            VALUES (%s,%s,%s,%s,%s,'request',1,%s,'estimate') ON CONFLICT DO NOTHING""",
            (
                run["id"],
                run["streamline_id"],
                run["tenant_id"],
                vendor,
                request_id,
                estimate,
            ),
        )


def settle(db: ControlDB, run_id: Any | None = None) -> None:
    db.execute(
        """UPDATE control.budget_reservation b SET settled_at=now(),reserved_cents=consumed_cents
        FROM control.run r WHERE r.id=b.run_id AND r.status IN ('succeeded','partial','failed','superseded')
        AND b.settled_at IS NULL AND (%s::uuid IS NULL OR r.id=%s::uuid)""",
        (run_id, run_id),
    )
