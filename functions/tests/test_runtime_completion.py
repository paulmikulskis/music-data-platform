"""Step 1b: completion follows committed, manifest-visible output sets."""

from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from conftest import bound
from mdp_functions.derived import resolve_config
from mdp_functions.errors import ServiceError
from mdp_functions.layers import gold, silver, universal
from mdp_functions.registry import REGISTRY, Manifest, register, sync
from mdp_functions.warehouse.postgres import manifest_sql
from test_enrichment_runtime import inputs

SEGMENTS = ("track", "0", "10000")


@pytest.fixture
async def enrichment(rt, databases):
    inputs(databases, 1)
    calls = []
    name = "completion_" + uuid4().hex[:8]
    tables = ["raw." + name + suffix for suffix in ("_features", "_vectors")]

    @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=tables,
          external=True, output_key=["segment"], preprocessing={"sample_rate": 48000})
    async def enrich(ctx, rows):
        calls.append(rows[0]["input_ref"])
        for table in tables:
            for segment in SEGMENTS:
                ctx.emit(table, {"segment": segment, "value": 1})

    sync(rt.db)
    yield name, tables, calls
    REGISTRY.pop(name)


def completions(databases, source_key):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        if not conn.execute("SELECT to_regclass('raw._enrichment_completion')").fetchone()[0]:
            return []
        return [r[0] for r in conn.execute(
            "SELECT required_outputs FROM raw._enrichment_completion WHERE _source_key=%s ORDER BY _landed_seq",
            (source_key,),
        )]


def manifest_rows(databases, table, cycle_id, global_tables=()):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return conn.execute(
            f"SELECT input_version,segment,_dump_id::text FROM {table} WHERE _dump_id IN "
            f"({manifest_sql('%(cycle)s', table, tuple(global_tables))}) ORDER BY 1,2",
            {"cycle": cycle_id},
        ).fetchall()


def fail_attempt(rt, run):
    # The interrupted worker never settled; expire its attempt as the reaper would.
    rt.db.one("UPDATE control.run_attempt SET status='failed' WHERE run_id=%s RETURNING id", (run["id"],))


def interrupt_second_output(rt, monkeypatch):
    """Land the first output's load of a page, then fail before the second one lands."""
    normal_drain = rt.drain
    state = {"failed": False, "outage": True}

    def drain(warehouse_id, run_id):
        loads = rt.db.all(
            "SELECT l.id FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND l.status='pending' ORDER BY l.target_table",
            (run_id,),
        )
        if loads and state["outage"]:
            if not state["failed"]:
                state["failed"] = True
                rt.landing(warehouse_id).process(loads[0]["id"])
            raise ServiceError("warehouse_unavailable", "Injected second output failure")
        normal_drain(warehouse_id, run_id)

    monkeypatch.setattr(rt, "drain", drain)
    return state


async def test_partial_output_retry_resumes_retained_set(rt, databases, enrichment, monkeypatch):
    name, tables, calls = enrichment
    binding, run = await bound(rt, name)
    state = interrupt_second_output(rt, monkeypatch)
    with pytest.raises(ServiceError, match="Injected"):
        await rt.execute(run["id"])
    state["outage"] = False
    fail_attempt(rt, run)
    assert state["failed"] and len(calls) == 1
    assert rt.db.one("SELECT count(*) n FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND l.status='loaded'", (run["id"],))["n"] == 1
    assert completions(databases, name) == []
    original = rt.db.one("SELECT config_version,resolved_config FROM control.run WHERE id=%s", (run["id"],))
    # Configuration is frozen at admission: a changed declaration does not move a retry.
    REGISTRY[name].preprocessing = {"sample_rate": 16000}
    retry = rt.admit(name, dbt_run_id=binding)
    assert retry["id"] == run["id"]
    await rt.execute(retry["id"])
    assert len(calls) == 1
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    assert rt.db.one("SELECT config_version,resolved_config FROM control.run WHERE id=%s", (run["id"],)) == original
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        for table in tables:
            assert conn.execute(f"SELECT count(*),count(distinct _dump_id) FROM {table}").fetchone() == (3, 1)
    [completion] = completions(databases, name)
    assert set(completion) == set(tables)
    assert all(list(v["dumps"].values()) == [3] for v in completion.values())
    assert all(v["output_key"] == ["segment"] for v in completion.values())
    assert rt.db.one("SELECT rows_written FROM control.run WHERE id=%s", (run["id"],))["rows_written"] == 6


