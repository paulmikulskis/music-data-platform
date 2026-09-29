"""The Shazam chart seed, shared by local initialization and Fly bootstrap.

The seed is the one writer of the global `chart` set. It holds at most the `sz_chart` row of
`dbt/seeds/chart_caps.csv`, seed members counted, and grows only when that row is raised.
"""

import csv
from pathlib import Path

from psycopg.types.json import Jsonb

from mdp_functions.settings import REPO

BASE = "https://www.shazam.com"
CAPS = REPO / "dbt/seeds/chart_caps.csv"
CHART_TYPES = ("top-200", "top-50", "discovery")


def chart_cap(source_key: str = "sz_chart", path: Path = CAPS) -> int:
    with path.open() as stream:
        caps = {row["source_key"]: int(row["cap"]) for row in csv.DictReader(stream)}
    return caps[source_key]


def chart_spec(row: dict) -> tuple[str, str, dict]:
    """(chart id, canonical key, frozen params) for one seed row. The chart page and its CSV share the
    path; a city chart's CSV has no trailing slash, a country chart's has one (recon 2026-09-24)."""
    chart_type, country, city = row["chart_type"], row["country"], row.get("city") or ""
    if chart_type not in CHART_TYPES or not country or (chart_type == "top-50") != bool(city):
        raise ValueError(f"unsupported chart seed row {chart_type}/{country}/{city}")
    path = "/".join(p for p in (chart_type, country, city) if p)
    chart_id = path.replace("/", ":")
    params = {
        "chart_type": chart_type,
        "country": country,
        **({"city": city} if city else {}),
        "priority_market": row.get("priority_market") or None,
        "cadence": "daily",
        "page_url": f"{BASE}/charts/{path}",
        "csv_url": f"{BASE}/services/charts/csv/{path}" + ("" if city else "/"),
    }
    return chart_id, f"sz:chart:{chart_id}", params


def seed_chart_targets(conn, path: Path, cap: int | None = None, *, activate: bool = True) -> int:
    """The global chart set exists even when empty: an invoke with no frozen export for its kind is
    refused. A seed longer than its cap is refused before any write."""
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    cap = chart_cap() if cap is None else cap
    if len(rows) > cap:
        raise ValueError(f"chart seed holds {len(rows)} members; seeds/chart_caps.csv caps sz_chart at {cap}")
    set_id = conn.execute(
        "INSERT INTO control.target_set(kind,name) VALUES ('chart','Public chart baseline') "
        "ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    ).fetchone()[0]
    for row in rows:
        chart_id, canonical, params = chart_spec(row)
        target_id, inserted = conn.execute(
            "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) "
            "VALUES (%s,'shazam',%s,%s,'resolved',CASE WHEN %s THEN now() END) "
            "ON CONFLICT(target_set_id,platform,platform_account_id) WHERE resolution_status='resolved' "
            "DO UPDATE SET platform_account_id=EXCLUDED.platform_account_id RETURNING id,(xmax=0)",
            (set_id, chart_id, chart_id.replace(":", "/"), activate),
        ).fetchone()
        # Existing keys win, as the playlist seed's do.
        conn.execute(
            "INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json,promotion_reason) "
            "VALUES (%s,'chart',%s,%s,%s) ON CONFLICT(target_id) DO UPDATE "
            "SET params_json=EXCLUDED.params_json || control.target_spec.params_json "
            "WHERE NOT EXCLUDED.params_json <@ control.target_spec.params_json",
            (target_id, canonical, Jsonb(params), "seed" if inserted else None),
        )
    return len(rows)
