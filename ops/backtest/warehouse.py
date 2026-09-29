"""Bounded production reads and isolated local dbt builds."""

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import yaml
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from ops.backtest.errors import BacktestError

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "mart_song_day",
    "mart_song_cluster_members",
    "mart_top_movers",
    "mart_early_signals_current",
    "mart_arrivals_current",
    "mart_readiness",
    "mart_song_aliases",
    "int_song_entries__daily",
    "int_song_shazam__daily",
    "int_song_followers__daily",
)
MIRRORS = {
    "raw.cycles",
    "raw.cycle_attempts",
    "raw.cycle_inputs",
    "raw.dump_stamps",
    "raw.targets",
}
# mdp_reference_guard reads this only when execute=true, so dbt parse omits its source edge.
RUNTIME_INPUTS = {"raw.mb_generation"}
COMPONENTS = [
    "playlist_adds",
    "follower_exposure_gain",
    "shazam_spread_gain",
    "stream_rate_gain",
    "chart_spread_gain",
]


def command(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, check=False, **kwargs)
    if result.returncode:
        raise RuntimeError("Command failed. Open ops/backtest/README.md#recover.")
    return result.stdout


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, default=str, indent=2) + "\n")
    temporary.replace(path)


# Runner bindings are mirrored to raw.cycle_attempts at admission. Read only these
# declared mirrors as service_read; control tables and Fly APIs are not read paths.
RUNNER_STARTS_SQL = """
    SELECT DISTINCT ON (c.cadence,c.scope) c.cadence,c.scope,a.bound_at AS started_at,
        c.closed_at
    FROM raw.cycle_attempts a JOIN raw.cycles c ON c.id=a.cycle_id
    WHERE a.reason_category='scheduled'
      AND a.dbt_run_id NOT LIKE 'manual:%%'
      AND a.dbt_run_id NOT LIKE 'backfill:%%'
      AND a.dbt_run_id NOT LIKE 'canary:%%'
      AND a.bound_at > now()-interval '8 days'
    ORDER BY c.cadence,c.scope,a.bound_at DESC
"""
PERIODS = {"hourly": 3600, "daily": 86400, "weekly": 604800}


def runner_starts(conn):
    try:
        return conn.execute(RUNNER_STARTS_SQL).fetchall()
    except psycopg.Error:
        raise BacktestError("backtest_runner_unknown") from None


def quiet_seconds(now, starts):
    """Conservative recurring windows from the latest start of each runner."""
    now = now.astimezone(UTC)
    global_cadences = {r["cadence"] for r in starts if r["scope"] == "global"}
    if not set(PERIODS) <= global_cadences:
        raise BacktestError("backtest_runner_unknown")
    remaining = []
    for row in starts:
        period = PERIODS.get(row["cadence"])
        start = row["started_at"]
        if period is None or start > now or (now - start).total_seconds() > period * 2:
            raise BacktestError("backtest_runner_unknown")
        # Close precedes transforms. Keep a buffer after it, with at least ten
        # minutes for hourly work and thirty for daily/weekly work.
        closed = row["closed_at"]
        duration = max(
            600 if row["cadence"] == "hourly" else 1800,
            (closed - start).total_seconds() + 600 if closed else period,
        )
        elapsed = (now - start).total_seconds()
        offset = elapsed % period
        if offset < duration or offset >= period - 120:
            return 0
        remaining.append(period - 120 - offset)
    return min(remaining)


def await_quiet(conn, timeout=3600):
    deadline = time.monotonic() + timeout
    while True:
        available = quiet_seconds(datetime.now(UTC), runner_starts(conn))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BacktestError("backtest_runner_wait_expired")
        if available >= 30:
            return
        print(
            "Capture waits for the runner window to end. Next: leave this command open.",
            flush=True,
        )
        time.sleep(min(30, remaining))


