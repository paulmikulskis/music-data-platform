"""Reconcile authoritative proxy spend without counting estimates twice."""

from decimal import ROUND_CEILING, Decimal

import httpx
from psycopg import sql

from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient


async def reconcile(rt):
    if not rt.settings.litellm_base_url or not rt.settings.litellm_admin_key:
        raise ServiceError(
            "litellm_unavailable", "the model proxy admin inputs are missing", 503
        )
    try:
        async with RefusingClient(timeout=30, layer="gold") as client:
            response = await client.get(
                rt.settings.litellm_base_url.removesuffix("/v1").rstrip("/")
                + "/spend/logs",
                headers={"Authorization": "Bearer " + rt.settings.litellm_admin_key},
            )
            response.raise_for_status()
            logs = response.json()
    except httpx.HTTPError as exc:
        raise ServiceError(
            "litellm_unavailable", "Proxy spend endpoint unavailable", 503
        ) from exc
    reconciled = 0
    for row in logs:
        request_id = row["request_id"]
        cost = int(
            (Decimal(str(row["spend"])) * 100).to_integral_value(rounding=ROUND_CEILING)
        )
        with rt.db.transaction() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("costsync:" + request_id,),
            )
            estimate = conn.execute(
                "SELECT * FROM control.cost_ledger WHERE vendor='litellm' AND provider_request_id=%s AND origin='estimate' FOR UPDATE",
                (request_id,),
            ).fetchone()
            if not estimate:
                continue  # This endpoint includes other applications; only reconcile owned request IDs.
            old = conn.execute(
                "SELECT cost_cents FROM control.cost_ledger WHERE vendor='litellm' AND provider_request_id=%s AND is_current",
                (request_id,),
            ).fetchone()
            conn.execute(
                "UPDATE control.cost_ledger SET is_current=false WHERE vendor='litellm' AND provider_request_id=%s AND origin='estimate'",
                (request_id,),
            )
            conn.execute(
                """INSERT INTO control.cost_ledger(run_id,streamline_id,tenant_id,llm_step_id,vendor,provider_request_id,unit,quantity,cost_cents,origin,is_current,occurred_at)
                VALUES (%s,%s,%s,%s,'litellm',%s,'token',%s,%s,'litellm',true,%s)
                ON CONFLICT(vendor,provider_request_id,origin) DO UPDATE SET quantity=EXCLUDED.quantity,cost_cents=EXCLUDED.cost_cents,is_current=true,occurred_at=EXCLUDED.occurred_at""",
                (
                    estimate["run_id"],
                    estimate["streamline_id"],
                    estimate["tenant_id"],
                    estimate["llm_step_id"],
                    request_id,
                    row["total_tokens"],
                    cost,
                    estimate["occurred_at"],
                ),
            )
            difference = cost - old["cost_cents"]
            conn.execute(
                "UPDATE control.run SET cost_cents=cost_cents+%s WHERE id=%s",
                (difference, estimate["run_id"]),
            )
            conn.execute(
                "UPDATE control.budget_reservation SET consumed_cents=consumed_cents+%s,reserved_cents=CASE WHEN settled_at IS NOT NULL THEN consumed_cents+%s ELSE reserved_cents END WHERE run_id=%s",
                (difference, difference, estimate["run_id"]),
            )
            reconciled += 1
    mirror_costs(rt)
    return {"reconciled": reconciled}


# The warehouse mirror of the LiteLLM cost ledger. Code-owned DDL: costsync creates it
# before mirroring, and the deployed bootstrap creates it (ensure_raw) so marts can read
# it before the first sync.
COST_LEDGER_COLUMNS = {
    "id": "uuid",
    "run_id": "uuid",
    "llm_step_id": "uuid",
    "provider_request_id": "text",
    "cost_cents": "bigint",
    "origin": "text",
    "is_current": "boolean",
}
COST_LEDGER_DDL = "CREATE TABLE IF NOT EXISTS raw.cost_ledger(" + ",".join(
    f"{name} {typ}" + (" primary key" if name == "id" else "")
    for name, typ in COST_LEDGER_COLUMNS.items()
) + ")"


def mirror_costs(rt):
    # Raw is loader-owned; SQL marts consume a read-only copy of this ledger.
    rows = rt.db.all(
        "SELECT id,run_id,llm_step_id,provider_request_id,cost_cents,origin,is_current FROM control.cost_ledger WHERE vendor IN ('litellm','typesafe')"
    )
    import psycopg

    with psycopg.connect(rt.settings.warehouse_url) as conn:
        conn.execute(COST_LEDGER_DDL)
        for row in rows:
            conn.execute(
                sql.SQL(
                    "INSERT INTO raw.cost_ledger VALUES ({}) ON CONFLICT(id) DO UPDATE SET cost_cents=EXCLUDED.cost_cents,is_current=EXCLUDED.is_current"
                ).format(sql.SQL(",").join(sql.Placeholder() for _ in row)),
                tuple(row.values()),
            )
