"""Land a MusicBrainz dump generation of the identity spine, scoped to the tracked catalog.

The spine is the closure of the tracked catalog: every ISRC and platform track, album and artist id in
`int_identity__track_inputs`, closed upward through recordings, tracks, releases, release groups,
artist credits and artists, with their URL relationships to tracked platforms and reference and social
hosts, and the merge redirects into them. From it also reaches every label an artist has an
affiliation with, up to its root owner, with the artists' IPI and ISNI codes. Its size follows the tracked catalog, not MusicBrainz; ids tracked later join at the next
generation, and mb_resolve lands the rows it touches meanwhile (raw.mb_resolve_closure).

It is a universal function of the weekly job: the runtime reads the hourly relation off-job into the
run's input dump, so a retried attempt resumes over the same catalog. A generation lands only when
the mirror holds a validated generation newer than the last one this source landed; otherwise the run
lands nothing. The closure and every table are read in one REPEATABLE READ transaction on the private
mirror, so the generation's rows are one snapshot, and each page commits its cursor with its dumps, so a
retried attempt resumes the same run where it stopped. A new run restarts the generation, so one
completion row lists every dump of it. `raw.mb_generation`, with the rows read per table, is the
function's last output; the runtime then writes `raw._run_completion`.

Before the body, prepare() keeps the newest two reconciled generations of every raw.mb_* table and
stops the landing, with a `warehouse_disk_high` alert, when one more generation would take the pgdata
volume past its ceiling. It also measures the closure trigger of 22.12 (the projected write's share of
the volume, and the tracked recordings) and opens `spine_narrowing_due` once either passes its line.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from mdp_functions.control_db import alert, event
from mdp_functions.layers import Ctx, universal
from mdp_functions.musicbrainz import (
    GENERATION_SQL,
    SCHEMAS,
    SPINE,
    closure,
    connect,
    label_closure,
    mirror_url,
    seed_ids,
    spine_query,
    spine_row,
    tracked_seeds,
)
from mdp_functions.reference import retain, volume_guard

INPUTS = "intermediate.int_identity__track_inputs"
PAGE_ROWS = 50_000


# Raw bytes a generation lands per tracked seed on the scoped profile (reference-profile.json), the
# guard's estimate before the first generation lands.
BYTES_PER_SEED = 2000


def prepare(rt: Any, run: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    url = getattr(rt.warehouse_for(run["warehouse_id"]), "url", None)
    if url is None:
        # A local DuckDB warehouse has no pgdata volume and keeps what it lands.
        return {}
    retained = retain(url)
    seeds = tracked_seeds(ctx.read(INPUTS))
    guard = volume_guard(url, rt.settings.pgdata_volume_bytes, rt.settings.pgdata_ceiling, len(seeds), BYTES_PER_SEED)
    with rt.db.transaction() as conn:
        event(conn, run["id"], "mb_spine_prepared", "Retention and volume guard", {**retained, **guard, "seeds": len(seeds)})
        if guard["stop"]:
            alert(conn, run["id"], "warehouse_disk_high", str(run["id"]), "critical")
        if guard["narrowing_due"] and not conn.execute(
            "SELECT 1 FROM control.alert WHERE class='spine_narrowing_due' AND resolved_at IS NULL"
        ).fetchone():
            # The closure trigger: the landing goes on, and the narrowed release step ships
            # before the next import.
            alert(conn, run["id"], "spine_narrowing_due", str(run["id"]))
    return {**guard, "retained": retained}


@universal(
    source_key="mb_spine",
    exclusion_reasons=("untracked_url",),
    reads=[INPUTS],
    writes=list(SCHEMAS),
    cadence="weekly",
    external=True,
    completion=True,
    key=["mb_generation", "mb_key"],
    schema=SCHEMAS,
    prepare=prepare,
    # A scoped landing reads the closure of the tracked catalog; the weekly invoke waits for it.
    # Starts disabled: enable through control_rt once apply-mdp-schema.py has run on the mirror and
    # MDP_MB_DB_URL is set on mdp-functions (ops/fly/SECRETS.md).
    knobs={"enabled": False, "timeout_s": 3600, "batch_size": 1, "max_concurrency": 1},
)
async def mb_spine(ctx: Ctx, inputs: list[dict[str, Any]]) -> None:
    if ctx.prepared.get("stop"):
        ctx.logger.warning("mb_spine_disk_high", **{k: v for k, v in ctx.prepared.items() if k != "retained"})
        return
    seeds = tracked_seeds(inputs)
    conn = await asyncio.to_thread(connect, mirror_url())
    # One snapshot for the generation record and every table.
    conn.isolation_level, conn.read_only = psycopg.IsolationLevel.REPEATABLE_READ, True
    try:
        generation = await asyncio.to_thread(lambda: conn.execute(GENERATION_SQL).fetchone())
        state = ctx.cursor(None) or {}
        landed = state.get("landed")
        if not generation or generation["validated_at"] is None:
            ctx.logger.info("mb_spine_no_validated_generation")
            return
        if landed is not None and generation["generation"] <= landed:
            ctx.logger.info("mb_spine_generation_current", generation=generation["generation"])
            return
        if state.get("run_id") != ctx.run_id or state.get("generation") != generation["generation"]:
            # A new run, or a promote between attempts: start the generation over.
            state = {"landed": landed, "run_id": ctx.run_id, "generation": generation["generation"],
                     "done": [], "table": None, "after": None, "counts": {}}
        stamp = {"mb_channel": "dump", "mb_generation": generation["generation"],
                 "mb_sequence": generation["replication_sequence"]}
        ids = await asyncio.to_thread(lambda: closure(conn, **seed_ids(conn, seeds)))
        ids["labels"] = await asyncio.to_thread(label_closure, conn, ids["artists"])
        for table in SPINE:
            if table in state["done"]:
                continue
            after = state["after"] if state["table"] == table else None
            keys = SPINE[table][0]
            query, params = spine_query(table, ids, after)
            with conn.cursor(name="mdp_mb_spine") as cursor:
                await asyncio.to_thread(cursor.execute, query, params)
                while rows := await asyncio.to_thread(cursor.fetchmany, PAGE_ROWS):
                    ctx.observed(len(rows))
                    for row in rows:
                        landed = spine_row(table, row)
                        if landed is None:
                            ctx.exclude(row, reason="untracked_url")
                            continue
                        ctx.emit(table, {**landed, **stamp})
                    state["counts"][table] = state["counts"].get(table, 0) + len(rows)
                    state["table"], state["after"] = table, [rows[-1][k] for k in keys]
                    ctx.set_cursor(None, json.loads(json.dumps(state, default=str)))
                    # Each chunk is a page: its dumps and cursor register before the next read.
                    await ctx.http.before_page()
            state["done"].append(table)
            state["table"], state["after"] = None, None
        ctx.observed(1)
        ctx.emit("raw.mb_generation", {
            **stamp, "mb_key": generation["generation"], "export_date": generation["export_date"],
            "schema_sequence": generation["schema_sequence"], "imported_at": generation["imported_at"],
            "validated_at": generation["validated_at"],
            "mirror_counts": json.dumps(state["counts"], sort_keys=True),
            "read_at": datetime.now(UTC),
        })
        ctx.record_completion(generation=generation["generation"], sequence=generation["replication_sequence"],
                              mirror_counts=state["counts"])
        state = {"landed": generation["generation"]}
        ctx.set_cursor(None, state)
    finally:
        await asyncio.to_thread(conn.close)