async def test_retry_lands_only_the_missing_output(rt, databases, enrichment, monkeypatch):
    name, tables, calls = enrichment
    binding, run = await bound(rt, name)
    state = interrupt_second_output(rt, monkeypatch)
    with pytest.raises(ServiceError, match="Injected"):
        await rt.execute(run["id"])
    state["outage"] = False
    fail_attempt(rt, run)
    # The second output's load is dead-lettered; only the first output committed.
    with psycopg.connect(databases["admin_control"]) as conn:
        first, lost = conn.execute(
            "SELECT l.dump_id::text,l.status FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s ORDER BY l.target_table",
            (run["id"],),
        ).fetchall()
        assert (first[1], lost[1]) == ("loaded", "pending")
        conn.execute("UPDATE control.load SET status='rejected' WHERE dump_id=%s", (lost[0],))
    retry = rt.admit(name, dbt_run_id=binding)
    await rt.execute(retry["id"])
    assert len(calls) == 1
    [completion] = completions(databases, name)
    assert completion[tables[0]]["dumps"] == {first[0]: 3}
    [replacement] = completion[tables[1]]["dumps"]
    assert replacement not in {first[0], lost[0]} and completion[tables[1]]["dumps"][replacement] == 3
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute(f"SELECT count(*),min(_dump_id::text) FROM {tables[0]}").fetchone() == (3, first[0])
        assert conn.execute(f"SELECT count(*),min(_dump_id::text) FROM {tables[1]}").fetchone() == (3, replacement)
    row = rt.db.one("SELECT status,rows_written FROM control.run WHERE id=%s", (run["id"],))
    assert row == {"status": "partial", "rows_written": 6}


async def test_old_cycle_replay_keeps_second_physical_copy(rt, databases, enrichment):
    name, tables, calls = enrichment
    old_id = "old:" + uuid4().hex
    old = await rt.cycles.bind_cycle("daily", "global", old_id, "scheduled", "local:daily", runner="core")
    rt.cycles.close(old["cycle_id"])
    _, later = await bound(rt, name)
    await rt.execute(later["id"])
    rt.cycles.close(later["cycle_id"])
    replay = rt.admit(name, dbt_run_id=old_id)
    await rt.execute(replay["id"])
    # The later cycle's completion names dumps the older manifest cannot see.
    assert len(calls) == 2
    for table in tables:
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            assert conn.execute(f"SELECT count(*),count(distinct _dump_id) FROM {table}").fetchone() == (6, 2)
        assert len(manifest_rows(databases, table, old["cycle_id"])) == 3
        assert len(manifest_rows(databases, table, later["cycle_id"])) == 3
    first, second = completions(databases, name)
    for table in tables:
        assert set(first[table]["dumps"]).isdisjoint(second[table]["dumps"])
    # A new work key in the old cycle reuses its now-complete visible set.
    again = rt.admit(name, dbt_run_id=old_id, manual=True, key=uuid4().hex)
    await rt.execute(again["id"])
    assert len(calls) == 2
    # A visible completion cannot hide a missing required dump.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.cycle_input WHERE cycle_id=%s AND dump_id=(SELECT l.dump_id FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND l.target_table=%s LIMIT 1)", (old["cycle_id"], replay["id"], tables[1]))
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(f"DELETE FROM raw.cycle_inputs WHERE cycle_id=%s AND dump_id IN (SELECT _dump_id FROM {tables[1]} WHERE _run_id=%s)", (old["cycle_id"], replay["id"]))
    incomplete = rt.admit(name, dbt_run_id=old_id, manual=True, key=uuid4().hex)
    await rt.execute(incomplete["id"])
    assert len(calls) == 3
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (incomplete["id"],))["status"] == "succeeded"
    # Only the invisible output is landed again; the visible one is reused.
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute(f"SELECT count(*) FROM {tables[0]} WHERE _run_id=%s", (incomplete["id"],)).fetchone()[0] == 0
        assert conn.execute(f"SELECT count(*) FROM {tables[1]} WHERE _run_id=%s", (incomplete["id"],)).fetchone()[0] == 3


