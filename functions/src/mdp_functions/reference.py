"""The reference probe, raw retention and the volume guard of the MusicBrainz spine.

It reads the mirror (the serving generation, the newest import attempt in `mdp_meta`, database bytes
against the volume) and the warehouse (the newest landed mb_spine generation and whether its
completion reconciles against the stamped dumps), writes `control.reference_source`, and opens or
resolves each class. Serving never waits on it: reference staging keeps the last complete generation
by its own manifest rule, and the probe only reports.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdp_functions.control_db import ControlDB
from mdp_functions.fetch.guard import transport_network

SOURCE = "musicbrainz"
DISK_HIGH = 0.8
# Full exports publish twice a week and the refresh runs monthly: past this age the dump is stale.
STALE_AFTER = timedelta(days=35)
CLASSES = ("reference_generation_incomplete", "reference_disk_high", "reference_dump_stale", "reference_import_failed")

LANDED = """
WITH completion AS (
  SELECT run_id, outputs, recorded, recorded->>'generation' AS generation, _ingested_at, _landed_seq,
         row_number() OVER (PARTITION BY run_id ORDER BY _landed_seq DESC, _dump_id DESC) AS rn
  FROM raw._run_completion c
  WHERE source_key = 'mb_spine' AND _dump_id IN (SELECT dump_id FROM raw.dump_stamps)
), newest AS (
  SELECT * FROM completion WHERE rn = 1 AND generation IS NOT NULL ORDER BY generation DESC, _landed_seq DESC LIMIT 1
), listed AS (
  SELECT t.key AS target_table, d.key::uuid AS dump_id, d.value::bigint AS row_count
  FROM newest n, jsonb_each(n.outputs) t, jsonb_each_text(t.value) d
)
SELECT n.generation, n._ingested_at AS landed_at, n.recorded->'mirror_counts' AS mirror_counts,
  (SELECT jsonb_object_agg(target_table, rows) FROM (
     SELECT target_table, sum(row_count) AS rows FROM listed WHERE target_table <> 'raw.mb_generation' GROUP BY 1) s) AS landed_counts,
  NOT EXISTS (SELECT 1 FROM listed l WHERE l.dump_id NOT IN (SELECT dump_id FROM raw.dump_stamps)) AS dumps_stamped
