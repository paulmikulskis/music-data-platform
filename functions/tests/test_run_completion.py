"""22.11 step 1b: a completion=True bronze run writes raw._run_completion as its last dump."""

from uuid import uuid4

import psycopg
import pytest
from conftest import bound
from mdp_functions.errors import ServiceError
from mdp_functions.layers import bronze, gold
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.warehouse.postgres import PostgresWarehouse, manifest_sql


@pytest.fixture
def completing(rt):
    name = "completing_" + uuid4().hex[:8]
    tables = ["raw." + name + "_a", "raw." + name + "_b"]

    @bronze(source_key=name, writes=tables, cadence="daily", external=False, completion=True)
    async def source(ctx):
        ctx.observed(3)
        ctx.record_completion(range_start=10, range_end=20)
        ctx.emit(tables[0], {"value": 1})
        ctx.emit(tables[0], {"value": 2})
        ctx.emit(tables[1], {"value": 3})

    sync(rt.db)
    yield name, tables
    REGISTRY.pop(name)


def completion(databases, source_key):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return conn.execute(
            "SELECT run_id,source_key,outputs,recorded,_dump_id::text,_landed_seq FROM raw._run_completion WHERE _source_key=%s",
            (source_key,),
        ).fetchall()


def reconciles(databases, cycle_id, source_key):
    """The consumer rule: the completion row and every dump it lists are in the cycle's manifest,
    and each listed dump's rows match its recorded count."""
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        visible = {str(r[0]) for r in conn.execute(manifest_sql("%(cycle)s"), {"cycle": cycle_id})}
        rows = conn.execute(
            "SELECT outputs,_dump_id::text FROM raw._run_completion WHERE _source_key=%s", (source_key,)
        ).fetchall()
        for outputs, dump in rows:
            if dump not in visible:
                continue
            counts = {
                table: {
                    str(d): n
                    for d, n in conn.execute(
                        f"SELECT _dump_id,count(*) FROM {table} GROUP BY _dump_id"
                    ).fetchall()
                }
                if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
                else {}
                for table in outputs
            }
            if all(
                dump_id in visible and counts[table].get(dump_id) == count
                for table, listed in outputs.items()
                for dump_id, count in listed.items()
            ):
                return True
    return False


async def test_completion_lands_last_and_reconciles(rt, databases, completing):
    name, tables = completing
    _, run = await bound(rt, name)
    await rt.execute(run["id"])
    [(run_id, source_key, outputs, recorded, _, seq)] = completion(databases, name)
    assert (run_id, source_key, recorded) == (str(run["id"]), name, {"range_start": 10, "range_end": 20})
    assert {t: sorted(v.values()) for t, v in outputs.items()} == {tables[0]: [2], tables[1]: [1]}
    # Registered after every other dump of the run.
    assert seq == rt.db.one("SELECT max(landed_seq) AS n FROM control.dump WHERE run_id=%s", (run["id"],))["n"]
    result = rt.db.one("SELECT status,rows_written FROM control.run WHERE id=%s", (run["id"],))
    assert result == {"status": "succeeded", "rows_written": 3}
    rt.cycles.close(run["cycle_id"])
    assert reconciles(databases, run["cycle_id"], name)


async def test_completion_of_a_run_whose_second_dump_fails_does_not_reconcile(rt, databases, completing, monkeypatch):
    name, tables = completing
    original = PostgresWarehouse.land

    def land(self, manifest, claim, parts, hook=None):
        if manifest["target_table"] == tables[1]:
            raise ValueError("corrupt part")  # a permanent data error dead-letters the dump
        return original(self, manifest, claim, parts, hook)

    monkeypatch.setattr(PostgresWarehouse, "land", land)
    _, run = await bound(rt, name)
    await rt.execute(run["id"])
    [(_, _, outputs, _, _, _)] = completion(databases, name)
    # The completion still names the rejected dump, so no consumer can take the run as complete.
    assert set(outputs) == set(tables)
    rt.cycles.close(run["cycle_id"])
    assert not reconciles(databases, run["cycle_id"], name)
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] != "succeeded"


def test_completion_is_a_bronze_or_universal_declaration():
    with pytest.raises(ServiceError, match="bronze or universal declaration"):
        gold(source_key="bad_completion", reads=["marts.x"], writes=["raw.x"], external=True, completion=True)(lambda: None)
    assert "bad_completion" not in REGISTRY


async def test_recovery_writes_the_completion_after_a_crash_past_the_last_batch(rt, databases, completing, monkeypatch):
    from mdp_functions.recovery import Recovery
    from mdp_functions.runs import Runtime

    name, tables = completing

    def crash(self, *args, **kwargs):
        raise RuntimeError("process died after the last batch")

    monkeypatch.setattr(Runtime, "write_completion", crash)
    _, run = await bound(rt, name)
    with pytest.raises(RuntimeError):
        await rt.execute(run["id"])
    monkeypatch.undo()
    written = "SELECT count(*) AS n FROM control.run_event WHERE run_id=%s AND event_type='run_completion_written'"
    assert rt.db.one(written, (run["id"],))["n"] == 0
    assert rt.db.one("SELECT count(*) AS n FROM control.batch WHERE run_id=%s AND status<>'succeeded'", (run["id"],))["n"] == 0
    # Recovery arrives after the attempt's deadline, so no live attempt remains to write it.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.run_attempt SET deadline_at=now()-interval '1 minute' WHERE run_id=%s", (run["id"],))
    await Recovery(rt).once(resume=False)
    [(run_id, _, outputs, recorded, _, _)] = completion(databases, name)
    assert run_id == str(run["id"]) and set(outputs) == set(tables) and recorded == {"range_start": 10, "range_end": 20}
    # The expired attempt still fails the run (invoke_timeout); its completion row lands anyway.
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "failed"
    rt.cycles.close(run["cycle_id"])
    assert reconciles(databases, run["cycle_id"], name)
