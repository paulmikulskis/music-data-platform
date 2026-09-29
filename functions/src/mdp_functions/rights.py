"""The rights registry's control mirror: `rights_sync` upserts every `dbt/seeds/rights_registry.csv` row
into `control.rights_source`, so a gold row's learning flag (the AND over its sources' rows, derived.py)
agrees with the registry the served marts annotate with. The deployed bootstrap runs it before the new
functions image starts. Rows are never deleted (rights_sync may not): a key dropped from the registry keeps
its last row."""

import csv
from pathlib import Path
from typing import Any

UPSERT = (
    "INSERT INTO control.rights_source(source_key,provider,category,license_ref,learning_eligible,resale_permitted,"
    "review_ref,synced_at) VALUES (%(source_key)s,%(provider)s,%(category)s,%(license_ref)s,%(learning_eligible)s,"
    "%(resale_permitted)s,%(review_ref)s,now()) ON CONFLICT (source_key) DO UPDATE SET provider=EXCLUDED.provider,"
    "category=EXCLUDED.category,license_ref=EXCLUDED.license_ref,learning_eligible=EXCLUDED.learning_eligible,"
    "resale_permitted=EXCLUDED.resale_permitted,review_ref=EXCLUDED.review_ref,synced_at=now(),updated_at=now()"
)


def registry_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as stream:
        return [{
            "source_key": row["source_key"], "provider": row["provider"] or row["source_key"],
            "category": row["category"], "license_ref": row["license_ref"] or None,
            "learning_eligible": row["learning_eligible"].strip().lower() == "true",
            "resale_permitted": row["resale_permitted"].strip().lower() == "true",
            "review_ref": (row.get("review_ref") or "").strip() or None,
        } for row in csv.DictReader(stream)]


def sync_rights(conn: Any, path: Path) -> int:
    """Upsert the registry into control.rights_source on a rights_sync connection; the row count."""
    rows = registry_rows(path)
    for row in rows:
        conn.execute(UPSERT, row)
    return len(rows)
