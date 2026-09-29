"""Tests for declared inputs."""


import pytest
from mdp_functions.settings import Settings


def test_compiled_input_relation_matches_declaration():
    from mdp_functions.derived import declared_input
    from mdp_functions.errors import ServiceError
    from mdp_functions.registry import Manifest

    manifest = Manifest(
        source_key="fixture",
        layer="gold",
        writes=[],
        reads=["marts.mart_enrichment_fixture"],
    )
    settings = Settings(service_read_url="postgresql:///warehouse")
    assert (
        declared_input(settings, manifest, '"warehouse"."marts"."mart_enrichment_fixture"')
        == manifest.reads[0]
    )
    for value in [
        '"other"."marts"."mart_enrichment_fixture"',
        "staging.mart_enrichment_fixture",
        "marts.wrong_alias",
    ]:
        with pytest.raises(ServiceError, match="Input database|must be declared"):
            declared_input(settings, manifest, value)
