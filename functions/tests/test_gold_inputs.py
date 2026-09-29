"""7.3 and 22.11 step 1b: validity-aware paged gold input reads, config_version from the declared
version, Replays attached to frozen configuration, and input_version components on outputs."""

import tracemalloc
from uuid import uuid4

import psycopg
import pytest
from conftest import bound
from mdp_functions.derived import pending_pages, resolve_config, snapshot_inputs
from mdp_functions.layers import gold
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.schemas import COMPLETION_COLUMNS, GOLD_COLUMNS, LINEAGE
from psycopg.types.json import Jsonb
from test_enrichment_runtime import inputs

INPUTS = 200_000
COMPLETE = 199_000
RELATION = "marts.paged_inputs"


@pytest.fixture
def paged(rt, databases):
    name = "paged_" + uuid4().hex[:8]
    table = "raw." + name
    calls = []

    @gold(source_key=name, reads=[RELATION], writes=[table], external=True)
    async def enrich(ctx, rows):
        calls.append(rows[0]["input_ref"])
        ctx.emit(table, {"value": 1})

    sync(rt.db)
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS marts")
        conn.execute(f"DROP TABLE IF EXISTS {RELATION}")
        conn.execute(
            f"CREATE TABLE {RELATION} AS SELECT md5('ref'||n) AS input_ref, md5('v1') AS input_version, "
            "'fixture_accounts' AS source_key, n FROM generate_series(1,%s) n",
            (INPUTS,),
        )
        conn.execute("GRANT USAGE ON SCHEMA marts TO service_read")
        conn.execute(f"GRANT SELECT ON {RELATION} TO service_read")
    yield name, table, calls
    REGISTRY.pop(name)


def dumps(databases, run, tables):
    """Committed output dumps (pinned-warehouse load `loaded`) owned by a fixture run."""
    ids = {table: str(uuid4()) for table in tables}
    with psycopg.connect(databases["admin_control"]) as conn:
        for table, dump in ids.items():
            conn.execute(
                "INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,uri_prefix) VALUES (%s,'output',%s,%s,%s,'fixture')",
                (dump, run["id"], run["streamline_id"], run["cycle_id"]),
            )
            conn.execute(
                "INSERT INTO control.load(dump_id,warehouse_id,target_table,status) VALUES (%s,%s,%s,'loaded')",
                (dump, run["warehouse_id"], table),
            )
    return ids


async def completed_history(rt, databases, name, table):
    """199,000 inputs with a valid completion: output rows and completion rows in stamped dumps."""
    config_version, resolved = resolve_config(rt, REGISTRY[name])
    _, owner = await bound(rt, "fixture_accounts")
    ids = dumps(databases, owner, [table, "raw._enrichment_completion"])
    rt.warehouse.ensure(table, {**GOLD_COLUMNS, "value": "bigint", **LINEAGE})
    rt.warehouse.ensure("raw._enrichment_completion", {**COMPLETION_COLUMNS, **LINEAGE})
    identity = "_source_key,scope,step,config_version,input_ref,input_version"
    values = "%(name)s,'global',%(step)s,%(config)s,md5('ref'||n),md5('v1')"
    params = {"name": name, "step": resolved["step"], "config": config_version, "count": COMPLETE,
              "output": ids[table], "completion": ids["raw._enrichment_completion"],
              "required": Jsonb({table: {"dumps": {ids[table]: 1}, "keys": ["[]"], "output_key": []}})}
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(
            f"INSERT INTO {table}({identity},value,_dump_id,_landed_seq) "
            f"SELECT {values},1,%(output)s,1 FROM generate_series(1,%(count)s) n", params,
        )
        conn.execute(
            f"INSERT INTO raw._enrichment_completion({identity},required_outputs,_dump_id,_landed_seq) "
            f"SELECT {values},%(required)s,%(completion)s,2 FROM generate_series(1,%(count)s) n", params,
        )
        conn.execute(f"ANALYZE {table}")
        conn.execute("ANALYZE raw._enrichment_completion")
    # The owner's close stamps both dumps; the gold cycle's later close includes them.
    rt.cycles.close(owner["cycle_id"])
    return config_version