async def test_chunked_inputs_share_pages_and_complete_per_input(rt, databases, enrichment):
    name, tables, calls = enrichment
    inputs(databases, 4)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.streamline SET batch_size=3 WHERE source_key=%s", (name,))
    binding, run = await bound(rt, name)
    await rt.execute(run["id"])
    assert len(calls) == 4
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        for table in tables:
            dumps = conn.execute(f"SELECT count(*) FROM {table} GROUP BY _dump_id ORDER BY 1").fetchall()
            assert dumps == [(3,), (9,)]
    assert len(completions(databases, name)) == 4
    again = rt.admit(name, dbt_run_id=binding, manual=True, key=uuid4().hex)
    await rt.execute(again["id"])
    assert len(calls) == 4


async def test_external_sources_and_row_eligibility(rt, databases, enrichment):
    name, tables, calls = enrichment
    second = name + "_other"
    with psycopg.connect(databases["admin_control"]) as conn:
        for source in (name, second, "fixture_accounts"):
            conn.execute(
                "INSERT INTO control.rights_source(source_key,provider,category,learning_eligible) VALUES (%s,%s,'A',true) "
                "ON CONFLICT(source_key) DO UPDATE SET learning_eligible=true",
                (source, source),
            )
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("ALTER TABLE marts.mart_enrichment_fixture ADD COLUMN grant_learning_eligible boolean DEFAULT false")
    _, run = await bound(rt, name)
    await rt.execute(run["id"])
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        # Registry rows allow learning; the input row's own flag does not.
        assert conn.execute(f"SELECT count(*),bool_or(learning_eligible) FROM {tables[0]}").fetchone() == (3, False)
        conn.execute("UPDATE marts.mart_enrichment_fixture SET grant_learning_eligible=true")
    register(replace(REGISTRY[name], source_key=second))
    try:
        sync(rt.db)
        _, run2 = await bound(rt, second)
        await rt.execute(run2["id"])
        # Another external function on the same input keeps its own completion key.
        assert len(calls) == 2
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            steps = conn.execute("SELECT distinct step FROM raw._enrichment_completion WHERE _source_key=ANY(%s)", ([name, second],)).fetchall()
            assert {s[0] for s in steps} == {"external:" + name, "external:" + second}
            assert conn.execute(f"SELECT bool_and(learning_eligible) FROM {tables[0]} WHERE _source_key=%s", (second,)).fetchone() == (True,)
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("UPDATE control.rights_source SET learning_eligible=false WHERE source_key='fixture_accounts'")
        again = rt.admit(second, manual=True, key=uuid4().hex, scope="global")
        # A rights change annotates new work; completed inputs are not reprocessed.
        await rt.execute(again["id"])
        assert len(calls) == 2
    finally:
        REGISTRY.pop(second)


