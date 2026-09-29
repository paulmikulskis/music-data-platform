"""Tests for cycle stamp upgrade."""

from uuid import uuid4

import psycopg
import pytest
from mdp_functions.runs import Runtime

OLD_COLUMNS = "id,cadence,scope,opened_at,opened_by_dbt_run_id,closed_at,status,git_sha,image_digest"


def old_bind(databases, cadence: str, dbt_id: str) -> str:
    """b45383a's scheduled bind: a cycle with no manifest_mode, and an attempt with no stamp_protocol."""
    with psycopg.connect(databases["admin_control"]) as conn:
        cycle_id = conn.execute(
            "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,git_sha,image_digest) VALUES (%s,'global',%s,NULL,NULL) RETURNING id",
            (cadence, dbt_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO control.cycle_attempt(dbt_run_id,cycle_id,reason_category,git_sha,image_digest,runner,job_id) "
            "VALUES (%s,%s,'scheduled',NULL,NULL,'core',%s)",
            (dbt_id, cycle_id, f"local:{cadence}"),
        )
    return str(cycle_id)


def old_close(databases, cycle_id: str) -> None:
    """b45383a's close: status and closed_at only, after listing the committed receipts."""
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.cycle SET status='closed',closed_at=now() WHERE id=%s", (cycle_id,))


def old_mirror(databases) -> None:
    """b45383a's mirror_cycles: every control.cycle row, deleted and inserted with its own columns."""
    with psycopg.connect(databases["admin_control"]) as control:
        rows = control.execute(f"SELECT {OLD_COLUMNS} FROM control.cycle").fetchall()
    with psycopg.connect(databases["admin_warehouse"]) as warehouse:
        for row in rows:
            warehouse.execute("DELETE FROM raw.cycles WHERE id=%s", (row[0],))
            warehouse.execute(f"INSERT INTO raw.cycles ({OLD_COLUMNS}) VALUES ({','.join(['%s'] * 9)})", row)


def control_rows(rt: Runtime, ids: list[str]) -> dict[str, tuple]:
    return {
        str(r["id"]): (r["status"], r["manifest_mode"], r["close_no"])
        for r in rt.db.all("SELECT id,status,manifest_mode::text,close_no FROM control.cycle WHERE id=ANY(%s::uuid[])", (ids,))
    }


def raw_rows(databases, ids: list[str]) -> dict[str, tuple]:
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return {
            str(r[0]): r[1:]
            for r in conn.execute("SELECT id,status,manifest_mode,close_no FROM raw.cycles WHERE id=ANY(%s::uuid[])", (ids,))
        }


async def test_0011_defaults_to_list_and_the_service_inserts_stamp(rt: Runtime, databases) -> None:
    old = old_bind(databases, "hourly", "core:" + uuid4().hex)
    new = await rt.cycles.bind_cycle("daily", "global", "core:" + uuid4().hex, "scheduled", "local:daily", runner="core", global_inputs=[])
    manual = rt.cycles.manual("weekly", "global")
    modes = {r["id"]: r["m"] for r in rt.db.all("SELECT id::text,manifest_mode::text AS m FROM control.cycle")}
    assert (modes[old], modes[str(new["cycle_id"])], modes[str(manual["id"])]) == ("list", "stamp", "stamp")


@pytest.mark.parametrize("bound_by", ["previous", "current"])
async def test_a_rebind_settles_a_cycle_the_previous_image_closed(rt: Runtime, databases, bound_by: str) -> None:
    """previous: an old runner's cycle (list, the 0011 default). current: a new runner bound on the new
    service, and the old service closed it after a functions rollback (stamp). Either one's phase-3
    re-bind on the new service mirrors list/0, which dbt reads as the old close's list."""
    dbt_id = "core:" + uuid4().hex
    if bound_by == "previous":
        cycle_id = old_bind(databases, "daily", dbt_id)
    else:
        cycle_id = str((await rt.cycles.bind_cycle("daily", "global", dbt_id, "scheduled", "local:daily", runner="core", global_inputs=[]))["cycle_id"])
    old_close(databases, cycle_id)
    old_mirror(databases)
    closed_at = rt.db.one("SELECT closed_at FROM control.cycle WHERE id=%s", (cycle_id,))["closed_at"]
    last = rt.db.one("SELECT last_close_no FROM control.scope_close WHERE scope='global'")
    rebound = await rt.cycles.bind_cycle("daily", "global", dbt_id, "scheduled", "local:daily", runner="core", global_inputs=[])
    assert str(rebound["cycle_id"]) == cycle_id
    assert control_rows(rt, [cycle_id]) == {cycle_id: ("closed", "list", 0)}
    assert raw_rows(databases, [cycle_id]) == {cycle_id: ("closed", "list", 0)}
    assert rt.db.one("SELECT closed_at FROM control.cycle WHERE id=%s", (cycle_id,))["closed_at"] == closed_at
    after = rt.db.one("SELECT last_close_no FROM control.scope_close WHERE scope='global'")
    assert (after or {})["last_close_no"] == (last or {"last_close_no": 0})["last_close_no"]


async def test_a_replay_or_retry_attach_settles_the_cycle_it_binds(rt: Runtime, databases) -> None:
    cycle_id = old_bind(databases, "hourly", "core:" + uuid4().hex)
    old_close(databases, cycle_id)
    replay = await rt.cycles.bind_cycle(
        "hourly", "global", "core:" + uuid4().hex, "other", "local:hourly", cycle_id=cycle_id, runner="core", global_inputs=[]
    )
    assert str(replay["cycle_id"]) == cycle_id and control_rows(rt, [cycle_id]) == {cycle_id: ("closed", "list", 0)}
    attached = old_bind(databases, "weekly", "core:" + uuid4().hex)
    old_close(databases, attached)
    retry = await rt.cycles.bind_cycle("weekly", "global", "core:" + uuid4().hex, "other", "local:weekly", runner="core", global_inputs=[])
    assert str(retry["cycle_id"]) == attached
    assert raw_rows(databases, [cycle_id, attached]) == {cycle_id: ("closed", "list", 0), attached: ("closed", "list", 0)}


async def test_the_catch_up_settles_window_cycles_and_rewrites_stripped_rows(rt: Runtime, databases) -> None:
    # Before the deploy window: a stamp cycle closed on the new service.
    first = await rt.cycles.bind_cycle("hourly", "global", "core:" + uuid4().hex, "scheduled", "local:hourly", runner="core", global_inputs=[])
    stamped = rt.cycles.close(first["cycle_id"])
    # In the window (or after a rollback): the old service closes a cycle, opens another, and mirrors.
    window = old_bind(databases, "daily", "core:" + uuid4().hex)
    old_close(databases, window)
    opened = old_bind(databases, "weekly", "core:" + uuid4().hex)
    old_mirror(databases)
    ids = [str(first["cycle_id"]), window, opened]
    assert all(row[1] is None for row in raw_rows(databases, ids).values())
    assert set(ids) <= set(rt.warehouse.stripped_cycles())

    # The next close on the new service repairs every row before its own reads.
    second = await rt.cycles.bind_cycle("hourly", "global", "core:" + uuid4().hex, "scheduled", "local:hourly", runner="core", global_inputs=[])
    closed = rt.cycles.close(second["cycle_id"])
    expected = {
        str(first["cycle_id"]): ("closed", "stamp", stamped["close_no"]),
        window: ("closed", "list", 0),
        opened: ("open", "list", None),
        str(second["cycle_id"]): ("closed", "stamp", closed["close_no"]),
    }
    assert control_rows(rt, list(expected)) == expected
    assert raw_rows(databases, list(expected)) == expected
    assert not set(expected) & set(rt.warehouse.stripped_cycles())
    # A stamp-mode read at the new close counts the window cycle's revisions (mdp_revision_sql).
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        counted = {
            str(r[0]) for r in conn.execute("SELECT id FROM raw.cycles WHERE scope='global' AND close_no <= %s", (closed["close_no"],))
        }
    assert {str(first["cycle_id"]), window, str(second["cycle_id"])} <= counted

    # A rollback strips the rows again; recovery's catch-up (every minute) rewrites them.
    old_mirror(databases)
    rt.cycles.mirror()
    assert raw_rows(databases, list(expected)) == expected
