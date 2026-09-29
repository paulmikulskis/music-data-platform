"""Tests for tenant relation reads."""


from types import SimpleNamespace

import pytest
from mdp_functions.derived import declared_input, tenant_read
from mdp_functions.errors import ServiceError
from mdp_functions.registry import Manifest, discover


def test_tenant_reads_resolve_the_compiled_tenant_schema() -> None:
    discover()
    manifest = Manifest("scoped_test", "universal", [], reads=["tenant_marts.mart_scoped_test"], tenant_bound=True)
    settings = SimpleNamespace(service_read_url="postgresql://x@h/warehouse")
    assert tenant_read(manifest, "tenant_tenant_a_marts.mart_scoped_test") == (
        "tenant_marts.mart_scoped_test",
        "tenant_a",
    )
    assert declared_input(
        settings, manifest, '"tenant_tenant_a_marts"."mart_scoped_test"'
    ) == ("tenant_tenant_a_marts.mart_scoped_test")
    with pytest.raises(ServiceError):
        declared_input(settings, manifest, "marts.mart_scoped_test")
