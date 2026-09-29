"""Deploy baselines use checked-in fixture schemas and current declarations."""

import json

from mdp_functions.registry import discover
from mdp_functions.schemas import (
    GOLD_COLUMNS,
    SILVER_INPUT_COLUMNS,
    declared,
    schema_fingerprint,
)


def acknowledge(conn, schema_root):
    count = 0
    for source, manifest in discover().items():
        fingerprints = []
        for table in manifest.writes:
            declaration = manifest.schema.get(table, "infer") if isinstance(manifest.schema, dict) else manifest.schema
            fixtures = []
            for path in sorted((schema_root / source).glob("*.json")):
                fixture = json.loads(path.read_text())
                if table == fixture.get("table") or table in fixture.get("tables", []):
                    fixtures.append(fixture["columns"])
            # Typed output has exactly the declared columns, including new optional fields.
            # Inferred output uses only fixture-produced shapes, never a live warehouse schema.
            columns = [declared(declaration)] if isinstance(declaration, type) else fixtures
            for shape in columns:
                if manifest.layer == "gold" or manifest.per_input:
                    runtime = GOLD_COLUMNS if manifest.layer == "gold" else SILVER_INPUT_COLUMNS
                    # Fixture histories supply the types of copied input-version columns.
                    components = {name: fixture[name] for fixture in fixtures
                                  for name in manifest.input_version if name in fixture}
                    shape = {**shape, **runtime, **components}
                fingerprints.append(f"{source}:{table}:{schema_fingerprint(shape)}")
        if fingerprints:
            conn.execute(
                "UPDATE control.streamline SET acknowledged_fingerprints=ARRAY(SELECT DISTINCT unnest("
                "acknowledged_fingerprints || %s::text[])) WHERE source_key=%s",
                (fingerprints, source),
            )
            count += 1
    return count