FROM newest n"""


def mirror_state(url: str) -> dict[str, Any]:
    """The serving generation, the newest import attempt and disk use, as mb_reader sees them."""
    state: dict[str, Any] = {}
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as conn:
        generation = conn.execute(
            "SELECT generation, export_date, replication_sequence, imported_at, validated_at, counts "
            "FROM mdp.generation ORDER BY generation DESC LIMIT 1").fetchone()
        if generation:
            state.update(imported_generation=generation["generation"], export_date=generation["export_date"],
                         replication_sequence=generation["replication_sequence"],
                         imported_at=generation["validated_at"] or generation["imported_at"])
        state["disk_used_bytes"] = conn.execute(
            "SELECT sum(pg_database_size(datname))::bigint AS n FROM pg_database WHERE NOT datistemplate").fetchone()["n"]
    options = conninfo_to_dict(url)
    options["dbname"] = os.environ.get("MDP_MB_META_DB", "mdp_meta")
    with psycopg.connect(make_conninfo(**options), row_factory=dict_row, connect_timeout=10) as conn:
        run = conn.execute(
            "SELECT generation, state, phase, started_at, finished_at, message FROM import_run ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
        if run:
            state.update(import_generation=run["generation"], import_state=run["state"], import_phase=run["phase"],
                         import_started_at=run["started_at"], import_finished_at=run["finished_at"], import_message=run["message"])
        volume = conn.execute("SELECT total_bytes FROM volume WHERE id = 1").fetchone()
        state["disk_total_bytes"] = volume["total_bytes"] if volume else None
    return state


def landed_state(service_read_url: str) -> dict[str, Any]:
    with psycopg.connect(service_read_url, row_factory=dict_row) as conn:
        if not conn.execute("SELECT to_regclass('raw._run_completion') AS r").fetchone()["r"]:
            return {}
        row = conn.execute(LANDED).fetchone()
    if not row:
        return {}
    mirror, landed = row["mirror_counts"] or {}, row["landed_counts"] or {}
    reconciled = bool(row["dumps_stamped"]) and all(int(landed.get(t, 0)) == int(n) for t, n in mirror.items()) \
        and set(landed) <= set(mirror)
    return {"landed_generation": row["generation"], "landed_at": row["landed_at"], "mirror_counts": mirror,
            "landed_counts": landed, "landed_reconciled": reconciled}


def closure_state(db: ControlDB) -> dict[str, Any]:
    """The closure trigger figures the newest mb_spine prepare step measured."""
    # Through mb_spine's own runs (weekly), so the read uses the run_event (run_id, at) index.
    row = db.one(
        "SELECT e.attrs, e.at FROM control.run r JOIN control.streamline s ON s.id = r.streamline_id "
        "JOIN control.run_event e ON e.run_id = r.id AND e.event_type = 'mb_spine_prepared' "
        "WHERE s.source_key = 'mb_spine' AND e.attrs ? 'projected_write_share' ORDER BY e.at DESC LIMIT 1")
    if not row:
        return {}
    return {"closure_write_share": row["attrs"].get("projected_write_share"),
            "closure_recordings": row["attrs"].get("tracked_recordings"), "closure_measured_at": row["at"]}


def verdicts(state: dict[str, Any], now: datetime) -> dict[str, bool]:
    """Which classes are open (True) or clear (False); a class without evidence either way is absent."""
    open_: dict[str, bool] = {}
    imported, landed = state.get("imported_generation"), state.get("landed_generation")
    current = bool(landed) and landed == imported and state.get("landed_reconciled")
    if landed is not None:
        open_["reference_generation_incomplete"] = not state.get("landed_reconciled")
    used, total = state.get("disk_used_bytes"), state.get("disk_total_bytes")
    if used is not None and total:
        open_["reference_disk_high"] = used >= DISK_HIGH * total
    if state.get("export_date") is not None:
        # Opens when the serving export is older than its cadence; clears once a current one lands.
        if now - state["export_date"] > STALE_AFTER:
            open_["reference_dump_stale"] = True
        elif current:
            open_["reference_dump_stale"] = False
    if state.get("import_state") == "failed":
        open_["reference_import_failed"] = True
    elif state.get("import_state") in ("validated", "promoted") and current:
        open_["reference_import_failed"] = False
    return open_


def apply(db: ControlDB, state: dict[str, Any], verdict: dict[str, bool]) -> None:
    columns = [c for c in state if c != "probe_error"] + ["probe_error"]
    values = [Jsonb(state[c]) if isinstance(state.get(c), dict) else state.get(c) for c in columns]
    with db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('mdp-reference-probe'))")
        conn.execute(
            f"INSERT INTO control.reference_source(source,{','.join(columns)},probed_at,updated_at) "
            f"VALUES (%s,{','.join(['%s'] * len(columns))},now(),now()) ON CONFLICT(source) DO UPDATE SET "
            + ",".join(f"{c}=EXCLUDED.{c}" for c in columns) + ",probed_at=now(),updated_at=now()",
            [SOURCE, *values],
        )
        for kind, is_open in verdict.items():
            existing = conn.execute(
                "SELECT id FROM control.alert WHERE class=%s AND subject_type='reference_source' AND subject_id=%s AND resolved_at IS NULL",
                (kind, SOURCE)).fetchone()
            if is_open and not existing:
                conn.execute(
                    "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) "
                    "VALUES (%s,%s,'reference_source',%s,%s)",
                    (kind, "critical" if kind in ("reference_disk_high", "reference_import_failed") else "warning",
                     SOURCE, kind.replace("_", "-")))
            elif not is_open and existing:
                # Resolution is control-owned; this definer function clears only reference classes.
                conn.execute("SELECT control.resolve_reference_alert(%s,%s)", (kind, SOURCE))


def probe(db: ControlDB, service_read_url: str, mirror_url: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """One probe pass; a mirror that cannot be read records its error and changes no alert it cannot judge."""
    mirror_url = mirror_url if mirror_url is not None else os.environ.get("MDP_MB_DB_URL", "")
    state: dict[str, Any] = {"probe_error": None}
    try:
        if not mirror_url:
            raise ConnectionError("MDP_MB_DB_URL is not configured")
        state.update(mirror_state(mirror_url))
    except (psycopg.Error, ConnectionError) as exc:
        state["probe_error"] = f"mirror: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    try:
        state.update(landed_state(service_read_url))
    except psycopg.Error as exc:
        state["probe_error"] = (state["probe_error"] or "") + f" warehouse: {type(exc).__name__}"
    state.update(closure_state(db))
    verdict = verdicts(state, now or datetime.now(UTC))
    apply(db, state, verdict)
    return {"state": state, "alerts": verdict}


# Retention and the volume guard run in mb_spine's prepare step, as loader_wh, the one writer of raw.mb_*.
KEEP_GENERATIONS = 2
MB_TABLES = (
    "mb_url_link", "mb_isrc", "mb_recording", "mb_track", "mb_medium", "mb_release", "mb_release_group",
    "mb_artist_credit", "mb_artist_credit_name", "mb_artist", "mb_l_artist_label", "mb_label", "mb_l_label_label",
    "mb_artist_ipi", "mb_artist_isni", "mb_redirect", "mb_generation",
)
# The closure trigger: once a generation's projected write passes this share of the pgdata
# volume, or the tracked recordings pass NARROW_RECORDINGS, the narrowed release step ships before the
# next import. prepare() measures both on every landing and the Reference page shows them.
NARROW_SHARE = 0.3
NARROW_RECORDINGS = 100_000
# A generation reconciles once its newest completion row and every dump it lists are stamped, and each
# table's listed rows equal the rows the run read (mdp_reference_generations(), at any close).
RECONCILED = """
WITH completion AS (
  SELECT DISTINCT ON (run_id) run_id::text AS run_id, _dump_id, recorded::jsonb AS recorded, outputs::jsonb AS outputs
  FROM raw._run_completion WHERE source_key = 'mb_spine'
  ORDER BY run_id, _landed_seq DESC, _dump_id DESC
), listed AS (
  SELECT c.run_id, t.key AS target_table, d.key::uuid AS dump_id, d.value::bigint AS row_count
  FROM completion c CROSS JOIN LATERAL jsonb_each(c.outputs) t CROSS JOIN LATERAL jsonb_each_text(t.value) d
)
SELECT c.recorded->>'generation' AS generation, c.run_id
FROM completion c
WHERE c.recorded->>'generation' IS NOT NULL
  AND EXISTS (SELECT 1 FROM raw.dump_stamps s WHERE s.dump_id = c._dump_id)
  AND NOT EXISTS (SELECT 1 FROM listed l WHERE l.run_id = c.run_id
                  AND NOT EXISTS (SELECT 1 FROM raw.dump_stamps s WHERE s.dump_id = l.dump_id))
  AND NOT EXISTS (
    SELECT 1 FROM (SELECT target_table, sum(row_count) AS n FROM listed l WHERE l.run_id = c.run_id GROUP BY 1) t
    FULL JOIN (SELECT key AS target_table, value::bigint AS n FROM jsonb_each_text(c.recorded->'mirror_counts')) m
      USING (target_table)
    WHERE coalesce(t.n, 0) <> coalesce(m.n, CASE WHEN target_table = 'raw.mb_generation' THEN 1 ELSE 0 END))
