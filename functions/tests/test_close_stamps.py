"""D6 close stamps: scope_close serializes closes, stamps come from control load state, and the
mirror catch-up puts a scope's stamps and closed raw.cycles rows through close_no in the warehouse
before a close returns or a tenant reads them."""

import asyncio
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import duckdb
import psycopg
import pytest
from conftest import bound
from mdp_functions import cycles as cycles_module
from mdp_functions.cycles import CYCLE_COLUMNS
from mdp_functions.errors import ServiceError
from mdp_functions.recovery import Recovery
from mdp_functions.settings import REPO
from mdp_functions.warehouse.base import mirror
from mdp_functions.warehouse.duckdb import DuckDBWarehouse
from mdp_functions.warehouse.postgres import PostgresWarehouse, manifest_sql
from psycopg.conninfo import conninfo_to_dict

HISTORY = 200_000
NEW = 20
TABLE = "raw.scale_probe"


def committed(databases, run, count, status="loaded", scope=None):
    """Output dumps whose control load is `loaded`, as Appendix C leaves them after the warehouse commit."""
    ids = [str(uuid4()) for _ in range(count)]
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,uri_prefix,scope) "
            "SELECT unnest(%s::uuid[]),'output',%s,%s,%s,'fixture',%s",
            (ids, run["id"], run["streamline_id"], run["cycle_id"], scope),
        )
        conn.execute(
            "INSERT INTO control.load(dump_id,warehouse_id,target_table,status) SELECT unnest(%s::uuid[]),%s,%s,%s",
            (ids, run["warehouse_id"], TABLE, status),
        )
    return ids


def history(databases, run, count):
    """Stamped history in control and its mirrored stamps, sharing generated ids."""
    with psycopg.connect(databases["admin_control"]) as conn:
        close_no = conn.execute(
            "UPDATE control.scope_close SET last_close_no=last_close_no+1,mirrored_close_no=last_close_no+1 "
            "WHERE scope='global' RETURNING last_close_no"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,uri_prefix,scope,close_no) "
            "SELECT md5('history'||n)::uuid,'output',%s,%s,%s,'history','global',%s FROM generate_series(1,%s) n",
            (run["id"], run["streamline_id"], run["cycle_id"], close_no, count),
        )
        conn.execute("ANALYZE control.dump")
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(
            "INSERT INTO raw.dump_stamps SELECT md5('history'||n)::uuid,'global',%s,'fixture_accounts',%s FROM generate_series(1,%s) n",
            (close_no, TABLE, count),
        )
        conn.execute("ANALYZE raw.dump_stamps")


def manifest(rt, cycle_id):
    return {str(r["dump_id"]) for r in rt.db.all("SELECT dump_id FROM control.cycle_manifest(%s)", (cycle_id,))}


def mirrored(databases, cycle_id, table=TABLE, global_tables=()):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return {str(r[0]) for r in conn.execute(manifest_sql("%(cycle)s", table, tuple(global_tables)), {"cycle": cycle_id})}


def warehouse_cycle(databases, cycle_id):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return conn.execute("SELECT status,close_no FROM raw.cycles WHERE id=%s", (cycle_id,)).fetchone()


def mirrored_through(rt, databases, scope="global"):
    """mirrored_close_no, after checking that nothing at or below it is missing from the warehouse:
    every stamp, and every stamp-mode cycle's closed raw.cycles row with its close_no."""
    through = rt.db.one("SELECT mirrored_close_no FROM control.scope_close WHERE scope=%s", (scope,))["mirrored_close_no"]
    stamped = {str(r["id"]) for r in rt.db.all("SELECT id FROM control.dump WHERE scope=%s AND close_no<=%s", (scope, through))}
    closed = {
        (str(r["id"]), r["close_no"]) for r in rt.db.all(
            "SELECT id,close_no FROM control.cycle WHERE scope=%s AND close_no<=%s AND manifest_mode='stamp'", (scope, through)
        )
    }
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        stamps = {str(r[0]) for r in conn.execute("SELECT dump_id FROM raw.dump_stamps WHERE scope=%s", (scope,))}
        rows = {
            (str(r[0]), r[1]) for r in conn.execute("SELECT id,close_no FROM raw.cycles WHERE scope=%s AND status='closed'", (scope,))
        }
    assert stamped <= stamps and closed <= rows, "mirrored_close_no passed a close that is not in the warehouse"
    return through