def peak(fn):
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


async def test_paged_read_takes_only_the_inputs_without_valid_completion(rt, databases, paged):
    name, table, calls = paged
    config_version = await completed_history(rt, databases, name, table)
    binding = await rt.cycles.bind_cycle("daily", "global", "daily:" + uuid4().hex, "scheduled", "local:daily", runner="core")
    rt.cycles.close(binding["cycle_id"])
    run = rt.admit(name, dbt_run_id=binding["dbt_run_id"])
    assert run["config_version"] == config_version
    # The whole relation, for scale: what one fetchall of it would hold.
    whole = peak(lambda: psycopg.connect(rt.settings.service_read_url).execute(f"SELECT * FROM {RELATION}").fetchall())
    parts = []
    paged_peak = peak(lambda: parts.extend(snapshot_inputs(rt, run, REGISTRY[name], RELATION)[0]))
    # One page and its validity rows at a time, never the relation.
    assert paged_peak < whole / 2, (paged_peak, whole)
    assert sum(f["row_count"] for f in parts) == INPUTS - COMPLETE and len(parts) == 1
    snapshot = rt.db.one(
        "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='input_snapshot'", (run["id"],)
    )["attrs"]
    assert {k: snapshot[k] for k in ("read", "pending", "parts")} == {"read": INPUTS, "pending": INPUTS - COMPLETE, "parts": 1}
    # In the relation's own job a part enters the input dump only when the run takes it up.
    assert rt.db.one("SELECT files FROM control.dump WHERE id=(SELECT input_dump_id FROM control.run WHERE id=%s)", (run["id"],))["files"] == []
    # Inputs share a page by streamline.batch_size, so the remainder lands in a few dumps.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.streamline SET batch_size=500 WHERE source_key=%s", (name,))
    await rt.execute(run["id"])
    # Only the remainder reached the function; completed inputs were neither observed nor rejected.
    assert len(calls) == INPUTS - COMPLETE
    row = rt.db.one("SELECT status,rows_written,rows_rejected FROM control.run WHERE id=%s", (run["id"],))
    assert row == {"status": "succeeded", "rows_written": INPUTS - COMPLETE, "rows_rejected": 0}
    dump = rt.db.one("SELECT files,row_count FROM control.dump WHERE id=(SELECT input_dump_id FROM control.run WHERE id=%s)", (run["id"],))
    assert len(dump["files"]) == 1 and dump["row_count"] == INPUTS - COMPLETE
    print(f"PAGED read={INPUTS} pending={INPUTS - COMPLETE} peak={paged_peak} whole={whole}")


async def test_pages_drop_completed_inputs_before_the_snapshot(rt, databases, paged):
    name, table, _ = paged
    await completed_history(rt, databases, name, table)
    binding = await rt.cycles.bind_cycle("daily", "global", "daily:" + uuid4().hex, "scheduled", "local:daily", runner="core")
    rt.cycles.close(binding["cycle_id"])
    run = rt.admit(name, dbt_run_id=binding["dbt_run_id"])
    run = rt.db.one("SELECT * FROM control.run WHERE id=%s", (run["id"],))
    pages = list(pending_pages(rt, run, REGISTRY[name], RELATION, page=50_000))
    assert [read for _, read in pages] == [50_000] * 4
    assert sum(len(rows) for rows, _ in pages) == INPUTS - COMPLETE
    assert {r["n"] for rows, _ in pages for r in rows} == set(range(COMPLETE + 1, INPUTS + 1))


