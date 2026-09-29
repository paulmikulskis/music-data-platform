"""A bronze lookup that fills optional values lets its cycle close past its own miss."""

import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.registry import Manifest, discover, register


def test_only_optional_lookups_let_the_cycle_close() -> None:
    catalog = discover()
    assert {key for key, manifest in catalog.items() if not manifest.blocks_cycle} == {
        "apple_song_duration"
    }
    assert catalog["apple_song_duration"].public()["blocks_cycle"] is False
    assert catalog["billboard_hot100"].public()["blocks_cycle"] is True


@pytest.mark.parametrize(
    ("layer", "value"), [("gold", False), ("universal", False), ("bronze", "no")]
)
def test_blocks_cycle_is_a_bronze_boolean(layer: str, value: object) -> None:
    manifest = Manifest(
        source_key="optional_lookup",
        layer=layer,
        writes=["raw.optional_lookup"],
        external=layer != "silver",
        blocks_cycle=value,
    )  # type: ignore[arg-type]
    with pytest.raises(ServiceError) as refused:
        register(manifest)
    assert refused.value.error_class == "invalid_blocks_cycle"