@contextmanager
def refused(databases, table, condition="true"):
    """A warehouse trigger that fails inserts into `table` matching `condition`, which rolls back the
    whole mirror transaction carrying them."""
    name = "refuse_" + uuid4().hex[:8]
    with psycopg.connect(databases["admin_warehouse"], autocommit=True) as conn:
        conn.execute(
            f"CREATE FUNCTION raw.{name}() RETURNS trigger LANGUAGE plpgsql AS "
            f"$$ BEGIN IF {condition} THEN RAISE EXCEPTION 'warehouse down'; END IF; RETURN NEW; END $$"
        )
        conn.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION raw.{name}()")
    try:
        yield
    finally:
        with psycopg.connect(databases["admin_warehouse"], autocommit=True) as conn:
            conn.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
            conn.execute(f"DROP FUNCTION IF EXISTS raw.{name}()")


async def bind(rt, cadence="hourly", scope="global", job=None, reason="scheduled", cycle_id=None):
    return await rt.cycles.bind_cycle(
        cadence, scope, f"{cadence}:{uuid4().hex}", reason, job or f"local:{cadence}", cycle_id, runner="core"
    )


async def close_new(rt, databases, run):
    binding = await bind(rt)
    new = committed(databases, run, NEW)
    started = time.perf_counter()
    closed = rt.cycles.close(binding["cycle_id"])
    elapsed = time.perf_counter() - started
    stamped = rt.db.all("SELECT id FROM control.dump WHERE close_no=%s AND scope='global'", (closed["close_no"],))
    assert sorted(str(r["id"]) for r in stamped) == sorted(new)
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM raw.dump_stamps WHERE close_no=%s", (closed["close_no"],)
        ).fetchone()[0] == NEW
    return closed, elapsed, len(mirrored(databases, binding["cycle_id"]))