def live_connection():
    secret = os.environ.get("MDP_BACKTEST_READ_URL")
    if secret is None:
        secret = command(
            [
                "secret_store", "get", "MDP_SERVICE_READ_URL",
            ],
            text=True,
        ).strip()
    opts = conninfo_to_dict(secret)
    if opts.get("user") != "service_read":
        raise ValueError(
            "Use the service_read credential. Open ops/backtest/README.md#capture."
        )
    opts.update(
        host="127.0.0.1",
        hostaddr="127.0.0.1",
        port="15471",
        connect_timeout="5",
        options="-c default_transaction_read_only=on -c statement_timeout=15000 "
        "-c lock_timeout=1000 -c idle_in_transaction_session_timeout=20000 "
        "-c application_name=mdp_backtest_capture",
    )
    conn = psycopg.connect(make_conninfo(**opts), autocommit=True, row_factory=dict_row)
    try:
        await_quiet(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def live_budget(conn, monitor=None):
    remaining = quiet_seconds(datetime.now(UTC), runner_starts(monitor or conn))
    if remaining < 3:
        raise RuntimeError(
            "Runner window starts soon. Run capture again after the window."
        )
    timeout = min(15000, int((remaining - 2) * 1000))
    conn.execute("SELECT set_config('statement_timeout', %s, false)", (str(timeout),))


def start_local(scratch):
    # Docker assigns a free loopback port. This cannot reuse the production proxy.
    command(["docker", "ps", "--format", "{{.Names}} {{.Ports}}"])
    token = uuid4().hex
    name = "replay-backtest-" + token[:10]
    command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "mdp.backtest=" + token,
            "--shm-size=1g",
            "-e",
            "POSTGRES_PASSWORD=backtest",
            "-p",
            "127.0.0.1::5432",
            "postgres:17",
        ]
    )
    info = json.loads(command(["docker", "inspect", name], text=True))[0]
    port = int(info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
    state = {
        "container": name,
        "token": token,
        "port": port,
        "cycles": [],
        "methods": {},
    }
    save(scratch / "state.json", state)
    for _ in range(60):
        try:
            with local_connection(state) as conn:
                conn.execute("CREATE SCHEMA backtest")
                conn.execute("CREATE TABLE backtest.owner (token text PRIMARY KEY)")
                conn.execute("INSERT INTO backtest.owner VALUES (%s)", (token,))
            return state
        except psycopg.OperationalError:
            time.sleep(1)
    raise RuntimeError(
        "Local Postgres did not start. Open ops/backtest/README.md#recover."
    )


def local_connection(state):
    info = json.loads(command(["docker", "inspect", state["container"]], text=True))[0]
    if info["Config"]["Labels"].get("mdp.backtest") != state["token"]:
        raise ValueError(
            "Container ownership differs. Use a fresh MDP_BACKTEST_SCRATCH path."
        )
    bindings = info["NetworkSettings"]["Ports"]["5432/tcp"]
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": str(state["port"])}]:
        raise ValueError("Local port differs. Use a fresh MDP_BACKTEST_SCRATCH path.")
    return psycopg.connect(
        host="127.0.0.1",
        port=state["port"],
        dbname="postgres",
        user="postgres",
        password="backtest",
        autocommit=True,
        row_factory=dict_row,
        options="-c statement_timeout=180000",
    )


def prepare_method(scratch, name, ref, state):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
        raise ValueError(
            "Use a short method name with letters, digits or underscores. Run replay --help."
        )
    sha = command(
        ["git", "rev-parse", "--verify", ref + "^{commit}"], text=True
    ).strip()
    project = scratch / "methods" / sha
    project.mkdir(parents=True, exist_ok=True)
    archive = command(["git", "archive", sha, "dbt"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(project, filter="data")
    # Infrastructure routing only. Every model, identity rule and seed stays at the pinned ref.
    (project / "dbt/macros/generate_schema_name.sql").write_text(
        "{% macro generate_schema_name(custom_schema_name, node) %}"
        "{{ var('backtest_schema') }}{% endmacro %}\n"
    )
    profiles = project / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "music_data_platform": {
                    "target": "pg_local",
                    "outputs": {
                        "pg_local": {
                            "type": "postgres",
                            "host": "127.0.0.1",
                            "port": state["port"],
                            "user": "postgres",
                            "password": "backtest",
                            "dbname": "postgres",
                            "schema": "dbt",
                            "threads": 4,
                            "sslmode": "disable",
                            "autocommit": False,
                        }
                    },
                }
            }
        )
    )
    method = {"name": name, "sha": sha, "schema": "bt_" + name, "path": str(project)}
    dbt(method, "parse", state, None)
    manifest = json.loads((project / "dbt/target/manifest.json").read_text())
    nodes = manifest["nodes"] | manifest["sources"]
    selected = set()

    def visit(key):
        if key in selected:
            return
        node = nodes[key]
        if set(node.get("tags", [])) & {"invoke", "export", "close"}:
            return
        selected.add(key)
        for parent in node.get("depends_on", {}).get("nodes", []):
            visit(parent)

    for target in TARGETS:
        visit("model.music_data_platform." + target)
    method["models"] = sorted(
        nodes[k]["name"] for k in selected if nodes[k]["resource_type"] == "model"
    )
    method["seeds"] = sorted(
        nodes[k]["name"] for k in selected if nodes[k]["resource_type"] == "seed"
    )
    method["raw"] = sorted(
        MIRRORS
        | RUNTIME_INPUTS
        | {
            nodes[k]["schema"] + "." + nodes[k]["identifier"]
            for k in selected
            if nodes[k]["resource_type"] == "source"
        }
    )
    if any(not r.startswith("raw.") for r in method["raw"]):
        raise ValueError(
            "A method reads outside raw. Review its inputs before capture."
        )
    return method


