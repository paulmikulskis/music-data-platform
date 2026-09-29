"""The pending Replay restore of one Core runner lock key (control.runner_restore, written by control_rt).

`run.sh` marks a restore before a Replay and clears it after the restore passes, so a Replay that a
kill or a machine stop cuts short still leaves the restore for the next run under that lock.

    runner_restore.py pending <lock_key>                     prints "<cycle_id> <target> <vars>" or nothing
    runner_restore.py mark <lock_key> <cycle_id> <target> <vars>
    runner_restore.py clear <lock_key> <cycle_id>
"""

import json
import os
import sys

import psycopg


def main() -> int:
    action, key, *rest = sys.argv[1:]
    with psycopg.connect(os.environ["MDP_CONTROL_RT_URL"]) as conn:
        if action == "pending":
            row = conn.execute(
                "SELECT cycle_id::text,target,vars FROM control.runner_restore WHERE lock_key=%s", (key,)
            ).fetchone()
            if row:
                print(row[0], row[1], json.dumps(row[2], separators=(",", ":"), sort_keys=True))
        elif action == "mark":
            cycle_id, target, variables = rest
            conn.execute(
                "INSERT INTO control.runner_restore(lock_key,cycle_id,target,vars) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(lock_key) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,target=EXCLUDED.target,"
                "vars=EXCLUDED.vars,requested_at=now()",
                (key, cycle_id, target, variables),
            )
        elif action == "clear":
            conn.execute("DELETE FROM control.runner_restore WHERE lock_key=%s AND cycle_id=%s", (key, rest[0]))
        else:
            raise SystemExit(f"unknown action {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
