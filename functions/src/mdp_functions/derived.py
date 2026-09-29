"""Declared warehouse inputs and isolated silver execution; no landing protocol changes."""

import asyncio
import inspect
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx, input_identity
from mdp_functions.warehouse.postgres import manifest_sql


def declared_input(settings, manifest, relation):
    """Normalize dbt's compiled relation and require its declared input mapping."""
    from psycopg.conninfo import conninfo_to_dict

    if not relation:
        return relation
    # Tenant slugs may hold a hyphen (tenant_acme-records_marts). dbt quotes such a name, the resolved form
    # drops the quotes, and every read quotes each part again (sql.Identifier), so both forms accept it.
    if not re.fullmatch(
        r'(?:"[a-z_][a-z0-9_-]*"|[a-z_][a-z0-9_-]*)(?:\.(?:"[a-z_][a-z0-9_-]*"|[a-z_][a-z0-9_-]*)){1,2}',
        relation,
    ):
        raise ServiceError(
            "undeclared_read", "Resolved input must be a declared relation"
        )
    parts = [part.strip('"') for part in relation.split(".")]
    if len(parts) == 3 and parts.pop(0) != conninfo_to_dict(
        settings.service_read_url
    ).get("dbname"):
        raise ServiceError(
            "undeclared_read", "Input database differs from the warehouse"
        )
    resolved = ".".join(parts)
    if tenant_read(manifest, resolved)[0] not in manifest.reads:
        raise ServiceError(
            "undeclared_read",
            "Resolved input must be declared in the input mapping",
        )
    return resolved


# A tenant-bound function declares tenant relations without the slug (tenant_marts.x); its tenant
# job compiles them into tenant_<slug>_marts.x, the one tenant's schema.
TENANT_SCHEMA = re.compile(r"tenant_(?P<slug>[a-z0-9_-]+?)_(?P<layer>staging|intermediate|marts)")


def tenant_read(manifest, resolved):
    """(declared name, slug) for a compiled relation; the slug is None for a global one."""
    schema, _, name = resolved.partition(".")
    found = TENANT_SCHEMA.fullmatch(schema) if manifest.tenant_bound else None
    return (f"tenant_{found['layer']}.{name}", found["slug"]) if found else (resolved, None)


