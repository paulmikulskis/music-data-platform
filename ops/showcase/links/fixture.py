"""Write synthetic preview facts for local UI tests. Run this file to refresh the fixture."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "links", Path(__file__).with_name("collect.py")
)
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


def fixture():
    manifest = links.definitions(ROOT)
    entries = []
    for link in manifest.links:
        for variant, destination in links.variants(link, ROOT):
            facts = {
                "exempted_name_matches": 0,
                "object_type": None,
                "highlights": [],
                "listing_count": None,
                "line_count": None,
                "headings": [],
                "line": None,
            }
            if isinstance(destination, links.Code):
                facts.update(
                    object_type="tree" if link.thumbnail == "folder" else "blob",
                    highlights=link.highlights,
                    listing_count=123 if link.thumbnail == "folder" else None,
                    line_count=None if link.thumbnail == "folder" else 1234,
                    headings=["Guide"] if link.thumbnail == "doc" else [],
                    line=42 if link.line_marker else None,
                )
            body, proper = links.body_for(link, destination, facts)
            entries.append(
                links.Fact(
                    id=link.id,
                    variant=variant,
                    destination=destination,
                    **facts,
                    body=body,
                    proper_names=proper,
                    probe=links.Probe(
                        state="no_answer",
                        checked_at="2026-09-27T12:00:00Z",
                        note="No answer at deploy",
                    ),
                    names="names_not_checked",
                ).model_dump()
            )
    complete = links.Generated(
        schema_version=1,
        revision=None,
        collected_at="2026-09-27T12:00:00Z",
        input_hashes=links.inputs(ROOT),
        names="names_not_checked",
        tenants="not_checked",
        entries=[links.Fact.model_validate(e) for e in entries],
    ).model_dump()
    # Scenarios share this production-sized fixture: remove one entry or its revision in tests.
    complete["entries"] = [
        entry for entry in complete["entries"] if entry["id"] != "team-4-guide"
    ]
    return complete


if __name__ == "__main__":
    output = ROOT / "control/apps/showcase/test/fixtures/links.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture(), indent=2, sort_keys=True) + "\n")