@pytest.fixture
def external(rt, databases):
    inputs(databases, 2)
    name = "external_" + uuid4().hex[:8]
    table = "raw." + name
    calls = []

    @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=[table], external=True,
          input_version=["followers"])
    async def lookup(ctx, rows):
        calls.append(rows[0]["input_ref"])
        yield {"value": 1, "followers": -1}

    sync(rt.db)
    yield name, table, calls, lookup
    REGISTRY.pop(name)


async def test_a_deploy_keeps_every_completion_valid(rt, databases, external):
    name, table, calls, _ = external
    _, run = await bound(rt, name)
    await rt.execute(run["id"])
    assert len(calls) == 2
    rt.cycles.close(run["cycle_id"])
    # A deploy changes the image and unrelated modules, never the declared version or parameters.
    rt.settings.image_digest = "sha256:" + "f" * 64
    later = await rt.cycles.bind_cycle("daily", "global", "daily:" + uuid4().hex, "scheduled", "local:daily", runner="core")
    rt.cycles.close(later["cycle_id"])
    again = rt.admit(name, dbt_run_id=later["dbt_run_id"])
    assert again["id"] != run["id"] and again["config_version"] == run["config_version"]
    await rt.execute(again["id"])
    assert len(calls) == 2
    # The runtime appends each declared input_version component beside input_version.
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        followers = sorted(r[0] for r in conn.execute(f"SELECT followers FROM {table}"))
    assert followers == [1000, 1001]


async def test_a_replay_after_a_code_change_attaches_to_the_frozen_run(rt, databases, external):
    name, table, calls, _ = external
    _, run = await bound(rt, name)
    await rt.execute(run["id"])
    rt.cycles.close(run["cycle_id"])
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        before = conn.execute(f"SELECT input_ref,value,config_version FROM {table} ORDER BY 1").fetchall()

    async def changed(ctx, rows):
        calls.append(rows[0]["input_ref"])
        yield {"value": 2}

    REGISTRY[name].function, REGISTRY[name].version = changed, "2"
    replay = await rt.cycles.bind_cycle(
        "daily", "global", "replay:" + uuid4().hex, "other", "local:daily", str(run["cycle_id"]), runner="core"
    )
    attached = rt.admit(name, dbt_run_id=replay["dbt_run_id"])
    assert attached["id"] == run["id"] and attached["config_version"] == run["config_version"]
    await rt.execute(attached["id"])
    assert len(calls) == 2
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute(f"SELECT input_ref,value,config_version FROM {table} ORDER BY 1").fetchall() == before


async def test_a_time_budget_ends_the_run_partial_and_terminal(rt, databases):
    import asyncio

    inputs(databases, 6)
    name = "budget_" + uuid4().hex[:8]
    table = "raw." + name
    calls = []

    @gold(
        source_key=name, reads=["marts.mart_enrichment_fixture"], writes=[table], external=True,
        time_budget_s=2.0, knobs={"allow_partial": True},
    )
    async def slow(ctx, rows):
        calls.append(rows[0]["input_ref"])
        # Six inputs take 3.6 s against a 2 s budget; the margin keeps a loaded machine taking one.
        await asyncio.sleep(0.6)
        yield {"value": 1}

    sync(rt.db)
    try:
        binding, run = await bound(rt, name)
        await rt.execute(run["id"])
        row = rt.db.one("SELECT status,coverage,error_class FROM control.run WHERE id=%s", (run["id"],))
        assert row == {"status": "partial", "coverage": "partial", "error_class": "time_budget"}
        taken = len(calls)
        assert 1 <= taken < 6
        # A budget end is by design: no partial_coverage alert.
        assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='partial_coverage'", (run["id"],))["n"] == 0
        # Terminal for the work key: a Retry returns the receipts and resumes nothing.
        again = rt.admit(name, dbt_run_id=binding)
        assert again["id"] == run["id"]
        await rt.execute(again["id"])
        assert len(calls) == taken and rt.receipts(run["id"])["run"]["status"] == "partial"
        # The remaining inputs wait for the next cycle's run, which skips the completed ones.
        rt.cycles.close(run["cycle_id"])
        later = await rt.cycles.bind_cycle("daily", "global", "daily:" + uuid4().hex, "scheduled", "local:daily", runner="core")
        rt.cycles.close(later["cycle_id"])
        REGISTRY[name].time_budget_s = 60
        following = rt.admit(name, dbt_run_id=later["dbt_run_id"])
        await rt.execute(following["id"])
        assert len(calls) == 6 and len(set(calls)) == 6
    finally:
        REGISTRY.pop(name)