def read_inputs(settings, manifest, relation=None):
    relation = declared_input(settings, manifest, relation)
    slug = tenant_read(manifest, relation)[1] if relation else None
    names = {}
    for name in manifest.reads:
        schema, _, table = name.partition(".")
        if schema.startswith("tenant_") and schema.count("_") == 1:
            if not slug:
                raise ServiceError("undeclared_read", "A tenant relation needs the tenant job's input relation")
            name = f"tenant_{slug}_{schema.removeprefix('tenant_')}.{table}"
        require_shared_input(name)
        names[name] = name
    with psycopg.connect(settings.service_read_url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='30s'")
        rows = {
            name: conn.execute(
                sql.SQL("SELECT * FROM {}").format(sql.Identifier(*name.split(".")))
            ).fetchall()
            for name in names
        }
    # Functions read by the declared name; the input relation keeps its compiled name too.
    return {**{tenant_read(manifest, name)[0]: value for name, value in rows.items()}, **rows}


INPUT_PAGE_ROWS = 5000
# Postgres type OIDs of input columns, as the runtime's column types.
PG_TYPES = {16: "boolean", 20: "bigint", 21: "bigint", 23: "bigint", 700: "double", 701: "double",
            1700: "double", 1082: "date", 1114: "timestamptz", 1184: "timestamptz", 114: "jsonb", 3802: "jsonb"}


def require_shared_input(relation):
    from mdp_functions.relation_labels import relation_label

    schema, name = relation.split('.')
    if 'private' in relation_label(schema, name).get('shared_privacy', {}).values():
        raise ServiceError('undeclared_read', 'Retained text is private to dbt transforms. Read derived counts instead.')


def relation_pages(settings, relation, page, off_job=False, types=None, order=()):
    """A declared relation in server-side cursor pages; never a whole-relation fetch. A read outside
    the relation's own job first takes ACCESS SHARE and reads every page in one REPEATABLE READ
    transaction, so a table swap between pages never splits it. A declared input order pages
    oldest first, so a time budget leaves the newest inputs for the next run."""
    require_shared_input(relation)
    name = sql.Identifier(*relation.split("."))
    ordered = sql.SQL(" ORDER BY {}").format(sql.SQL(",").join(map(sql.Identifier, order))) if order else sql.SQL("")
    with psycopg.connect(settings.service_read_url, row_factory=dict_row) as conn:
        if off_job:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute(sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(name))
        else:
            conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='30s'")
        with conn.cursor(name="mdp_input_pages") as cursor:
            cursor.execute(sql.SQL("SELECT * FROM {}{}").format(name, ordered))
            while rows := cursor.fetchmany(page):
                if types is not None and not types:
                    types.update({c.name: PG_TYPES.get(c.type_code, "text") for c in cursor.description})
                yield rows


def completed(visible, identity, manifest):
    """Step 1b validity: a manifest-visible completion whose listed dumps are visible with their counts."""
    key = identity_key(identity)
    seen = {table: grouped.get(key, []) for table, grouped in visible.items()}
    return any(reconciles(c["required_outputs"], seen, manifest) for c in visible[COMPLETION].get(key, []))


def pending_pages(rt, run, manifest, relation, page=None, types=None, counts=None):
    """Input pages with every input whose completion is valid for the run's cycle removed, before
    anything is snapshotted, observed, or rejected. Yields (pending rows, rows read)."""
    seen = set()
    off_job = bool((run.get("resolved_config") or {}).get("rerun"))
    parked = parked_inputs(rt, run, manifest)
    for rows in relation_pages(rt.settings, relation, page or INPUT_PAGE_ROWS, off_job, types, manifest.input_order):
        identities = [input_identity(row) for row in rows]
        visible = visible_outputs(rt, run, manifest, identities)
        pending = []
        for row, identity in zip(rows, identities, strict=True):
            key = identity_key(identity)
            if park_key(row) in parked:
                if counts is not None and key not in seen:
                    seen.add(key)
                    counts["parked"] += 1
                    counts.setdefault("parked_refs", []).append(row.get("input_ref"))
                continue
            if key not in seen and not completed(visible, identity, manifest):
                seen.add(key)
                pending.append(row)
        yield pending, len(rows)


def snapshot_inputs(rt, run, manifest, relation, page=None):
    """Read the pending inputs, every input whose completion is not valid for the run's cycle, into
    parts. In the relation's own job a part enters the input dump when the run takes it up, so the
    dump holds only what the run processes; a read outside that job (a rerun by config_version)
    snapshots every page before processing. Other declared reads enter in full. A retry reuses the
    parts. Returns (parts, input dump id, the input relation's column types)."""
    from mdp_functions.control_db import event

    existing = rt.db.one(
        "SELECT r.input_dump_id,d.files FROM control.run r JOIN control.dump d ON d.id=r.input_dump_id WHERE r.id=%s",
        (run["id"],),
    )
    if existing:
        read = rt.db.one(
            "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='input_snapshot' ORDER BY at DESC LIMIT 1",
            (run["id"],),
        )
        parts = read["attrs"]["candidates"] if read and "candidates" in read["attrs"] else existing["files"]
        return parts, existing["input_dump_id"], (read["attrs"].get("column_types") if read else None) or {}
    off_job = bool((run.get("resolved_config") or {}).get("rerun"))
    identity, parts, counts, types = uuid4(), [], {"read": 0, "pending": 0, "parked": 0}, {}

    def put(name, rows):
        key = f"inputs/{run['id']}/{identity}/part-{len(parts):05d}.json"
        data = json.dumps(rows, default=str).encode()
        rt.store.put(key, data)
        parts.append({"key": key, "name": key.rsplit("/", 1)[1], "role": "input", "format": "json",
                      "bytes": len(data), "row_count": len(rows), "relation": name})

    for rows, read in pending_pages(rt, run, manifest, relation, page, types, counts):
        counts["read"] += read
        counts["pending"] += len(rows)
        if rows:
            put(relation, rows)
    # A tenant-bound function declares its reads without the slug; the input relation is compiled.
    declared, slug = tenant_read(manifest, relation)
    for name in manifest.reads:
        if name != declared:
            schema, _, table = name.partition(".")
            compiled = f"tenant_{slug}_{schema.removeprefix('tenant_')}.{table}" if (
                slug and schema.startswith("tenant_") and schema.count("_") == 1) else name
            for rows in relation_pages(rt.settings, compiled, page or INPUT_PAGE_ROWS, off_job):
                put(name, rows)
    files = [part for part in parts if off_job or part["relation"] != relation]
    with rt.db.transaction() as conn:
        locked = conn.execute(
            "SELECT input_dump_id FROM control.run WHERE id=%s FOR UPDATE", (run["id"],)
        ).fetchone()
        if not locked["input_dump_id"]:
            conn.execute(
                """INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,uri_prefix,files,row_count,function_version,published_at)
                VALUES (%s,'input',%s,%s,%s,%s,%s,%s,%s,now())""",
                (identity, run["id"], run["streamline_id"], run["cycle_id"],
                 rt.store.uri(f"inputs/{run['id']}/{identity}/"), Jsonb(files),
                 sum(p["row_count"] for p in files if p["relation"] == relation), rt.settings.image_digest),
            )
            conn.execute("UPDATE control.run SET input_dump_id=%s WHERE id=%s", (identity, run["id"]))
            refs = counts.pop("parked_refs", [])
            event(conn, run["id"], "input_snapshot", "Pending inputs read in parts",
                  {**counts, "parts": sum(p["relation"] == relation for p in parts), "candidates": parts,
                   "column_types": types})
            if counts["parked"]:
                parked_alert(conn, run, counts["parked"], refs)
    return snapshot_inputs(rt, run, manifest, relation, page) if locked["input_dump_id"] else (parts, identity, types)


# A vendor or mirror error fails one input, never the run: that input is a typed reject, which the
# next run retries (it has no completion), and the run ends partial. Consecutive failures open the
# circuit: the rest of the run's inputs are rejected with the same class without a call.
INPUT_ERRORS = {"vendor_retryable", "vendor_4xx"}
OPEN_CIRCUIT_AFTER = 3
# Reject reasons that may mean the surface changed (a missing envelope, a drifted shape). They count
# as surface drift only in a streak of OPEN_CIRCUIT_AFTER consecutive misses on inputs this
# streamline never rejected before, with no parsed input between them; otherwise each is its input's own.
DRIFT_REASONS = ("envelope_mismatch", "drift:")


class CircuitOpen(Exception):
    """Every remaining input of the run was rejected after consecutive vendor or mirror errors."""


def end_with_rejects(rt, run, error_class, rejected, parkable=(), drift=()):
    """`parkable` lists the (input_ref, input_version) of the inputs that failed on their own: a
    per-input 404 or other non-429 4xx, a record the function rejected, or a miss with a parsed input
    after it or an earlier rejection of the same input. It never holds an input a circuit rejected
    unasked, a drift streak, or a 5xx (parked_inputs). `drift` lists the misses not yet known to be
    their inputs' own; a later run counts a miss on one of them as that input's own (rejected_before).
    A surface_drift end opens the run's critical alert once per attempt."""
    from mdp_functions.control_db import alert, event

    with rt.db.transaction() as conn:
        conn.execute(
            "UPDATE control.run SET error_class=%s,error_message=%s WHERE id=%s AND error_class IS NULL",
            (error_class, f"{rejected} input(s) rejected by {error_class}; the next run retries them", run["id"]),
        )
        event(conn, run["id"], "inputs_rejected", "Rejected inputs end the run partial",
              {"error_class": error_class, "rejected": rejected,
               "parkable": [{"input_ref": r, "input_version": v} for r, v in sorted(set(parkable), key=str)],
               "drift": [{"input_ref": r, "input_version": v} for r, v in sorted(set(drift), key=str)]})
        if error_class == "surface_drift":
            alert(conn, run["id"], "surface_drift", str(run["id"]), severity="critical")


# A parked input sits out until its failures age out of this window, then is tried again.
PARK_WINDOW_DAYS = 28
# At or above this many parked inputs, the streamline's inputs_parked alert is a warning.
PARK_WARN_AT = 10


def park_key(row):
    return (row.get("input_ref"), row.get("input_version"))


def parked_inputs(rt, run, manifest):
    """(input_ref, input_version) pairs that failed on their own in at least `park_after` of this
    streamline's runs over the last PARK_WINDOW_DAYS, counting only runs after its last released
    inputs_parked alert (streamlines.unpark resolves it). They are left out of the read, so dead
    inputs never head the oldest-first order or open the circuit on live ones."""
    if not manifest.park_after:
        return set()
    return {(row["ref"], row["version"]) for row in rt.db.all(
        """SELECT k->>'input_ref' AS ref, k->>'input_version' AS version
           FROM control.run_event e JOIN control.run r ON r.id=e.run_id,
             jsonb_array_elements(coalesce(e.attrs->'parkable','[]'::jsonb)) k
           WHERE r.streamline_id=%s AND e.event_type='inputs_rejected'
             AND e.at > now() - make_interval(days => %s)
             AND e.at > coalesce((SELECT max(a.resolved_at) FROM control.alert a JOIN control.run ar ON ar.id=a.run_id
               WHERE ar.streamline_id=%s AND a.class='inputs_parked'), '-infinity')
           GROUP BY 1, 2 HAVING count(DISTINCT e.run_id) >= %s""",
        (run["streamline_id"], PARK_WINDOW_DAYS, run["streamline_id"], manifest.park_after),
    )}


def rejected_before(rt, run):
    """(input_ref, input_version) pairs an earlier run of this streamline rejected within
    PARK_WINDOW_DAYS, as its own failure or in a drift streak: a miss on one of them never starts or
    extends a drift streak, so inputs that head every read cannot pause the streamline again."""
    return {(row["ref"], row["version"]) for row in rt.db.all(
        """SELECT DISTINCT k->>'input_ref' AS ref, k->>'input_version' AS version
           FROM control.run_event e JOIN control.run r ON r.id=e.run_id,
             jsonb_array_elements(coalesce(e.attrs->'parkable','[]'::jsonb) || coalesce(e.attrs->'drift','[]'::jsonb)) k
           WHERE r.streamline_id=%s AND e.event_type='inputs_rejected' AND e.run_id<>%s
             AND e.at > now() - make_interval(days => %s)""",
        (run["streamline_id"], run["id"], PARK_WINDOW_DAYS),
    )}


def parked_alert(conn, run, count, refs):
    """One open inputs_parked alert per streamline while inputs sit out, and an event listing them."""
    from mdp_functions.control_db import alert, event

    event(conn, run["id"], "inputs_parked", "Inputs that kept failing sit out this read",
          {"parked": count, "input_refs": refs[:200]})
    if not conn.execute(
        "SELECT 1 FROM control.alert a JOIN control.run r ON r.id=a.run_id "
        "WHERE r.streamline_id=%s AND a.class='inputs_parked' AND a.resolved_at IS NULL",
        (run["streamline_id"],),
    ).fetchone():
        alert(conn, run["id"], "inputs_parked", str(run["id"]),
              severity="warning" if count >= PARK_WARN_AT else "info")


def end_by_budget(rt, run, manifest, reason=None):
    from mdp_functions.control_db import event

    with rt.db.transaction() as conn:
        conn.execute(
            "UPDATE control.run SET error_class='time_budget',error_message=%s WHERE id=%s",
            (reason or f"The {manifest.time_budget_s:g}s work budget ended. Check /runbooks/time-budget for the next scheduled read.", run["id"]),
        )
        event(conn, run["id"], "time_budget_ended", "A time limit stops new inputs. Check /runbooks/time-budget for the next scheduled read.",
              {"time_budget_s": manifest.time_budget_s})


def take_up(rt, dump_id, part):
    """A part enters the input dump when the run takes it up (idempotent for a retry)."""
    rt.db.execute(
        "UPDATE control.dump SET files=files || %s, row_count=row_count + %s WHERE id=%s AND NOT files @> %s",
        (Jsonb([part]), part["row_count"], dump_id, Jsonb([{"key": part["key"]}])),
    )


def snapshot_relations(rt, run, manifest):
    """The declared reads of a completion=True universal run: on its first attempt each relation is read
    off-job (ACCESS SHARE, one REPEATABLE READ transaction) into the run's input dump; a retried attempt
    reads that dump, so the run resumes over the inputs it started with."""
    from mdp_functions.control_db import event

    relations: dict[str, list[dict[str, Any]]] = {name: [] for name in manifest.reads}
    existing = rt.db.one(
        "SELECT d.files FROM control.run r JOIN control.dump d ON d.id=r.input_dump_id WHERE r.id=%s", (run["id"],))
    if existing:
        for part in existing["files"]:
            relations[part["relation"]].extend(load_part(rt, part))
        return relations
    identity, parts = uuid4(), []
    for name in manifest.reads:
        for rows in relation_pages(rt.settings, name, INPUT_PAGE_ROWS, off_job=True):
            key = f"inputs/{run['id']}/{identity}/part-{len(parts):05d}.json"
            data = json.dumps(rows, default=str).encode()
            rt.store.put(key, data)
            parts.append({"key": key, "name": key.rsplit("/", 1)[1], "role": "input", "format": "json",
                          "bytes": len(data), "row_count": len(rows), "relation": name})
            relations[name].extend(rows)
    with rt.db.transaction() as conn:
        locked = conn.execute("SELECT input_dump_id FROM control.run WHERE id=%s FOR UPDATE", (run["id"],)).fetchone()
        if locked["input_dump_id"]:
            return snapshot_relations(rt, run, manifest)
        conn.execute(
            """INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,uri_prefix,files,row_count,function_version,published_at)
            VALUES (%s,'input',%s,%s,%s,%s,%s,%s,%s,now())""",
            (identity, run["id"], run["streamline_id"], run["cycle_id"], rt.store.uri(f"inputs/{run['id']}/{identity}/"),
             Jsonb(parts), sum(p["row_count"] for p in parts), rt.settings.image_digest),
        )
        conn.execute("UPDATE control.run SET input_dump_id=%s WHERE id=%s", (identity, run["id"]))
        event(conn, run["id"], "input_snapshot", "Declared reads snapshotted off-job",
              {"relations": {name: len(rows) for name, rows in relations.items()}, "parts": len(parts)})
    return relations


def load_part(rt, part):
    return part["rows"] if "rows" in part else json.loads(rt.store.get(part["key"]))


async def call_function(ctx, rows):
    from mdp_functions.fetch.guard import source_network

    with source_network(ctx):
        result = ctx.manifest.function(ctx, rows)
        if inspect.isasyncgen(result):
            async for row in result:
                ctx.yield_row(row)
        elif inspect.isawaitable(result):
            await result
        else:
            raise ServiceError("invalid_function", "Function must be async")


async def silver_execute(ctx: Ctx, rows: list[dict[str, Any]]):
    import sys

    import cloudpickle

    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(Path(__file__).parent / "silver_sandbox")
        + os.pathsep
        + os.pathsep.join(sys.path)
    )
    prefix = []
    if sys.platform == "linux" and shutil.which("unshare"):
        probe = await asyncio.to_thread(
            subprocess.run, ["unshare", "-n", "true"], capture_output=True, check=False
        )
        if probe.returncode == 0:
            prefix = ["unshare", "-n"]
    process = await asyncio.create_subprocess_exec(
        *prefix,
        sys.executable,
        "-m",
        "mdp_functions.silver_worker",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, _ = await process.communicate(cloudpickle.dumps((ctx, rows)))
        if process.returncode:
            raise ServiceError(
                "function_failed", "Silver subprocess exited without a result"
            )
        result = cloudpickle.loads(output)
        if "error_class" in result:
            ctx.observed(len(rows))
            for row in rows:
                ctx.reject(row, reason=result["error_class"])
            raise ServiceError(result["error_class"], result["message"])
        for key, value in result.items():
            setattr(ctx, key, value)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


COMPLETION = "raw._enrichment_completion"


def resolve_config(rt, manifest, version=None, paused=False):
    """Resolve once at admission: config_version is the declared decorator version plus the
    declared parameters (the llm_step or model_steps configuration and preprocessing). Neither the
    image digest nor any module source enters it. Retries and Replays read the frozen
    control.run.resolved_config; `version` is the llm_step `step_version` a rerun requests. A paused
    (disabled) streamline resolves no llm_step, so it needs no prompt configuration or proxy."""
    import hashlib

    from mdp_functions.llm import step_for

    step = step_for(rt.db, manifest.llm_step, version) if manifest.llm_step and not paused else None
    if step and manifest.provider == "typesafe":
        from mdp_functions.jev import connection, question_set
        question_set(step)
        if not rt.settings.fixture:
            connection(rt.settings, step)
    if step and manifest.provider != "typesafe" and not (rt.settings.fixture and step["model"] == "local-stub") and (not rt.settings.litellm_base_url or not rt.settings.litellm_keys.get(step["litellm_key_alias"] or "")):
        raise ServiceError("litellm_unavailable", "the model proxy URL and configured key alias are required", 503)
    declared = json.loads(json.dumps({
        "version": manifest.version,
        "parameters": {
            "llm_step": step["step_version"] if step else None,
            "model_steps": manifest.model_steps,
            "preprocessing": manifest.preprocessing,
        },
    }, default=str))
    digest = hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    resolved = json.loads(json.dumps({
        **declared,
        "llm_step": step,
        "model_steps": manifest.model_steps,
        "preprocessing": manifest.preprocessing,
        "output_key": manifest.output_key,
        "writes": manifest.writes,
        "input_eligibility": manifest.input_eligibility,
    }, default=str))
    resolved["step"] = (str(step["id"]) if step else
        "models:" + hashlib.sha256(json.dumps(manifest.model_steps, sort_keys=True).encode()).hexdigest()
        if manifest.model_steps else ("silver:" if manifest.layer == "silver" else "external:") + manifest.source_key)
    resolved["requested"] = version
    return digest, resolved


def pg_identity(values):
    """mdp_input_identity's hash of typed values as Postgres computes it: md5 of jsonb_build_array(...)::text
    (dates as ISO text, jsonb's ", " separator)."""
    import hashlib

    text = json.dumps([v.isoformat() if hasattr(v, "isoformat") else v for v in values],
                      separators=(", ", ": "), ensure_ascii=False)
    return hashlib.md5(text.encode()).hexdigest()


def identity_key(identity):
    return json.dumps([identity["input_ref"], identity["input_version"]])


def output_identity(row, keys):
    # Text form, so a key typed by the warehouse matches the value the function emitted.
    return json.dumps([None if row.get(k) is None else str(row.get(k)) for k in keys])


def visible_outputs(rt, run, manifest, identities):
    """This configuration's committed rows in the run's cycle manifest, by input identity. A silver
    input's completion holds under any configuration: a version bump applies from the next input and
    never reprocesses a completed one."""
    refs = [i["input_ref"] for i in identities]
    versions = [i["input_version"] for i in identities]
    result = {}
    with psycopg.connect(rt.settings.service_read_url, row_factory=dict_row) as conn:
        for table in [*manifest.writes, COMPLETION]:
            grouped = result[table] = {}
            wanted = ["input_ref", "input_version", "_dump_id",
                      *(["required_outputs"] if table == COMPLETION else manifest.table_key(table))]
            columns = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name=%s",
                (table.split(".")[1],))}
            # Legacy outputs without the enrichment identity cannot prove completion.
            if not {"scope", "step", "config_version", *wanted} <= columns:
                continue
            for row in conn.execute(sql.SQL(
                "SELECT {} FROM {} WHERE _source_key=%(source)s AND scope=%(scope)s AND step=%(step)s "
                "AND (%(any_config)s OR config_version=%(config)s) "
                "AND (input_ref,input_version) IN (SELECT * FROM unnest(%(refs)s::text[],%(versions)s::text[])) "
                "AND _dump_id IN (" + manifest_sql("%(cycle)s", table) + ") ORDER BY _landed_seq DESC, _dump_id DESC"
            ).format(sql.SQL(",").join(map(sql.Identifier, wanted)), sql.Identifier(*table.split("."))),
                {"source": manifest.source_key, "scope": str(run["tenant_id"] or "global"),
                 "step": run["resolved_config"]["step"], "config": run["config_version"],
                 "any_config": manifest.layer == "silver",
                 "refs": refs, "versions": versions, "cycle": run["cycle_id"]}):
                grouped.setdefault(identity_key(row), []).append(row)
    return result