def dbt(method, action, state, cycle, selection=()):
    project = Path(method["path"])
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("MDP_", "DBT_"))},
        "DBT_MDP_SCOPE": "global",
        "DBT_MDP_CADENCE": "daily",
        "DBT_CLOUD_RUN_ID": cycle["opened_by_dbt_run_id"]
        if cycle
        else "backtest:parse",
        "DBT_CLOUD_JOB_ID": "backtest",
        "TZ": "UTC",
    }
    args = [
        "uv",
        "run",
        "--project",
        str(project / "dbt"),
        "dbt",
        action,
        "--project-dir",
        str(project / "dbt"),
        "--profiles-dir",
        str(project / "profiles"),
        "--target",
        "pg_local",
        "--no-partial-parse",
        "--vars",
        json.dumps({"backtest_schema": method["schema"]}),
    ]
    if selection:
        args += ["--select", *selection]
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    log = project / (f"{cycle['id'] if cycle else 'parse'}-{action}.log")
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        missing = re.search(
            r"reference_generation_incomplete:.*?generation ([0-9-]+)", result.stdout
        )
        if missing and action == "run":
            return missing.group(1)
        raise RuntimeError(
            "Replay failed. Read the private log under MDP_BACKTEST_SCRATCH/methods."
        )


def committed_choices():
    """Git history owns selection dates; private state is only a cache."""
    try:
        choices = json.loads(
            command(["git", "show", "HEAD:ops/backtest/choices.json"], text=True)
        )
        if not isinstance(choices, dict) or not choices:
            raise ValueError
        for method in choices.values():
            if not isinstance(method, dict) or not method.get("sha"):
                raise ValueError
            chosen = date.fromisoformat(method["chosen_on"])
            if chosen > datetime.now(UTC).date():
                raise ValueError
        return choices
    except (RuntimeError, ValueError, KeyError, TypeError):
        raise BacktestError("backtest_chosen_on_missing") from None


def validate_choices(collection, choices):
    for name, method in collection.items():
        recorded = choices.get(name)
        if not recorded or not method.get("chosen_on"):
            raise BacktestError("backtest_chosen_on_missing")
        if any(method.get(key) != recorded[key] for key in ("sha", "chosen_on")):
            raise BacktestError("backtest_choice_mismatch")


def method_choices(state):
    choices = committed_choices()
    saved = {}
    for collection in (state.get("capture_methods", {}), state["methods"]):
        validate_choices(collection, choices)
        saved.update(collection)
    return saved


def methods(scratch, specs, state, chosen_on=()):
    saved = method_choices(state)
    choices = committed_choices()
    specs = specs or [
        f"{name}={method['sha']}" for name, method in (saved or choices).items()
    ]
    dates = dict(item.split("=", 1) for item in chosen_on or ())
    result = {}
    for spec in specs:
        name, ref = spec.split("=", 1)
        if name in result:
            raise ValueError("Method names must differ. Run replay --help.")
        recorded = choices.get(name)
        if not recorded:
            raise BacktestError("backtest_chosen_on_missing")
        if name in dates and dates[name] != recorded["chosen_on"]:
            raise BacktestError("backtest_choice_mismatch")
        result[name] = prepare_method(scratch, name, ref, state)
        result[name]["chosen_on"] = recorded["chosen_on"]
    if dates.keys() - result.keys():
        raise BacktestError("backtest_chosen_on_missing")
    validate_choices(result, choices)
    return result


