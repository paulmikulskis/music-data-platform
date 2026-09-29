"""Tests for function time budget."""


from uuid import uuid4

import psycopg
import pytest
from cycle_mirror_fixture import control_rt
from mdp_functions.errors import ServiceError
from mdp_functions.layers import gold
from mdp_functions.registry import REGISTRY, sync


def test_a_time_budget_needs_a_declared_allow_partial(rt, databases):
    from mdp_functions.streamline_defaults import seed_streamline_defaults

    name = "budget_" + uuid4().hex[:8]
    with pytest.raises(ServiceError) as caught:

        @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=["raw." + name], external=True, time_budget_s=5)
        async def undeclared(ctx, rows):
            yield {"value": 1}

    assert caught.value.error_class == "invalid_time_budget" and name not in REGISTRY

    # A budget-ended run is partial, so the streamline starts with allow_partial; the UDF then accepts it.
    @gold(
        source_key=name, reads=["marts.mart_enrichment_fixture"], writes=["raw." + name], external=True,
        time_budget_s=5, knobs={"allow_partial": True},
    )
    async def declared(ctx, rows):
        yield {"value": 1}

    try:
        sync(rt.db)
        with psycopg.connect(databases["control_url"]) as functions, psycopg.connect(control_rt(databases)) as control:
            assert seed_streamline_defaults(functions, control, {name: REGISTRY[name]}) == [name]
        assert rt.db.one("SELECT allow_partial FROM control.streamline WHERE source_key=%s", (name,)) == {
            "allow_partial": True
        }
    finally:
        REGISTRY.pop(name)