async def test_close_and_mirror_stay_flat_over_history(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    await close_new(rt, databases, run)  # the first close creates the warehouse tables and indexes
    _, small, before = await close_new(rt, databases, run)
    history(databases, run, HISTORY)
    started = time.perf_counter()
    rt.cycles.mirror()
    idle_mirror = time.perf_counter() - started
    closed, large, after = await close_new(rt, databases, run)
    # The newest manifest carries every stamped dump through its close, history included.
    assert after == before + HISTORY + NEW
    assert len(manifest(rt, closed["id"])) == after
    # Nothing was copied per cycle: cycle_input holds derived rows only.
    assert rt.db.one("SELECT count(*) AS n FROM control.cycle_input")["n"] == 0
    print(f"CLOSE small={small:.3f}s large={large:.3f}s idle_mirror={idle_mirror:.3f}s history={HISTORY}")
    assert large < 3 * small + 0.5
    assert idle_mirror < 0.5


async def test_late_commit_joins_the_next_close_and_replays_are_stable(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    first = await bind(rt)
    early = committed(databases, run, 2)
    # Registered before the close, but its load is still pending: not committed yet.
    [late] = committed(databases, run, 1, status="pending")
    rt.cycles.close(first["cycle_id"])
    before = manifest(rt, first["cycle_id"])
    assert set(early) <= before and late not in before
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.load SET status='loaded' WHERE dump_id=%s", (late,))
    second = await bind(rt)
    rt.cycles.close(second["cycle_id"])
    after = manifest(rt, second["cycle_id"])
    assert late in after and before < after
    # A replay of the first cycle reads the same set, in control, the warehouse, and the dbt macro.
    replay = await bind(rt, reason="other", cycle_id=str(first["cycle_id"]))
    assert replay["cycle_id"] == first["cycle_id"]
    assert rt.cycles.close(first["cycle_id"])["close_no"] < rt.db.one(
        "SELECT close_no FROM control.cycle WHERE id=%s", (second["cycle_id"],)
    )["close_no"]
    assert manifest(rt, first["cycle_id"]) == before == mirrored(databases, first["cycle_id"])
    cycle = rt.db.one("SELECT * FROM control.cycle WHERE id=%s", (first["cycle_id"],))
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        staged = {str(r[0]) for r in conn.execute(dbt_manifest_sql(cycle, TABLE))}
    assert staged == before


def dbt_manifest_sql(cycle, table):
    """Render the dbt macro with the dbt project's Jinja; `return` captures the macro's value."""
    context = {
        "cycle_id": str(cycle["id"]), "cadence": cycle["cadence"], "scope": cycle["scope"],
        "manifest_mode": cycle["manifest_mode"], "close_no": cycle["close_no"],
        "global_close_no": cycle["global_close_no"],
    }
    render = f"""
from pathlib import Path
from jinja2 import Environment
captured = []
environment = Environment(extensions=['jinja2.ext.do'])
environment.globals['return'] = lambda value: captured.append(value) or ''
macros = Path('dbt/macros/mdp_context.sql').read_text() + Path('dbt/macros/mdp_global_inputs.sql').read_text()
environment.from_string(macros).module.mdp_manifest_sql({context!r}, {table!r})
print(captured[-1])
"""
    return subprocess.run(
        ["uv", "run", "--project", "dbt", "python", "-c", render], capture_output=True, text=True, check=True
    ).stdout


async def test_hourly_and_daily_closes_serialize_in_one_scope(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    hourly, daily = await bind(rt, "hourly"), await bind(rt, "daily")
    [held] = committed(databases, run, 1)
    # Hold a row the hourly stamping UPDATE needs, so it waits while holding the scope_close lock.
    blocker = psycopg.connect(databases["admin_control"])
    blocker.execute("SELECT 1 FROM control.dump WHERE id=%s FOR UPDATE", (held,))
    results = {}

    def close(name, cycle):
        results[name] = rt.cycles.close(cycle["cycle_id"])

    first = threading.Thread(target=close, args=("hourly", hourly))
    first.start()
    await wait_for_lock(databases)
    second = threading.Thread(target=close, args=("daily", daily))
    second.start()
    await asyncio.sleep(0.3)
    assert "daily" not in results  # the daily close waits on the scope_close row
    [landed] = committed(databases, run, 1)  # commits while both closes wait
    blocker.rollback()
    blocker.close()
    first.join(10)
    second.join(10)
    assert results["daily"]["close_no"] == results["hourly"]["close_no"] + 1
    hourly_manifest = manifest(rt, hourly["cycle_id"])
    assert held in hourly_manifest and landed not in hourly_manifest
    assert {held, landed} <= manifest(rt, daily["cycle_id"])
    # The earlier closed manifest never grows, in control or in the warehouse.
    later = await bind(rt, "hourly")
    committed(databases, run, 3)
    rt.cycles.close(later["cycle_id"])
    assert manifest(rt, hourly["cycle_id"]) == hourly_manifest == mirrored(databases, hourly["cycle_id"])


async def wait_for_lock(databases, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(databases["admin_control"]) as conn:
            if conn.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query ILIKE %s",
                ("%UPDATE control.dump d SET close_no%",),
            ).fetchone()[0]:
                return
        await asyncio.sleep(0.05)
    raise AssertionError("the hourly close never waited on the held dump row")


@pytest.mark.parametrize("completion", ["retried_close", "next_close", "recovery"])
async def test_a_failed_mirror_is_completed_without_passing_its_close(rt, databases, completion):
    _, run = await bound(rt, "fixture_accounts")
    daily, hourly = await bind(rt, "daily"), await bind(rt, "hourly")
    new = committed(databases, run, 3)
    # The catch-up writes raw.cycles first; refusing the stamps rolls the closed row back with them.
    with refused(databases, "raw.dump_stamps"), pytest.raises(ServiceError) as caught:
        rt.cycles.close(daily["cycle_id"])
    assert caught.value.error_class == "warehouse_unavailable"
    k = rt.db.one("SELECT close_no FROM control.cycle WHERE id=%s", (daily["cycle_id"],))["close_no"]
    assert mirrored_through(rt, databases) < k
    assert warehouse_cycle(databases, daily["cycle_id"])[0] == "open"
    if completion == "retried_close":
        assert rt.cycles.close(daily["cycle_id"])["close_no"] == k
    elif completion == "next_close":
        assert rt.cycles.close(hourly["cycle_id"])["close_no"] == k + 1
    else:
        await Recovery(rt).once(resume=False)
    assert mirrored_through(rt, databases) == (k + 1 if completion == "next_close" else k)
    assert warehouse_cycle(databases, daily["cycle_id"]) == ("closed", k)
    assert set(new) <= mirrored(databases, daily["cycle_id"])


async def test_closes_whose_mirrors_finish_out_of_order_never_pass_the_earlier_close(rt, databases, monkeypatch):
    _, run = await bound(rt, "fixture_accounts")
    daily, hourly = await bind(rt, "daily"), await bind(rt, "hourly")
    early = committed(databases, run, 2)
    original, paused, release = cycles_module.catch_up, threading.Event(), threading.Event()

    def delayed(db, warehouse, scope=None):
        if threading.current_thread().name == "daily-close":
            paused.set()
            assert release.wait(10)
        original(db, warehouse, scope)

    monkeypatch.setattr(cycles_module, "catch_up", delayed)
    results = {}
    daily_close = threading.Thread(
        target=lambda: results.update(daily=rt.cycles.close(daily["cycle_id"])), name="daily-close"
    )
    daily_close.start()
    try:
        # Daily close k committed in control; its catch-up has not run.
        assert await asyncio.to_thread(paused.wait, 10)
        k = rt.db.one("SELECT close_no FROM control.cycle WHERE id=%s", (daily["cycle_id"],))["close_no"]
        assert warehouse_cycle(databases, daily["cycle_id"])[0] == "open"
        late = committed(databases, run, 1)
        # Hourly close k+1 mirrors first, and its catch-up carries k's stamps and closed row too.
        assert rt.cycles.close(hourly["cycle_id"])["close_no"] == k + 1
        assert mirrored_through(rt, databases) == k + 1
        assert warehouse_cycle(databases, daily["cycle_id"]) == ("closed", k)
        assert set(early) <= mirrored(databases, daily["cycle_id"]) and late[0] not in mirrored(databases, daily["cycle_id"])
    finally:
        release.set()
        daily_close.join(10)
    # The delayed close finds nothing left to mirror and returns terminal.
    assert results["daily"]["close_no"] == k
    assert mirrored_through(rt, databases) == k + 1


async def test_a_late_state_change_never_reopens_a_closed_row(rt, databases, tmp_path):
    cycle = await bind(rt)
    stale = rt.db.one(f"SELECT {CYCLE_COLUMNS} FROM control.cycle WHERE id=%s", (cycle["cycle_id"],))
    closed = rt.cycles.close(cycle["cycle_id"])
    mirror(rt.warehouse, {"cycles": [stale]})
    assert warehouse_cycle(databases, cycle["cycle_id"]) == ("closed", closed["close_no"])
    local = DuckDBWarehouse(str(tmp_path / "warehouse.duckdb"))
    mirror(local, {"cycles": [rt.db.one(f"SELECT {CYCLE_COLUMNS} FROM control.cycle WHERE id=%s", (cycle["cycle_id"],))]})
    mirror(local, {"cycles": [stale]})
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as conn:
        assert conn.execute("SELECT status FROM raw.cycles WHERE id=?", [str(cycle["cycle_id"])]).fetchone() == ("closed",)


async def test_a_migrate_destination_load_never_holds_back_a_stamp(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    cycle = await bind(rt)
    [ready] = committed(databases, run, 1)
    [unready] = committed(databases, run, 1, status="pending")
    with psycopg.connect(databases["admin_control"]) as conn:
        destination = conn.execute(
            "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref) VALUES ('postgres','migrate_probe','X') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO control.load(dump_id,warehouse_id,target_table,status) VALUES (%s,%s,%s,'pending'),(%s,%s,%s,'loaded')",
            (ready, destination, TABLE, unready, destination, TABLE),
        )
    try:
        rt.cycles.close(cycle["cycle_id"])
        # Only the load to the run's pinned warehouse decides the stamp.
        read = manifest(rt, cycle["cycle_id"])
        assert ready in read and unready not in read
        assert mirrored(databases, cycle["cycle_id"]) == read
    finally:
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("DELETE FROM control.load WHERE warehouse_id=%s", (destination,))
            conn.execute("DELETE FROM control.warehouse WHERE id=%s", (destination,))


def compile_model(databases, tmp_path, model, dbt_run_id):
    """`dbt compile` of one model against the test warehouse on a non-local target, bound to a run."""
    info = conninfo_to_dict(databases["admin_warehouse"])
    (tmp_path / "profiles.yml").write_text(
        "music_data_platform:\n  target: pg_local\n  outputs:\n    pg_local:\n      type: postgres\n"
        f"      host: {info.get('host', '127.0.0.1')}\n      port: {info.get('port', 5432)}\n"
        f"      user: {info['user']}\n      pass: {info['password']}\n      dbname: {info['dbname']}\n"
        "      schema: dbt\n      threads: 1\n      sslmode: prefer\n"
    )
    env = os.environ | {
        "DBT_CLOUD_RUN_ID": dbt_run_id, "DBT_MDP_CADENCE": "daily", "DBT_MDP_SCOPE": "global", "NO_COLOR": "1",
    }
    result = subprocess.run(
        ["uv", "run", "--project", str(REPO / "dbt"), "dbt", "compile", "--project-dir", str(REPO / "dbt"),
         "--profiles-dir", str(tmp_path), "--target-path", str(tmp_path / "target"), "--log-path", str(tmp_path / "logs"),
         "--target", "pg_local", "--select", model],
        env=env, capture_output=True, text=True, check=False,
    )
    compiled = next((tmp_path / "target" / "compiled").rglob(model + ".sql"), None) if result.returncode == 0 else None
    return result.returncode, result.stdout + result.stderr, compiled.read_text() if compiled else ""


async def test_phase_three_of_a_cycle_without_close_no_fails_loudly(rt, databases, tmp_path):
    binding = await bind(rt, cadence="daily")
    # Phase 1 runs before close and never reads the manifest.
    code, output, _ = compile_model(databases, tmp_path, "bronze_invoke__sp_playlist", binding["dbt_run_id"])
    assert code == 0, output
    # A phase-3 model of the same open cycle fails instead of compiling a filter that admits nothing.
    code, output, _ = compile_model(databases, tmp_path, "stg_playlist__snapshots", binding["dbt_run_id"])
    assert code != 0 and "cycle_not_closed" in output
    closed = rt.cycles.close(binding["cycle_id"])
    code, output, sql = compile_model(databases, tmp_path, "stg_playlist__snapshots", binding["dbt_run_id"])
    assert code == 0, output
    assert f"close_no <= {closed['close_no']}" in sql


async def test_tenant_reads_only_mirrored_global_closes(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    job, scope = "tenant-job:" + uuid4().hex, "tenant:" + str(uuid4())
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,global_inputs) VALUES (%s,'core','hourly',%s,%s)",
            (job, scope, [TABLE]),
        )
    first = await bind(rt)
    visible = committed(databases, run, 2)
    rt.cycles.close(first["cycle_id"])
    # Global close N commits in control, but the warehouse refuses its closed raw.cycles row, and the
    # same transaction takes its stamps back with it.
    second = await bind(rt)
    unmirrored = committed(databases, run, 2)
    with refused(databases, "raw.cycles", "NEW.status='closed' AND NEW.scope='global'"):
        with pytest.raises(ServiceError):
            rt.cycles.close(second["cycle_id"])
        assert warehouse_cycle(databases, second["cycle_id"])[0] == "open"
        tenant = await bind(rt, scope=scope, job=job)
        closed = rt.cycles.close(tenant["cycle_id"])
    first_close = rt.db.one("SELECT close_no FROM control.cycle WHERE id=%s", (first["cycle_id"],))["close_no"]
    # global_close_no is the mirrored close N-1, never the committed-but-unmirrored N.
    assert closed["global_close_no"] == first_close == mirrored_through(rt, databases)
    read = manifest(rt, tenant["cycle_id"])
    assert set(visible) <= read and not set(unmirrored) & read
    assert mirrored(databases, tenant["cycle_id"], TABLE, (TABLE,)) == read
    # Once the catch-up mirrors the global close, the next tenant cycle reads it. The same table
    # also carries the tenant's own dumps, and the filter selects both scopes by the stamp's scope.
    rt.cycles.mirror()
    second_close = rt.db.one("SELECT close_no FROM control.cycle WHERE id=%s", (second["cycle_id"],))["close_no"]
    assert warehouse_cycle(databases, second["cycle_id"]) == ("closed", second_close)
    own = committed(databases, run, 2, scope=scope)
    following = await bind(rt, scope=scope, job=job)
    assert rt.cycles.close(following["cycle_id"])["global_close_no"] == second_close
    read = manifest(rt, following["cycle_id"])
    assert set(unmirrored) | set(own) | set(visible) <= read
    assert mirrored(databases, following["cycle_id"], TABLE, (TABLE,)) == read
    # Without the declaration the tenant filter admits only its own scope.
    assert mirrored(databases, following["cycle_id"], TABLE) == set(own)


async def test_a_rejected_dump_leaves_the_stamp_candidates_until_a_repair_lands_it(rt, databases, monkeypatch):
    from test_landing import pending

    load = await pending(rt)
    cycle = rt.db.one("SELECT cycle_id FROM control.run WHERE id=%s", (load["run_id"],))
    original = PostgresWarehouse.land

    def corrupt(self, manifest, claim, parts, hook=None):
        raise ValueError("corrupt part")  # a permanent data error dead-letters the dump

    monkeypatch.setattr(PostgresWarehouse, "land", corrupt)
    rt.landing(load["warehouse_id"]).process(load["id"])
    dump = rt.db.one("SELECT rejected_at,close_no FROM control.dump WHERE id=%s", (load["dump_id"],))
    assert dump["rejected_at"] is not None
    # The partial index the close reads skips it, and the close leaves it unstamped.
    index = rt.db.one("SELECT indexdef FROM pg_indexes WHERE indexname='dump_unstamped_idx'")["indexdef"]
    assert "quarantined_at IS NULL" in index and "rejected_at IS NULL" in index
    rt.cycles.close(cycle["cycle_id"])
    assert rt.db.one("SELECT close_no FROM control.dump WHERE id=%s", (load["dump_id"],))["close_no"] is None
    # A repair that lands clears the mark, and the next close stamps the dump.
    monkeypatch.setattr(PostgresWarehouse, "land", original)
    rt.landing(load["warehouse_id"]).repair(load["dump_id"], load["warehouse_id"], load["target_table"])
    rt.landing(load["warehouse_id"]).process(load["id"])
    assert rt.db.one("SELECT rejected_at FROM control.dump WHERE id=%s", (load["dump_id"],))["rejected_at"] is None
    later = await bind(rt, "weekly")
    closed = rt.cycles.close(later["cycle_id"])
    assert rt.db.one("SELECT close_no FROM control.dump WHERE id=%s", (load["dump_id"],))["close_no"] == closed["close_no"]


async def test_derived_rows_are_mirrored_before_a_run_is_terminal(rt, databases, monkeypatch):
    from test_landing import pending

    load = await pending(rt)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.streamline SET layer='silver' WHERE source_key='billboard_hot100'")
    original = PostgresWarehouse.mirror

    def unavailable(self, tables):
        if tables.get("cycle_inputs"):
            raise psycopg.OperationalError("warehouse down")
        return original(self, tables)

    monkeypatch.setattr(PostgresWarehouse, "mirror", unavailable)
    with pytest.raises(ServiceError):
        rt.landing(load["warehouse_id"]).process(load["id"])
    rt.db.execute("UPDATE control.batch SET status='succeeded' WHERE run_id=%s", (load["run_id"],))
    rt.settle(load["run_id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (load["run_id"],))["status"] in {"succeeded", "partial"}
    # The derived row committed in control but is not in raw.cycle_inputs: the poll keeps waiting.
    assert rt.receipts(load["run_id"])["run"]["status"] == "running"
    monkeypatch.setattr(PostgresWarehouse, "mirror", original)
    assert rt.receipts(load["run_id"])["run"]["status"] != "running"
    assert rt.db.one("SELECT count(*) AS n FROM control.cycle_input WHERE mirrored_at IS NULL")["n"] == 0