def manifest_predicate(cycle, relation):
    """The global branch of mdp_manifest_sql, with table-specific stamps."""
    derived = sql.SQL("SELECT dump_id FROM raw.cycle_inputs WHERE cycle_id={}").format(
        sql.Literal(cycle["id"])
    )
    if cycle["manifest_mode"] == "list":
        return derived
    if cycle["manifest_mode"] != "stamp" or cycle["close_no"] is None:
        raise ValueError("Cycle is not closed. Capture a closed cycle.")
    return sql.SQL(
        "SELECT dump_id FROM raw.dump_stamps WHERE scope='global' AND close_no<={} "
        "AND target_table={} UNION {}"
    ).format(sql.Literal(cycle["close_no"]), sql.Literal(relation), derived)


def capture_query(cycle, relation, columns):
    name = sql.Identifier(*relation.split("."))
    cid = sql.Literal(cycle["id"])
    visible_cycles = sql.SQL(
        "SELECT id FROM raw.cycles WHERE scope='global' AND status='closed' AND closed_at<={}"
    ).format(sql.Literal(cycle["closed_at"]))
    if relation == "raw.cycles":
        predicate = sql.SQL("id IN ({})").format(visible_cycles)
    elif relation == "raw.cycle_attempts" or relation == "raw.cycle_inputs":
        predicate = sql.SQL("cycle_id={}").format(cid)
    elif relation == "raw.dump_stamps":
        predicate = sql.SQL("scope='global' AND close_no<={}").format(
            sql.Literal(cycle["close_no"] or 0)
        )
    elif relation == "raw.targets":
        predicate = sql.SQL("_cycle_id IN ({})").format(visible_cycles)
    elif "_dump_id" in columns:
        predicate = sql.SQL("_dump_id IN ({})").format(
            manifest_predicate(cycle, relation)
        )
    else:
        raise ValueError(
            "Raw input has no manifest key. Review the method's input declaration."
        )
    return sql.SQL("SELECT * FROM {} WHERE {}").format(name, predicate)


def capture(scratch, specs, output=None, chosen_on=()):
    state_path = scratch / "state.json"
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else start_local(scratch)
    )
    if "container" not in state:
        saved_methods = state["methods"]
        state = start_local(scratch)
        state["methods"] = saved_methods
    configured = methods(scratch, specs, state, chosen_on)
    state["capture_methods"] = configured
    save(state_path, state)
    relations = sorted({r for m in configured.values() for r in m["raw"]})
    with live_connection() as live:
        live_budget(live)
        cycles = live.execute(
            "SELECT id::text,opened_by_dbt_run_id,opened_at,closed_at,close_no,manifest_mode "
            "FROM raw.cycles WHERE cadence='daily' AND scope='global' AND status='closed' "
            "ORDER BY opened_at,id"
        ).fetchall()
    for cycle in cycles:
        cycle = json.loads(json.dumps(cycle, default=str))
        folder = scratch / "captures" / cycle["id"]
        if (folder / "capture.json").exists():
            previous = json.loads((folder / "capture.json").read_text())
            if set(previous["tables"]) >= set(relations):
                continue
        folder.mkdir(parents=True, exist_ok=True)
        counts = {}
        with live_connection() as live, live_connection() as monitor:
            live.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for relation in relations:
                live_budget(live, monitor)
                columns = live.execute(
                    "SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS type "
                    "FROM pg_attribute a WHERE a.attrelid=to_regclass(%s) "
                    "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                    (relation,),
                ).fetchall()
                # A newly declared source can be absent before deployment. Local declaration creates it empty.
                if not columns:
                    counts[relation] = {"columns": [], "rows": 0, "absent": True}
                    continue
                query = capture_query(cycle, relation, [c["attname"] for c in columns])
                live_budget(live, monitor)
                digest = hashlib.sha256()
                path = folder / (relation + ".copy")
                with (
                    path.open("wb") as file,
                    live.cursor().copy(
                        sql.SQL("COPY ({}) TO STDOUT").format(query)
                    ) as source,
                ):
                    for chunk in source:
                        file.write(chunk)
                        digest.update(chunk)
                # COPY text escapes newlines in values, so physical lines count rows exactly.
                with path.open("rb") as file:
                    count = sum(1 for _ in file)
                counts[relation] = {
                    "columns": columns,
                    "rows": count,
                    "sha256": digest.hexdigest(),
                }
            live.execute("COMMIT")
        save(
            folder / "capture.json",
            {
                "cycle": cycle,
                "tables": counts,
                "captured_at": datetime.now(UTC).isoformat(),
            },
        )
        print(
            f"Captured cycle {cycle['id']} ({sum(c['rows'] for c in counts.values())} rows). Next: replay.",
            flush=True,
        )
    state["cycles"] = cycles
    state["capture_methods"] = configured
    state["captured_at"] = datetime.now(UTC).isoformat()
    save(state_path, state)
    if output:
        capture_summary(scratch, state, output)
    print(
        "Capture is ready. Next: uv run --project functions python ops/backtest/run.py replay."
    )