def test_an_off_job_read_holds_access_share_in_one_repeatable_read(rt, databases, paged):
    from mdp_functions.derived import relation_pages

    pages = relation_pages(rt.settings, RELATION, 50_000, off_job=True)
    first = next(pages)
    try:
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            held = conn.execute(
                "SELECT mode FROM pg_locks WHERE relation=%s::regclass AND granted AND pid<>pg_backend_pid()",
                (RELATION,),
            ).fetchall()
            assert ("AccessShareLock",) in held
            # A table swap waits for the read instead of splitting it.
            conn.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                conn.execute(f"ALTER TABLE {RELATION} RENAME TO paged_inputs_swapped")
        assert len(first) == 50_000 and sum(len(p) for p in pages) == INPUTS - 50_000
    finally:
        pages.close()


async def test_components_take_the_input_column_type(rt, databases):
    inputs(databases, 1)
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("ALTER TABLE marts.mart_enrichment_fixture ADD COLUMN delta bigint")
    name = "typed_" + uuid4().hex[:8]
    table = "raw." + name

    @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=[table], external=True,
          input_version=["delta"])
    async def lookup(ctx, rows):
        yield {"value": 1}

    sync(rt.db)
    try:
        # The first run sees only nulls; the column is still typed like its input.
        _, run = await bound(rt, name)
        await rt.execute(run["id"])
        rt.cycles.close(run["cycle_id"])
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            conn.execute("UPDATE marts.mart_enrichment_fixture SET delta=5, input_version=md5('v2')")
        later = await rt.cycles.bind_cycle("daily", "global", "daily:" + uuid4().hex, "scheduled", "local:daily", runner="core")
        rt.cycles.close(later["cycle_id"])
        again = rt.admit(name, dbt_run_id=later["dbt_run_id"])
        await rt.execute(again["id"])
        assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (again["id"],))["status"] == "succeeded"
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            kind = conn.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_schema='raw' AND table_name=%s AND column_name='delta'",
                (name,),
            ).fetchone()[0]
            values = [r[0] for r in conn.execute(f"SELECT delta FROM {table} ORDER BY delta NULLS FIRST")]
        assert kind == "bigint" and values == [None, 5]
    finally:
        REGISTRY.pop(name)


@pytest.mark.parametrize('has_output', [False, True])
async def test_gold_declared_exclusions_are_not_failed_inputs(rt, databases, has_output):
    inputs(databases, 2)
    name = 'excluded_gold'
    table = 'raw.excluded_gold'

    @gold(source_key=name, reads=['marts.mart_enrichment_fixture'], writes=[table], external=True,
          exclusion_reasons=('outside_scope',))
    async def lookup(ctx, rows):
        ctx.exclude({'position': 1}, reason='outside_scope')
        if has_output:
            ctx.emit(table, {'value': 1})

    try:
        sync(rt.db)
        _, run = await bound(rt, name)
        await rt.execute(run['id'])
        state = rt.receipts(run['id'])
        assert state['run']['status'] == 'succeeded'
        assert state['run']['coverage'] == 'full'
        assert state['run']['rows_rejected'] == 0
        assert sum(int(r['rows_excluded']) for r in state['receipts']) == 2
        assert not rt.db.all("SELECT 1 FROM control.alert WHERE run_id=%s AND class='partial_coverage'", (run['id'],))
    finally:
        REGISTRY.pop(name)