def test_config_is_the_declared_version_and_parameters(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mdp_functions import derived

    module = tmp_path / "function.py"
    module.write_text("first implementation")
    monkeypatch.setattr(derived.inspect, "getsourcefile", lambda fn: str(module))
    manifest = Manifest("digest", "gold", [], function=lambda: None)
    a, frozen = resolve_config(SimpleNamespace(settings=SimpleNamespace(image_digest="sha256:old")), manifest)
    # Module source and the image digest never enter config_version; a deploy keeps it.
    module.write_text("second implementation")
    b, _ = resolve_config(SimpleNamespace(settings=SimpleNamespace(image_digest="sha256:new")), manifest)
    assert a == b
    manifest.version = "2"
    c, _ = resolve_config(SimpleNamespace(), manifest)
    manifest.preprocessing = {"decoder": "v2", "sample_rate": 48000, "window": 10, "hop": 5}
    d, _ = resolve_config(SimpleNamespace(), manifest)
    manifest.model_steps = [{"id": "dsp_v1", "weights": None, "params": {"hop": 5}}]
    e, bundle = resolve_config(SimpleNamespace(), manifest)
    assert len({a, c, d, e}) == 4
    assert frozen["version"] == "1" and "implementation" not in frozen
    assert frozen["parameters"] == {"llm_step": None, "model_steps": [], "preprocessing": {}}
    assert frozen["step"] == "external:digest" and bundle["step"].startswith("models:")


@pytest.mark.parametrize("decorator", [silver, gold, universal])
def test_raw_reads_refused(decorator):
    with pytest.raises(ServiceError, match="manifest-filtered"):
        decorator(source_key="bad_raw", reads=["raw.anything"], writes=[], external=False)(lambda: None)
    assert "bad_raw" not in REGISTRY




async def test_tenant_replay_reads_frozen_global_inputs(rt, databases, enrichment):
    name, tables, _calls = enrichment
    _, first = await bound(rt, name)
    await rt.execute(first["id"])
    # A global close stamps the first build; tenant cycles read global dumps up to that close.
    rt.cycles.close(first["cycle_id"])
    global_close = rt.db.one("SELECT close_no FROM control.cycle WHERE id=%s", (first["cycle_id"],))["close_no"]
    scope = "tenant:" + str(uuid4())
    job = "tenant-job:" + uuid4().hex
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.dbt_job(job_id,runner,cadence,scope,global_inputs) VALUES (%s,'core','daily',%s,%s)", (job, scope, tables))
    binding = await rt.cycles.bind_cycle("daily", scope, "tenant:" + uuid4().hex, "scheduled", job, runner="core")
    tenant_cycle = binding["cycle_id"]
    rt.cycles.close(tenant_cycle)
    closed = rt.db.one("SELECT global_close_no,global_inputs FROM control.cycle WHERE id=%s", (tenant_cycle,))
    assert closed == {"global_close_no": global_close, "global_inputs": sorted(tables)}
    # Nothing is copied: cycle_input keeps derived rows only.
    assert rt.db.one("SELECT count(*) AS n FROM control.cycle_input WHERE cycle_id=%s", (tenant_cycle,))["n"] == 0
    frozen = rt.db.all("SELECT dump_id FROM control.cycle_manifest(%s) ORDER BY dump_id", (tenant_cycle,))
    # The dumps of the two declared global tables; the undeclared completion table stays out.
    assert len(frozen) == 2
    original = [manifest_rows(databases, t, tenant_cycle, tables) for t in tables]
    assert {r[0] for rows in original for r in rows} == {first_version(databases)}
    # A newer global build lands and a later global close stamps it.
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("UPDATE marts.mart_enrichment_fixture SET input_version='newer'")
    _, newer = await bound(rt, name)
    await rt.execute(newer["id"])
    rt.cycles.close(newer["cycle_id"])
    replay = await rt.cycles.bind_cycle("daily", scope, "replay:" + uuid4().hex, "other", job, str(tenant_cycle), runner="core")
    assert replay["cycle_id"] == tenant_cycle
    rt.cycles.close(tenant_cycle)
    assert rt.db.all("SELECT dump_id FROM control.cycle_manifest(%s) ORDER BY dump_id", (tenant_cycle,)) == frozen
    assert [manifest_rows(databases, t, tenant_cycle, tables) for t in tables] == original
    # The next tenant cycle reads the newer global close too.
    following = await rt.cycles.bind_cycle("daily", scope, "tenant:" + uuid4().hex, "scheduled", job, runner="core")
    rt.cycles.close(following["cycle_id"])
    assert {r[0] for r in manifest_rows(databases, tables[0], following["cycle_id"], tables)} == {first_version(databases), "newer"}


def first_version(databases):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return conn.execute("SELECT md5('v1')").fetchone()[0]