def capture_summary(scratch, state, output):
    with (output / "capture.csv").open("w") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(
            [
                "cycle_id",
                "opened_at",
                "closed_at",
                "close_no",
                "relation",
                "rows",
                "sha256",
                "absent",
                "captured_at",
            ]
        )
        for cycle in state["cycles"]:
            capture = json.loads(
                (scratch / "captures" / cycle["id"] / "capture.json").read_text()
            )
            for relation, table in sorted(capture["tables"].items()):
                writer.writerow(
                    [
                        cycle["id"],
                        cycle["opened_at"],
                        cycle["closed_at"],
                        cycle["close_no"],
                        relation,
                        table["rows"],
                        table.get("sha256", ""),
                        int(table.get("absent", False)),
                        capture["captured_at"],
                    ]
                )


def load_capture(conn, folder, method):
    metadata = json.loads((folder / "capture.json").read_text())
    if not set(method["raw"]) <= set(metadata["tables"]):
        raise ValueError(
            "Method needs uncaptured inputs. Run capture with both --method options."
        )
    conn.execute("DROP SCHEMA IF EXISTS raw CASCADE")
    conn.execute("CREATE SCHEMA raw")
    # Use this ref's generated declarations, including typed empty inputs absent in production.
    bootstrap = (Path(method["path"]) / "dbt/macros/bootstrap_raw.sql").read_text()
    for statement in re.findall(r'run_query\(("(?:[^"\\]|\\.)*")\)', bootstrap):
        conn.execute(json.loads(statement))
    for relation, table in metadata["tables"].items():
        if not table["columns"]:
            continue
        name = sql.Identifier(*relation.split("."))
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(name))
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                name,
                sql.SQL(",").join(
                    sql.SQL("{} {}").format(
                        sql.Identifier(c["attname"]), sql.SQL(c["type"])
                    )
                    for c in table["columns"]
                ),
            )
        )
        path = folder / (relation + ".copy")
        if hashlib.sha256(path.read_bytes()).hexdigest() != table["sha256"]:
            raise ValueError(
                "Capture hash differs. Run capture in a fresh scratch directory."
            )
        with (
            path.open("rb") as file,
            conn.cursor().copy(sql.SQL("COPY {} FROM STDIN").format(name)) as sink,
        ):
            while chunk := file.read(1024 * 1024):
                sink.write(chunk)
        if any(c["attname"] == "_dump_id" for c in table["columns"]):
            conn.execute(sql.SQL("CREATE INDEX ON {} (_dump_id)").format(name))
    conn.execute("CREATE INDEX ON raw.dump_stamps (target_table,scope,close_no)")
    conn.execute("CREATE SCHEMA IF NOT EXISTS mdp")
    conn.execute(
        "CREATE OR REPLACE FUNCTION mdp.bind_cycle(VARIADIC text[]) RETURNS text LANGUAGE sql AS $$ SELECT 'bound' $$"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS mdp.pseudonym_key (key text)")
    conn.execute("TRUNCATE mdp.pseudonym_key")
    conn.execute("INSERT INTO mdp.pseudonym_key VALUES ('backtest-local')")
    conn.execute("ANALYZE")


def export(conn, table, output):
    with output.open("w") as file:
        writer = csv.writer(file, lineterminator="\n")
        rows = conn.execute(
            sql.SQL("SELECT * FROM backtest.{} ORDER BY 1,2,3").format(
                sql.Identifier(table)
            )
        )
        writer.writerow([c.name for c in rows.description])
        for row in rows:
            writer.writerow(
                [
                    json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                    for v in row.values()
                ]
            )


def replay(scratch, specs, output, chosen_on=()):
    state = json.loads((scratch / "state.json").read_text())
    configured = methods(scratch, specs, state, chosen_on)
    from ops.backtest.extract import extract, extract_billboard, initialize

    harness_hash = hashlib.sha256(
        b"".join(
            (Path(__file__).parent / name).read_bytes()
            for name in ("warehouse.py", "extract.py", "observations.sql")
        )
    ).hexdigest()

    with local_connection(state) as conn:
        initialize(conn)
        # A method name cannot silently replace a different revision.
        for name, method in configured.items():
            if (
                name in state["methods"]
                and state["methods"][name]["sha"] != method["sha"]
            ):
                raise ValueError(
                    "Name already pins another ref. Choose a new --method name."
                )
            for cycle in state["cycles"]:
                folder = scratch / "captures" / cycle["id"]
                captured = json.loads((folder / "capture.json").read_text())
                capture_hash = hashlib.sha256(
                    json.dumps(captured["tables"], sort_keys=True).encode()
                ).hexdigest()
                if conn.execute(
                    "SELECT 1 FROM backtest.builds JOIN backtest.inputs USING(cycle_id,method) "
                    "WHERE cycle_id=%s AND method=%s AND capture_hash=%s AND harness_hash=%s",
                    (cycle["id"], name, capture_hash, harness_hash),
                ).fetchone():
                    continue
                with conn.transaction():
                    for table in (
                        "builds",
                        "inputs",
                        "pool",
                        "predictions",
                        "aliases",
                        "artists",
                        "facts",
                        "readiness",
                        "targets",
                        "observations",
                        "billboard_members",
                        "billboard_weeks",
                        "billboard_blockers",
                        "family_audit",
                    ):
                        conn.execute(
                            sql.SQL(
                                "DELETE FROM backtest.{} WHERE cycle_id=%s AND method=%s"
                            ).format(sql.Identifier(table)),
                            (cycle["id"], name),
                        )
                schema = sql.Identifier(method["schema"])
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(schema))
                load_capture(conn, folder, method)
                dbt(method, "seed", state, cycle, method["seeds"])
                missing_reference = dbt(method, "run", state, cycle, method["models"])
                if missing_reference:
                    conn.execute(
                        "INSERT INTO backtest.failures VALUES (%s,%s,'reference_generation_incomplete',%s) "
                        "ON CONFLICT(cycle_id,method) DO UPDATE SET reference_id=excluded.reference_id",
                        (cycle["id"], name, missing_reference),
                    )
                    print(
                        f"Cycle {cycle['id']} needs reference {missing_reference}. Next: ask the owner to restore its retained dumps.",
                        flush=True,
                    )
                    continue
                with conn.transaction():
                    extract(conn, method, cycle)
                    extract_billboard(conn, method, cycle)
                    conn.execute(
                        "INSERT INTO backtest.inputs VALUES (%s,%s,%s,%s)",
                        (cycle["id"], name, capture_hash, harness_hash),
                    )
                    conn.execute(
                        "DELETE FROM backtest.failures WHERE cycle_id=%s AND method=%s",
                        (cycle["id"], name),
                    )
                print(
                    f"Replayed {cycle['id']} with {name}. Next: finish the paired days, then label.",
                    flush=True,
                )
            state["methods"][name] = {
                "sha": method["sha"],
                "schema": method["schema"],
                "chosen_on": method["chosen_on"],
            }
            save(scratch / "state.json", state)
        for table in ("predictions", "builds", "readiness", "failures", "inputs"):
            export(conn, table, output / (table + ".csv"))
    print(
        "Predictions are ready. Next: uv run --project functions python ops/backtest/run.py label."
    )


def cleanup(scratch, state, keep_choices=False):
    # Validate the ownership label again before deletion.
    with local_connection(state):
        pass
    command(["docker", "rm", "-f", "-v", state["container"]])
    for name in ("captures", "methods"):
        shutil.rmtree(scratch / name, ignore_errors=True)
    if keep_choices:
        chosen = method_choices(state)
        save(
            scratch / "state.json",
            {
                "cycles": [],
                "methods": {
                    name: {key: method[key] for key in ("sha", "chosen_on")}
                    for name, method in chosen.items()
                },
            },
        )
    else:
        (scratch / "state.json").unlink()
    print(
        "Private capture and local container are removed. Next: open the evidence report."
    )
