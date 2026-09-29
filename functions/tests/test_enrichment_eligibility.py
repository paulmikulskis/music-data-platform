"""Tests for enrichment eligibility."""


from uuid import uuid4

import psycopg
import pytest
from conftest import bound
from mdp_functions.layers import gold
from mdp_functions.registry import REGISTRY, sync
from psycopg import sql
from test_enrichment_runtime import inputs


@pytest.mark.parametrize(
    "provider_eligible,source_identity,expected",
    [(False, "fixture_accounts", False), (True, None, False), (True, "fixture_accounts", True)],
)
async def test_external_gold_eligibility_fails_closed(
    rt, databases, provider_eligible, source_identity, expected
):
    inputs(databases, 1)
    name = "enrichment_external_" + uuid4().hex[:8]

    @gold(
        source_key=name,
        provider="fixture_external_provider",
        reads=["marts.mart_enrichment_fixture"],
        writes=["raw." + name],
        external=True,
        input_key=["platform_account_id"],
        input_version=["followers"],
    )
    async def external(ctx, rows):
        yield {"label": "fixture"}

    try:
        with psycopg.connect(databases["admin_control"]) as conn:
            # The function's own registry row counts beside its provider's.
            for source, eligible in [
                ("fixture_accounts", True),
                ("fixture_external_provider", provider_eligible),
                (name, True),
            ]:
                conn.execute(
                    "INSERT INTO control.rights_source(source_key,provider,category,learning_eligible) VALUES (%s,%s,'fixture',%s) ON CONFLICT(source_key) DO UPDATE SET learning_eligible=EXCLUDED.learning_eligible",
                    (source, source, eligible),
                )
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            conn.execute(
                "UPDATE marts.mart_enrichment_fixture SET source_key=%s", (source_identity,)
            )
        sync(rt.db)
        _, run = await bound(rt, name)
        await rt.execute(run["id"])
        saved = rt.db.one(
            "SELECT status,error_message FROM control.run WHERE id=%s", (run["id"],)
        )
        assert saved["status"] == "succeeded", saved
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            assert (
                conn.execute(
                    sql.SQL("SELECT learning_eligible FROM {}").format(
                        sql.Identifier("raw", name)
                    )
                ).fetchone()[0]
                is expected
            )
    finally:
        REGISTRY.pop(name)
