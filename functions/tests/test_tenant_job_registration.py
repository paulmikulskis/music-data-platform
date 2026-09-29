"""Tests for tenant job registration."""

from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@pytest.mark.docker
async def test_the_export_copies_the_target_role_into_a_spec_without_one(rt, databases):
    """the frozen params_json carries control.target.role even when the spec never held a role; a
    target with no role keeps its spec as written."""
    from mdp_functions.targets import export_targets

    tenant_id = uuid4()
    scope = f"tenant:{tenant_id}"
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.tenant(id,slug,name) VALUES (%s,%s,'Role copy')",
            (tenant_id, f"copy-{tenant_id.hex[:8]}"),
        )
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('local:daily-tenant','core','daily',%s)",
            (scope,),
        )
        set_id = conn.execute(
            "INSERT INTO control.target_set(kind,name,tenant_id) VALUES ('account','Scoped accounts',%s) RETURNING id",
            (tenant_id,),
        ).fetchone()[0]
        for account, role in (("account_a", "fixture"), ("account_b", None)):
            target = conn.execute(
                "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,role,resolution_status,activated_at)"
                " VALUES (%s,'fixture',%s,'h',%s,'resolved',now()) RETURNING id",
                (set_id, account, role),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES (%s,'account',%s,%s)",
                (target, f"fixture:account:{account}", Jsonb({"fixture_key": "fixture_a"})),
            )
    binding = await rt.cycles.bind_cycle(
        "daily", scope, "local:" + uuid4().hex, "scheduled", "local:daily-tenant", runner="core"
    )
    revision = export_targets(rt.db, rt.warehouse, binding["cycle_id"], "account", tenant_id)
    with psycopg.connect(databases["admin_warehouse"], row_factory=dict_row) as conn:
        frozen = conn.execute(
            "SELECT platform_account_id,params_json::jsonb AS params FROM raw.targets WHERE _revision_id=%s ORDER BY 1",
            (revision["id"],),
        ).fetchall()
    assert frozen == [
        {"platform_account_id": "account_a", "params": {"fixture_key": "fixture_a", "role": "fixture"}},
        {"platform_account_id": "account_b", "params": {"fixture_key": "fixture_a"}},
    ]
