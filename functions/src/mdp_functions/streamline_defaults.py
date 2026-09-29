"""Initial streamline knobs, applied once, and the notes that explain the gated ones.

Knobs (`enabled`, `batch_size`, `max_concurrency`, `timeout_s`, `allow_partial`) are control-owned:
registry sync never writes them, and `functions_rt` cannot. A manifest declares the
knobs its streamline starts with (`knobs=`). The bootstrap seed, which runs before a
new functions image syncs, creates any missing streamline through the code-owned
columns and applies its declared knobs through `control_rt` while the row still holds
the table defaults. An audit row (`action='streamline_defaults'`) then marks it, so a
later seed never overrides an operator's choice. A host's starting request rate
(`HOST_RATES`, on `control.host_health`) follows the same once-only rule.

Collectors whose live surface refuses the honest user agent declare `enabled=False`;
their host rows record the refusal.
"""

from psycopg.types.json import Jsonb

from mdp_functions.registry import discover

TABLE_DEFAULTS = {
    "enabled": True,
    "batch_size": 1,
    "max_concurrency": 1,
    "timeout_s": 300,
    "allow_partial": False,
}
# The paired Spotify collector does two fetches per target: it starts from the
# retired embed collector's production knobs with at least double its timeout.
CARRY_OVER = {
    "sp_playlist": "sp_playlist_embed",
    "sp_playlist_weekly": "sp_playlist_embed",
}
# Source keys no function declares any more: each row stays, disabled once, so its runs
# and landed rows keep their streamline; the exporter drops their invoke stubs and tests.
RETIRED = (
    "sp_playlist_embed",
    "sp_playlist_page",
)
# Initial host failure hints. No `blocked_until`: a note pauses nothing.
SIGNATURES = {
    "api-v2.soundcloud.com": "auth_refused_honest_ua",
}
# The request rate each free-source host starts with; every other host keeps the table
# default of 1 per second. Applied once, and only over that default, so an operator's rate stands.
HOST_RATES = {
    "itunes.apple.com": 0.25,
    "www.shazam.com": 0.5,
    "wikimedia.org": 2,
    "www.wikidata.org": 2,
}


def marked(conn, action: str, subject: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM control.audit_log WHERE action=%s AND subject=%s",
            (action, subject),
        ).fetchone()
    )


def audit(conn, action: str, subject: str, before=None, after=None) -> None:
    conn.execute(
        'INSERT INTO control.audit_log(actor,action,subject,"before","after") VALUES (%s,%s,%s,%s,%s)',
        (
            "seed",
            action,
            subject,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
        ),
    )


def knobs_of(conn, source_key: str) -> dict | None:
    row = conn.execute(
        "SELECT enabled,batch_size,max_concurrency,timeout_s,allow_partial FROM control.streamline WHERE source_key=%s",
        (source_key,),
    ).fetchone()
    return dict(zip(TABLE_DEFAULTS, row, strict=True)) if row else None


def seed_streamline_defaults(functions_conn, control_conn, catalog=None) -> list[str]:
    """`functions_conn` is functions_rt (code-owned columns, host signatures);
    `control_conn` is control_rt (knobs and the audit log). Returns the streamlines
    whose declared knobs were applied."""
    catalog = discover() if catalog is None else catalog
    applied = []
    for source_key, manifest in sorted(catalog.items()):
        declared = dict(manifest.knobs)
        if not declared and source_key not in CARRY_OVER:
            continue
        unknown = set(declared) - set(TABLE_DEFAULTS)
        if unknown:
            raise ValueError(f"{source_key}: undeclarable knobs {sorted(unknown)}")
        if marked(control_conn, "streamline_defaults", source_key):
            continue
        functions_conn.execute(
            "INSERT INTO control.streamline(source_key,layer,tenant_bound,writes,reads,external,cadence_tag) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_key) DO NOTHING",
            (
                source_key,
                manifest.layer,
                manifest.tenant_bound,
                manifest.writes,
                manifest.reads,
                manifest.external,
                manifest.cadence,
            ),
        )
        functions_conn.commit()
        current = knobs_of(control_conn, source_key)
        previous = knobs_of(control_conn, CARRY_OVER.get(source_key, ""))
        if previous:
            declared.update(
                batch_size=previous["batch_size"],
                max_concurrency=previous["max_concurrency"],
                timeout_s=max(declared.get("timeout_s", 0), 2 * previous["timeout_s"]),
            )
        # Only a streamline still on the table defaults is new; anything else was set
        # by an operator and stays as it is.
        pristine = current == TABLE_DEFAULTS
        if pristine:
            control_conn.execute(
                "UPDATE control.streamline SET "
                + ",".join(f"{k}=%s" for k in declared)
                + " WHERE source_key=%s",
                (*declared.values(), source_key),
            )
            applied.append(source_key)
        audit(
            control_conn,
            "streamline_defaults",
            source_key,
            current,
            {**current, **declared} if pristine else current,
        )
    for source_key in RETIRED:
        if marked(control_conn, "streamline_retired", source_key):
            continue
        before = knobs_of(control_conn, source_key)
        if before is None:
            continue
        control_conn.execute(
            "UPDATE control.streamline SET enabled=false WHERE source_key=%s",
            (source_key,),
        )
        audit(
            control_conn,
            "streamline_retired",
            source_key,
            before,
            {**before, "enabled": False},
        )
    control_conn.commit()
    for host, signature in SIGNATURES.items():
        functions_conn.execute(
            "INSERT INTO control.host_health(host,last_signature) VALUES (%s,%s) "
            "ON CONFLICT(host) DO UPDATE SET last_signature=EXCLUDED.last_signature,updated_at=now() "
            "WHERE control.host_health.last_signature IS NULL",
            (host, signature),
        )
    functions_conn.commit()
    for host, rps in HOST_RATES.items():
        if marked(control_conn, "host_rate_default", host):
            continue
        before = control_conn.execute(
            "SELECT host_rps FROM control.host_health WHERE host=%s", (host,)
        ).fetchone()
        control_conn.execute(
            "INSERT INTO control.host_health(host,host_rps) VALUES (%s,%s) "
            "ON CONFLICT(host) DO UPDATE SET host_rps=EXCLUDED.host_rps,updated_at=now() "
            "WHERE control.host_health.host_rps=1",
            (host, rps),
        )
        audit(
            control_conn,
            "host_rate_default",
            host,
            {"host_rps": float(before[0])} if before else None,
            {"host_rps": rps if before is None or float(before[0]) == 1 else float(before[0])},
        )
    control_conn.commit()
    return applied
