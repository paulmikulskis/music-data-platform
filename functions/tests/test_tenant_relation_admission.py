"""Tests for tenant relation admission."""


from uuid import uuid4

import psycopg
import pytest
from mdp_functions import derived
from mdp_functions.errors import ServiceError
from mdp_functions.registry import Manifest
from mdp_functions.settings import Settings


def test_tenant_relations_map_to_their_declared_reads():
    manifest = Manifest("scoped_test", "universal", [], reads=["tenant_intermediate.int_scoped_inputs"], tenant_bound=True)
    assert derived.tenant_read(manifest, "tenant_tenant_a_intermediate.int_scoped_inputs") == (
        "tenant_intermediate.int_scoped_inputs", "tenant_a")
    assert derived.tenant_read(manifest, "staging.stg_x") == ("staging.stg_x", None)
    settings = Settings(service_read_url="postgresql://service_read@localhost/warehouse")
    assert derived.declared_input(
        settings, manifest, '"warehouse"."tenant_tenant_a_intermediate"."int_scoped_inputs"'
    ) == "tenant_tenant_a_intermediate.int_scoped_inputs"
    with pytest.raises(ServiceError, match="declared"):
        derived.declared_input(settings, manifest, "tenant_tenant_a_intermediate.int_other")


async def test_a_tenant_relation_is_admitted_only_in_its_own_tenant_scope(rt, databases, monkeypatch):
    from mdp_functions.registry import REGISTRY
    monkeypatch.setitem(REGISTRY, "scoped_test", Manifest("scoped_test", "universal", [], reads=["tenant_intermediate.int_scoped_inputs"], tenant_bound=True, cadence="daily"))
    from mdp_functions.registry import sync
    sync(rt.db)
    tenant = uuid4()
    scope = "tenant:" + str(tenant)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.tenant(id,name,slug) VALUES (%s,'sound fixture','sound_fixture')", (tenant,))
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,global_inputs) VALUES ('tenant-sounds','core','daily',%s,%s)",
            (scope, ["raw.account_snapshots"]),
        )
    dbt_id = "tenant:" + uuid4().hex
    await rt.cycles.bind_cycle(
        "daily", scope, dbt_id, "scheduled", "tenant-sounds", runner="core", global_inputs=["raw.account_snapshots"]
    )
    with pytest.raises(ServiceError) as refused:
        rt.admit("scoped_test", dbt_run_id=dbt_id,
                 input_relation="tenant_other_intermediate.int_scoped_inputs")
    assert refused.value.error_class == "undeclared_read" and "tenant" in str(refused.value)
    admitted = rt.admit("scoped_test", dbt_run_id=dbt_id,
                        input_relation="tenant_sound_fixture_intermediate.int_scoped_inputs")
    assert admitted["input_relation"] == "tenant_sound_fixture_intermediate.int_scoped_inputs"
