#!/usr/bin/env python3
"""Recover historical frozen targets from immutable raw.targets, never live targets.

Run with migrator MDP_CONTROL_DATABASE_URL and read-only MDP_SERVICE_READ_URL.
Missing or conflicting historical rows remain blocked; rerunning is safe.
"""
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def backfill(control, warehouse):
    recovered = missing = 0
    members = control.execute(
        "SELECT revision_id,target_id FROM control.target_export_member WHERE NOT spec_recovered"
    ).fetchall()
    for member in members:
        rows = warehouse.execute(
            "SELECT * FROM raw.targets WHERE _revision_id::text=%s AND id::text=%s",
            (str(member['revision_id']), str(member['target_id'])),
        ).fetchall()
        targets = [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows]
        if not targets or any(t != targets[0] for t in targets) or not all(
            targets[0].get(k) is not None for k in ('id', 'target_set_id', 'platform', 'platform_account_id')
        ) or 'handle' not in targets[0]:
            missing += 1
            continue
        # PostgreSQL types (uuid/timestamp) become the same JSON representation as to_jsonb(t).
        import json
        target = json.loads(json.dumps(targets[0], default=str))
        control.execute(
            "UPDATE control.target_export_member SET target_json=%s,spec_recovered=true "
            "WHERE revision_id=%s AND target_id=%s AND NOT spec_recovered",
            (Jsonb(target), member['revision_id'], member['target_id']),
        )
        recovered += 1
    return recovered, missing


if __name__ == '__main__':
    with psycopg.connect(os.environ['MDP_CONTROL_DATABASE_URL'], row_factory=dict_row) as control:
        with psycopg.connect(os.environ['MDP_SERVICE_READ_URL'], row_factory=dict_row) as warehouse:
            recovered, missing = backfill(control, warehouse)
    print(f'recovered={recovered} still_blocked={missing}')
    raise SystemExit(1 if missing else 0)