ORDER BY 1 DESC, 2
"""


def existing(conn: psycopg.Connection) -> set[str]:
    return {r["relname"] for r in conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'raw'").fetchall()}


def retain(warehouse_url: str) -> dict[str, Any]:
    """Keep the newest two reconciled generations of every raw.mb_* table: delete older generations, and
    the rows of runs other than the reconciled one within a kept generation. Nothing is deleted before a
    second generation reconciles, and a newer generation still landing is never touched."""
    with psycopg.connect(warehouse_url, row_factory=dict_row) as conn:
        present = existing(conn)
        # A table the extension added lands with its first generation; retention covers it from then on.
        tables = [t for t in MB_TABLES if t in present]
        if not {"_run_completion", "dump_stamps", "mb_generation", "mb_recording"} <= present:
            # Nothing has landed yet: every table appears with its first dump.
            return {"kept": [], "deleted": {}}
        reconciled: dict[str, str] = {}
        for row in conn.execute(RECONCILED).fetchall():
            reconciled.setdefault(row["generation"], row["run_id"])
        kept = sorted(reconciled, reverse=True)[:KEEP_GENERATIONS]
        if len(kept) < KEEP_GENERATIONS:
            return {"kept": kept, "deleted": {}}
        deleted = {}
        for table in tables:
            n = conn.execute(
                f"DELETE FROM raw.{table} WHERE mb_generation < %s OR (mb_generation = ANY(%s) "
                "AND (mb_generation, _run_id::text) <> ALL (SELECT * FROM unnest(%s::text[], %s::text[])))",
                (kept[-1], kept, kept, [reconciled[g] for g in kept])).rowcount
            if n:
                deleted[f"raw.{table}"] = n
        return {"kept": kept, "deleted": deleted}


def volume_guard(warehouse_url: str, volume_bytes: int, ceiling: float, seeds: int,
                 bytes_per_seed: int) -> dict[str, Any]:
    """Whether landing one more generation keeps the pgdata volume under `ceiling`. The landing adds a
    generation to raw and its copy in the reference models, each estimated at the newest generation's
    raw size, or at `bytes_per_seed` per tracked seed before the first."""
    with psycopg.connect(warehouse_url, row_factory=dict_row) as conn:
        used = conn.execute(
            "SELECT coalesce(sum(pg_database_size(datname)), 0)::bigint AS n FROM pg_database "
            "WHERE datallowconn AND has_database_privilege(datname, 'CONNECT')").fetchone()["n"]
        sizes = conn.execute(
            "SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)::bigint AS bytes FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'raw' AND c.relname = ANY(%s)",
            (list(MB_TABLES),)).fetchone()["bytes"]
        present = existing(conn)
        generations = conn.execute("SELECT count(DISTINCT mb_generation) AS n FROM raw.mb_generation").fetchone()["n"] \
            if "mb_generation" in present else 0
        recordings = conn.execute(
            "SELECT count(*) AS n FROM raw.mb_recording WHERE mb_generation = (SELECT max(mb_generation) FROM raw.mb_recording)"
        ).fetchone()["n"] if "mb_recording" in present else 0
    generation = sizes // generations if generations else seeds * bytes_per_seed
    projected = used + 2 * generation
    share = round(2 * generation / volume_bytes, 4) if volume_bytes else None
    return {"used_bytes": used, "generation_bytes": generation, "projected_bytes": projected,
            "limit_bytes": int(volume_bytes * ceiling), "stop": projected > volume_bytes * ceiling,
            "projected_write_share": share, "tracked_recordings": recordings,
            "narrowing_due": (share or 0) > NARROW_SHARE or recordings > NARROW_RECORDINGS}


# A build that reads a pruned generation fails in dbt with this class (mdp_reference_guard); the runner
# reports it and the service opens one alert per dbt run until a repair re-lands the generation.
INCOMPLETE = "reference_generation_incomplete"


def open_incomplete(conn: psycopg.Connection, run_id: str) -> tuple[str, bool]:
    """The dbt run's one reference_generation_incomplete alert, opened by the service as functions_rt."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{INCOMPLETE}:{run_id}",))
    found = conn.execute(
        "SELECT id FROM control.alert WHERE class=%s AND subject_type='dbt_run' AND subject_id=%s", (INCOMPLETE, run_id)
    ).fetchone()
    if found:
        return str(found["id"]), False
    opened = conn.execute(
        "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) "
        "VALUES (%s,'critical','dbt_run',%s,'reference-generation-incomplete') RETURNING id",
        (INCOMPLETE, run_id),
    ).fetchone()
    return str(opened["id"]), True


