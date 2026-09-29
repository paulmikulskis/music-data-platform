"""Shared fixtures for tenant schedule tests."""


from datetime import timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from mdp_functions.api import create_app
from mdp_functions.runs import Runtime

ROOT = Path(__file__).resolve().parents[2]


UTC = timezone.utc


def tenant(
    databases,
    slug="acme",
    tz="America/New_York",
    weekday=5,
    recipients=("lead@example.com",),
):
    with psycopg.connect(databases["admin_control"]) as conn:
        tenant_id = conn.execute(
            "INSERT INTO control.tenant(slug,name) VALUES (%s,%s) ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (slug, slug.title()),
        ).fetchone()[0]
        scope = f"tenant:{tenant_id}"
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,timezone) VALUES (%s,'core','daily',%s,%s) ON CONFLICT DO NOTHING",
            (f"core-daily-{scope}", scope, tz),
        )
    return str(tenant_id), scope


async def run_now(rt: Runtime, source_key: str, dbt_run_id: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            create_app(rt.settings, rt, recover=False), raise_app_exceptions=False
        ),
        base_url="http://test",
        headers={
            "Authorization": "Bearer " + rt.settings.service_token,
            "Idempotency-Key": "manual:" + uuid4().hex,
        },
    ) as client:
        return await client.post(
            f"/v1/functions/{source_key}/run", params={"dbt_run_id": dbt_run_id}
        )
