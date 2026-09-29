"""Idempotent playlist seed shared by local initialization and Fly bootstrap."""

import csv
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from mdp_functions.playlist import weekday_bucket

DAILY_OWNERS = {"editorial", "dsp_algorithmic", "chart"}


def spec_hash(spec: dict) -> str:
    """Stable identity of a Bandcamp discover spec; its playlist id is `discover:<hash>`."""
    return hashlib.sha1(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def target_spec(row: dict) -> tuple[str, dict]:
    """Canonical key and frozen params for one seed row."""
    platform, account = row["platform"], row["platform_account_id"]
    params = json.loads(row.get("params") or "{}")
    owner_class = row.get("owner_class") or params.get("owner_class")
    if owner_class:
        params["owner_class"] = owner_class
    # Editorial, chart, and hub playlists are daily; curator and user playlists weekly.
    params["cadence"] = row.get("cadence") or (
        "daily" if owner_class in DAILY_OWNERS or not owner_class else "weekly"
    )
    if platform in ("apple_music", "spotify"):
        apple = platform == "apple_music"
        identity = account.split(":", 1)
        variant, playlist_id = (
            identity if len(identity) == 2 else ("us" if apple else "US", identity[0])
        )
        params["storefront" if apple else "market"] = variant
        # Renders keep their own frozen cadence, weekly unless a spec says otherwise.
        params.setdefault("render_cadence", "weekly")
        return f"{'am' if apple else 'sp'}:playlist:{variant}:{playlist_id}", params
    kind = row.get("kind") or "playlist"
    if kind == "curator" and platform == "soundcloud":
        return f"sc:user:{account}", params
    if kind == "artist_page" and platform == "bandcamp":
        return f"bc:band:{account}", params
    if platform == "soundcloud":
        kind = "system" if account.startswith("soundcloud:") else "playlist"
        return f"sc:{kind}:{account}", params
    if platform == "bandcamp":
        return f"bc:list:{account}", params
    raise ValueError(f"unsupported playlist platform {platform}")


SETS = {
    "playlist": "Public playlist baseline",
    "curator": "Public curator baseline",
    "artist_page": "Public artist pages",
    "track": "Public tracks",
}


def seed_playlist_targets(conn, path: Path, *, activate: bool = True) -> int:
    """Every kind a collector declares gets its global target set, even when empty:
    an invoke with no frozen export for its kind is refused."""
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    set_ids = {
        kind: conn.execute(
            "INSERT INTO control.target_set(kind,name) VALUES (%s,%s) "
            "ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id,(xmax=0)",
            (kind, name),
        ).fetchone()[0]
        for kind, name in SETS.items()
    }
    for row in rows:
        kind = row.get("kind") or "playlist"
        target_id, inserted = conn.execute(
            "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) "
            "VALUES (%s,%s,%s,%s,'resolved',CASE WHEN %s THEN now() END) "
            "ON CONFLICT(target_set_id,platform,platform_account_id) WHERE resolution_status='resolved' DO UPDATE SET platform_account_id=EXCLUDED.platform_account_id RETURNING id,(xmax=0)",
            (set_ids[kind], row["platform"], row["platform_account_id"], row["handle"], activate),
        ).fetchone()
        canonical, params = target_spec(row)
        if params["cadence"] == "weekly":
            params["weekday_bucket"] = weekday_bucket({"id": target_id})
        # Existing keys win (a promoter's render flag, an earlier frozen cadence); the
        # seed only fills in what a spec frozen before per-target cadence lacks.
        conn.execute(
            "INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json,promotion_reason) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(target_id) DO UPDATE "
            "SET params_json=EXCLUDED.params_json || control.target_spec.params_json "
            "WHERE NOT EXCLUDED.params_json <@ control.target_spec.params_json",
            (target_id, kind, canonical, Jsonb(params), "seed" if inserted else None),
        )
    return len(rows)