@transport_network()
def report_incomplete(run_id: str, target: str) -> str:
    """After a failed dbt run: when a model failed with reference_generation_incomplete, ask the service
    to open its alert. Prints what it did; a failure to send is printed, never raised."""
    import json
    from pathlib import Path

    import httpx

    try:
        results = json.loads((Path(target) / "run_results.json").read_text())["results"]
    except (OSError, ValueError, KeyError):
        return ""
    if not any(INCOMPLETE + ":" in (r.get("message") or "") for r in results):
        return ""
    url, token = os.environ.get("MDP_SERVICE_URL"), os.environ.get("MDP_SERVICE_TOKEN")
    if not url or not token:
        return f"ALERT {INCOMPLETE} not sent: set MDP_SERVICE_URL and MDP_SERVICE_TOKEN"
    try:
        response = httpx.post(f"{url.rstrip('/')}/v1/alerts/{INCOMPLETE}", json={"run_id": run_id},
                              headers={"Authorization": f"Bearer {token}"}, timeout=30)
        response.raise_for_status()
    except httpx.HTTPError as error:
        return f"ALERT {INCOMPLETE} not sent ({error.__class__.__name__})"
    return f"ALERT {INCOMPLETE} {response.json()['alert_id']}"


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["report-incomplete"]:
        print(report_incomplete(sys.argv[2], sys.argv[3]))