def reconciles(required, visible, manifest):
    """Every required dump is visible with its recorded count, and together they carry every key."""
    if set(required) != set(manifest.writes):
        return False
    for table, expected in required.items():
        rows = [r for r in visible.get(table, []) if str(r["_dump_id"]) in expected["dumps"]]
        if dict(Counter(str(r["_dump_id"]) for r in rows)) != expected["dumps"]:
            return False
        if not set(expected["keys"]) <= {output_identity(r, manifest.table_key(table)) for r in rows}:
            return False
    return True


def required_outputs(outputs, visible, landed, manifest):
    """The dumps and counts that carry every output key of one input, or None while any is missing."""
    required = {}
    for table, records in outputs.items():
        rows = [*landed.get(table, []), *visible.get(table, [])]
        chosen = {}
        for row in rows:
            chosen.setdefault(output_identity(row, manifest.table_key(table)), str(row["_dump_id"]))
        keys = sorted({output_identity(r, manifest.table_key(table)) for r in records})
        if not set(keys) <= chosen.keys():
            return None
        dumps = {chosen[k] for k in keys}
        required[table] = {
            "dumps": dict(Counter(str(r["_dump_id"]) for r in rows if str(r["_dump_id"]) in dumps)),
            "keys": keys,
            "output_key": manifest.table_key(table),
        }
    return required


