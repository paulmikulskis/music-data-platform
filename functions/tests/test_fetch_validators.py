"""a batch with a rejected or quarantined dump loses the fetch validators it wrote."""

import psycopg
from conftest import bound
from mdp_functions.recovery import Recovery
from psycopg.types.json import Jsonb


def dump(conn, run, status):
    identity = conn.execute(
        "INSERT INTO control.dump(kind,run_id,streamline_id,cycle_id,uri_prefix) VALUES ('output',%s,%s,%s,'fixture') RETURNING id",
        (run["id"], run["streamline_id"], run["cycle_id"]),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO control.load(dump_id,warehouse_id,target_table,status) VALUES (%s,%s,'raw.playlist_items',%s)",
        (identity, run["warehouse_id"], status),
    )
    return identity


def cursor(conn, run, target, dump_id):
    conn.execute(
        "INSERT INTO control.cursor(streamline_id,target_id,cursor_key,cursor_value,version,dump_id) VALUES (%s,%s,'default',%s,1,%s)",
        (run["streamline_id"], target, Jsonb({"validators": {"etag": {"value": "W/1"}}, "page": 3}), dump_id),
    )


async def test_recovery_clears_validators_written_by_a_batch_with_a_rejected_dump(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    batches = rt.db.all("SELECT id,target_ids FROM control.batch WHERE run_id=%s ORDER BY index", (run["id"],))
    (bad, bad_target), (good, good_target) = [(b["id"], b["target_ids"][0]) for b in batches[:2]]
    with psycopg.connect(databases["admin_control"]) as conn:
        snapshot, items = dump(conn, run, "loaded"), dump(conn, run, "rejected")
        clean, earlier = dump(conn, run, "loaded"), dump(conn, run, "rejected")
        conn.execute("UPDATE control.batch SET dump_ids=%s WHERE id=%s", ([snapshot, items], bad))
        conn.execute("UPDATE control.batch SET dump_ids=%s WHERE id=%s", ([clean], good))
        # The second target also had an earlier batch with a rejected dump; a clean batch then
        # advanced its cursor, so its validators stay.
        conn.execute(
            "INSERT INTO control.batch(run_id,index,target_ids,dump_ids) VALUES (%s,99,%s,%s)",
            (run["id"], [good_target], [earlier]),
        )
        cursor(conn, run, bad_target, snapshot)
        cursor(conn, run, good_target, clean)
    await Recovery(rt).once(resume=False)
    values = {
        str(r["target_id"]): (r["cursor_value"], r["version"])
        for r in rt.db.all("SELECT target_id,cursor_value,version FROM control.cursor WHERE streamline_id=%s", (run["streamline_id"],))
    }
    # The rejected items dump invalidates the snapshot's validators too: they are one batch.
    assert values[str(bad_target)] == ({"page": 3}, 2)
    assert values[str(good_target)] == ({"validators": {"etag": {"value": "W/1"}}, "page": 3}, 1)
    # Clearing is idempotent: a second pass leaves the cursor as it is.
    await Recovery(rt).once(resume=False)
    assert rt.db.one("SELECT version FROM control.cursor WHERE target_id=%s", (bad_target,))["version"] == 2


async def test_a_quarantined_dump_clears_its_batch_validators(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    [batch] = rt.db.all("SELECT id,target_ids FROM control.batch WHERE run_id=%s ORDER BY index LIMIT 1", (run["id"],))
    with psycopg.connect(databases["admin_control"]) as conn:
        quarantined = dump(conn, run, "loaded")
        conn.execute("UPDATE control.dump SET quarantined_at=now() WHERE id=%s", (quarantined,))
        conn.execute("UPDATE control.batch SET dump_ids=%s WHERE id=%s", ([quarantined], batch["id"]))
        cursor(conn, run, batch["target_ids"][0], quarantined)
    await Recovery(rt).once(resume=False)
    value = rt.db.one("SELECT cursor_value FROM control.cursor WHERE target_id=%s", (batch["target_ids"][0],))
    assert value["cursor_value"] == {"page": 3}