def retain(rt, ctx, run, computed):
    """Keep paid-for results before publication, so a retry never repeats a completed call."""
    from mdp_functions.control_db import event, fence

    key = f"enrichment/{run['id']}/{uuid4()}.json"
    rt.store.put(key, json.dumps(computed, default=str).encode())
    with rt.db.transaction() as conn:
        fence(conn, ctx.batch)
        event(conn, run["id"], "enrichment_prepared", "Per-input output sets retained",
              {"key": key, "inputs": len(computed)})


def retained(rt, run):
    computed = {}
    for row in rt.db.all(
        "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='enrichment_prepared' ORDER BY at",
        (run["id"],),
    ):
        computed.update(json.loads(rt.store.get(row["attrs"]["key"])))
    return computed


async def silver_inputs(rt, ctx, run, manifest, relation, rows, flush):
    """Step 1b per-input completion for a silver function declaring input_key. Rows whose
    input already has a completion visible in the run cycle's manifest are dropped before the body runs,
    so a later run for a completed input lands nothing. The body sees the pending rows in its sandbox;
    their outputs land first, then one completion row per input whose outputs all landed. An input the
    cycle owns (Manifest.cycle_inputs) completes with no rows at all, and also when some of its rows were
    rejected: the rejects stay in raw._rejected, and a later cycle of that input lands nothing. Any other
    input with a rejected row gets no completion, so the next run takes it up again."""
    config = run["resolved_config"]
    if not config:
        raise ServiceError("config_unresolved", "Silver run predates its frozen configuration; admit a new run")
    identities = [input_identity(row) for row in rows]
    owned = [{"input_ref": pg_identity([values[k] for k in manifest.input_key]),
              "input_version": pg_identity([values[k] for k in manifest.input_version])}
             for values in (manifest.cycle_inputs(ctx.cycle) if manifest.cycle_inputs else [])]
    unique = list({identity_key(i): i for i in [*identities, *owned]}.values())
    visible = await asyncio.to_thread(visible_outputs, rt, run, manifest, unique)
    pending = [row for row, i in zip(rows, identities, strict=True) if not completed(visible, i, manifest)]
    keys = {identity_key(input_identity(row)) for row in pending}
    keys |= {identity_key(i) for i in owned if not completed(visible, i, manifest)}
    if not keys:
        await flush(True)
        return
    ctx.relations[relation] = pending
    ctx.input_metadata = {"scope": str(run["tenant_id"] or "global"), "step": config["step"],
                          "config_version": run["config_version"]}
    if pending:
        await silver_execute(ctx, pending)
    outputs = {key: {table: [] for table in manifest.writes} for key in keys}
    for table, records in ctx.outputs.items():
        for record in records:
            outputs[identity_key(record)][table].append(record)
    for grouped in outputs.values():
        for table, records in grouped.items():
            found = [output_identity(r, manifest.table_key(table)) for r in records]
            if len(set(found)) != len(found) or any(r.get(k) is None for r in records for k in manifest.table_key(table)):
                raise ServiceError("invalid_output_key", "Silver output keys must be present and unique per input and table")
    rejected = {identity_key(r["record"]) for r in ctx.rejected
                if isinstance(r.get("record"), dict) and r["record"].get("input_ref") and r["record"].get("input_version")}
    await flush()
    loaded = {str(r["dump_id"]) for r in await asyncio.to_thread(rt.db.all,
        "SELECT dump_id FROM control.load WHERE warehouse_id=%s AND dump_id=ANY(%s::uuid[]) AND status='loaded'",
        (run["warehouse_id"], [records[0]["_dump_id"] for records in ctx.published.values()]))}
    published: dict[str, dict[str, list]] = {}
    landed: dict[str, dict[str, list]] = {}
    for table, records in ctx.published.items():
        for record in records:
            published.setdefault(identity_key(record), {}).setdefault(table, []).append(record)
            if records[0]["_dump_id"] in loaded:
                landed.setdefault(identity_key(record), {}).setdefault(table, []).append(record)
    completions = []
    owned_keys = {identity_key(i) for i in owned}
    for key in sorted(keys - (rejected - owned_keys)):
        # A cycle-owned input completes on what it published, since rows rejected in the sandbox or at
        # the dump never publish; every published row must still have landed.
        expected = ({table: published.get(key, {}).get(table, []) for table in manifest.writes}
                    if key in owned_keys else outputs[key])
        required = required_outputs(expected, {}, landed.get(key, {}), manifest)
        if required is not None:
            ref, version = json.loads(key)
            completions.append({"input_ref": ref, "input_version": version, **ctx.input_metadata,
                                "required_outputs": required})
    if completions:
        # Completion is evidence, not a published output in run accounting.
        ctx.outputs, ctx.observed_count, ctx.yielded_count = {COMPLETION: completions}, 0, 0
        await flush()
    await flush(True)


async def execute_derived(rt, ctx, run, manifest, flush):
    from mdp_functions.llm import LLM
    from mdp_functions.promoter import ControlTargets

    relation = run.get("input_relation") or (manifest.reads[0] if manifest.reads else None)
    if manifest.layer != "gold":
        if manifest.layer == "universal" and manifest.completion:
            ctx.relations = await asyncio.to_thread(snapshot_relations, rt, run, manifest)
        else:
            ctx.relations = await asyncio.to_thread(read_inputs, rt.settings, manifest, run.get("input_relation"))
        rows = ctx.relations[relation] if relation else []
    if manifest.layer == "silver" and manifest.per_input:
        await silver_inputs(rt, ctx, run, manifest, relation, rows, flush)
        return
    if manifest.layer == "silver":
        await silver_execute(ctx, rows)
        await flush(True)
        return
    if manifest.layer == "universal":
        ctx.control = ControlTargets(rt.settings)
        if manifest.prepare is not None:
            ctx.prepared = await asyncio.to_thread(manifest.prepare, rt, run, ctx)
        await call_function(ctx, rows)
        await flush(True)
        return
    config = run["resolved_config"]
    if not config:
        raise ServiceError("config_unresolved", "Gold run predates its frozen configuration; admit a new run")
    step = config["llm_step"]
    ctx.resolved_config = config
    ctx.prompt = step["body"] if step else ""
    if step and manifest.provider == "typesafe":
        from mdp_functions.jev import for_run, question_set
        ctx._jev = for_run(rt, run, step, ctx)
        ctx.question_set = question_set(step)
    elif step:
        ctx.llm = LLM(rt, run, step, ctx)
    scope = str(run["tenant_id"] or "global")
    provider = manifest.provider or ("litellm" if step else manifest.source_key)
    chunk = max(1, (await asyncio.to_thread(
        rt.db.one, "SELECT batch_size FROM control.streamline WHERE id=%s", (run["streamline_id"],)))["batch_size"])
    eligibility = {}
    pending = []
    budget_reason = None

    async def land():
        """Outputs land first, then one completion row per input whose whole set reconciles."""
        fresh = {identity_key(i): o for i, o, new in pending if new}
        if fresh:
            await asyncio.to_thread(retain, rt, ctx, run, fresh)
        ctx.outputs = {}
        for identity, outputs, _ in pending:
            seen = visible_for(identity)
            for table, records in outputs.items():
                present = {output_identity(r, manifest.table_key(table)) for r in seen.get(table, [])}
                ctx.outputs.setdefault(table, []).extend(
                    r for r in records if output_identity(r, manifest.table_key(table)) not in present)
        ctx.outputs = {t: r for t, r in ctx.outputs.items() if r}
        landed = {}
        if ctx.outputs:
            # Gold keeps no record accounting; the page counts what it publishes.
            ctx.observed_count = ctx.yielded_count = sum(map(len, ctx.outputs.values()))
            await flush()
            loaded = {str(r["dump_id"]) for r in await asyncio.to_thread(rt.db.all,
                "SELECT dump_id FROM control.load WHERE warehouse_id=%s AND dump_id=ANY(%s::uuid[]) AND status='loaded'",
                (run["warehouse_id"], [records[0]["_dump_id"] for records in ctx.published.values()]))}
            for table, records in ctx.published.items():
                if records[0]["_dump_id"] in loaded:
                    for record in records:
                        landed.setdefault(identity_key(record), {}).setdefault(table, []).append(record)
        completions = []
        for identity, outputs, _ in pending:
            key = identity_key(identity)
            required = required_outputs(outputs, visible_for(identity), landed.get(key, {}), manifest)
            # A rejected or unlanded output leaves the input incomplete for the next run.
            if required is not None:
                completions.append({**identity, "scope": scope, "step": config["step"],
                                    "config_version": run["config_version"], "required_outputs": required})
        pending.clear()
        if completions:
            # Completion is evidence, not a published output in run accounting.
            ctx.outputs, ctx.observed_count, ctx.yielded_count = {COMPLETION: completions}, 0, 0
            await flush()

    def visible_for(identity):
        key = identity_key(identity)
        return {table: grouped.get(key, []) for table, grouped in visible.items()}

    async def process_part(rows, identities, later):
        """Returns False once the time budget ends: no new input is admitted after it."""
        nonlocal budget_reason
        for index, row in enumerate(rows):
            ctx.input_row = row
            identity = identities[index]
            key = identity_key(identity)
            if key in handled or completed(visible, identity, manifest):
                continue
            if deadline is not None and time.monotonic() >= deadline:
                return False
            handled.add(key)
            outputs = computed.get(key)
            if outputs is None:
                sources = row.get("_source_keys") or row.get("source_keys") or [row.get("source_key") or row.get("_source_key")]
                if isinstance(sources, str):
                    sources = json.loads(sources)
                # The function's own registry row counts beside the provider's: a contract read
                # may set an enrichment apart from its provider.
                contributing = tuple(sorted({*sources, provider, manifest.source_key}, key=str))
                if contributing not in eligibility:
                    # Missing registry rows count as ineligible: the LEFT JOIN makes the AND false.
                    eligibility[contributing] = (await asyncio.to_thread(rt.db.one,
                        "SELECT coalesce(bool_and(coalesce(r.learning_eligible,false)),false) AS eligible FROM unnest(%s::text[]) sources(source_key) LEFT JOIN control.rights_source r USING(source_key)",
                        (list(contributing),)))["eligible"]
                flags = {"learning_eligible", "grant_learning_eligible"} & row.keys()
                if relation in manifest.input_eligibility:
                    flags.add(manifest.input_eligibility[relation])
                ctx.input_metadata = {
                    "scope": scope, "step": config["step"],
                    "llm_step_id": str(step["id"]) if step else None,
                    "config_version": run["config_version"],
                    "run_admitted_at": run["created_at"],
                    # Annotation only: unknown row-level eligibility is false, and nothing is gated.
                    "learning_eligible": eligibility[contributing] and all(row.get(flag) is True for flag in flags),
                }
                try:
                    await call_function(ctx, [row])
                except ServiceError as exc:
                    ctx.outputs, ctx.yielded_count = {}, 0
                    if exc.error_class == "time_budget" and deadline is not None:
                        budget_reason = exc.message
                        return False
                    if exc.error_class in INPUT_ERRORS:
                        failures["run"] += 1
                        failures["consecutive"] += 1
                        failures["class"] = exc.error_class
                        if exc.error_class == "vendor_4xx":
                            # A 4xx is the input's own; a 5xx or a transport failure is the vendor's.
                            failures["parkable"].append(park_key(row))
                        ctx.observed(1)
                        ctx.reject(row, reason=exc.error_class)
                        await flush()
                        if failures["consecutive"] < OPEN_CIRCUIT_AFTER:
                            continue
                        # The vendor or mirror is down: reject what remains without calling it.
                        rest = rows[index + 1:]
                    else:
                        # Paid-for inputs still land; the failed input and the rest are rejected for retry.
                        await land()
                        rest = rows[index:]
                    for remaining in rest:
                        ctx.observed(1)
                        ctx.reject(remaining, reason=exc.error_class)
                        failures["run"] += exc.error_class in INPUT_ERRORS
                    await flush()
                    # Later parts are rejected for retry too, one part at a time.
                    for part in later:
                        for remaining in await asyncio.to_thread(load_part, rt, part):
                            ctx.observed(1)
                            ctx.reject(remaining, reason=exc.error_class)
                            failures["run"] += exc.error_class in INPUT_ERRORS
                        await flush()
                    if exc.error_class in INPUT_ERRORS:
                        # A run whose circuit opened parks nothing: its rejects may be the vendor's.
                        failures["parkable"].clear()
                        raise CircuitOpen from exc
                    raise
                failures["consecutive"] = 0
                if ctx.rejected:
                    # The function rejected this input: it stays incomplete for the next run, and
                    # the run ends partial. An envelope or shape miss on an input never rejected
                    # before joins the drift streak; OPEN_CIRCUIT_AFTER in a row, with no parsed
                    # input between, is surface_drift and ends the run's reads (the rest wait,
                    # unrejected). Any other reject is the input's own.
                    failures["rejected"] += 1
                    drift = any(str(r["reason"]).startswith(DRIFT_REASONS) for r in ctx.rejected)
                    ctx.observed_count = ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())
                    await flush()
                    if not drift or park_key(row) in failures["seen"]:
                        failures["parkable"].append(park_key(row))
                        continue
                    failures["streak"].append(park_key(row))
                    if len(failures["streak"]) >= OPEN_CIRCUIT_AFTER:
                        failures["drift"] = True
                        return False
                    continue
                outputs = {table: ctx.outputs.get(table, []) for table in manifest.writes}
                ctx.outputs, ctx.observed_count, ctx.yielded_count = {}, sum(ctx.exclusions.values()), 0
                if ctx.exclusions:
                    await flush()
                if any(outputs.values()):
                    # A parsed input shows the surface works: the misses before it were their own.
                    failures["parkable"] += failures["streak"]
                    failures["streak"], failures["parsed"] = [], True
                for table, records in outputs.items():
                    keys = [output_identity(r, manifest.table_key(table)) for r in records]
                    if len(set(keys)) != len(keys) or any(r.get(k) is None for r in records for k in manifest.table_key(table)):
                        raise ServiceError("invalid_output_key", "Gold output keys must be present and unique per input and table")
                pending.append((identity, outputs, True))
            else:
                pending.append((identity, outputs, False))
            if len(pending) >= chunk:
                await land()
        return True

    async with await psycopg.AsyncConnection.connect(rt.settings.control_url) as lock:
        await lock.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("enrich:" + manifest.source_key,))
        await asyncio.to_thread(rt.drain, run["warehouse_id"], run["id"])
        files, dump_id, ctx.input_types = await asyncio.to_thread(snapshot_inputs, rt, run, manifest, relation)
        if any("relation" not in part for part in files):
            # A whole-relation snapshot written before paged parts: one object keyed by relation.
            legacy = await asyncio.to_thread(load_part, rt, files[0])
            files = [{"relation": name, "rows": rows} for name, rows in legacy.items()]
        ctx.relations = {name: [] for name in manifest.reads}
        for part in files:
            if part["relation"] != relation:
                ctx.relations[part["relation"]] += await asyncio.to_thread(load_part, rt, part)
        computed = await asyncio.to_thread(retained, rt, run)
        handled = set()
        failures = {"run": 0, "consecutive": 0, "class": None, "rejected": 0, "parkable": [],
                    "streak": [], "parsed": False, "drift": False,
                    "seen": await asyncio.to_thread(rejected_before, rt, run)}
        # The declared time budget starts after the paged read.
        deadline = time.monotonic() + manifest.time_budget_s if manifest.time_budget_s else None
        ctx.deadline = deadline
        admitting = True
        parts = [part for part in files if part["relation"] == relation]
        for number, part in enumerate(parts):
            if deadline is not None and time.monotonic() >= deadline:
                admitting = False
                break
            if "key" in part:
                await asyncio.to_thread(take_up, rt, dump_id, part)
            # One part in memory at a time; a retry re-checks it, since an earlier attempt may
            # have completed some of its inputs.
            rows = ctx.relations[relation] = await asyncio.to_thread(load_part, rt, part)
            identities = [ctx.input_identity(row) for row in rows]
            visible = await asyncio.to_thread(visible_outputs, rt, run, manifest, identities)
            try:
                admitting = await process_part(rows, identities, parts[number + 1:])
            except CircuitOpen:
                await land()
                break
            await land()
            if not admitting:
                break
        await land()
        if not failures["drift"] and failures["parsed"]:
            # Misses after the run's last parsed input, fewer than a streak: the inputs' own.
            failures["parkable"] += failures["streak"]
            failures["streak"] = []
        if failures["drift"]:
            await asyncio.to_thread(end_with_rejects, rt, run, "surface_drift", failures["rejected"],
                                    failures["parkable"], failures["streak"])
        elif failures["run"]:
            await asyncio.to_thread(end_with_rejects, rt, run, failures["class"], failures["run"],
                                    failures["parkable"], failures["streak"])
        elif failures["rejected"]:
            await asyncio.to_thread(end_with_rejects, rt, run, "input_rejected", failures["rejected"],
                                    failures["parkable"], failures["streak"])
        if not admitting and not failures["drift"]:
            # Terminal for the work key: a Retry, Replay or restore returns these receipts, and the
            # remaining inputs wait for the next cycle's run.
            await asyncio.to_thread(end_by_budget, rt, run, manifest, budget_reason)
        await flush(True)
    from mdp_functions.costsync import mirror_costs
    await asyncio.to_thread(mirror_costs, rt)
