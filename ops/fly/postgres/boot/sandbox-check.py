#!/usr/bin/env python3
"""Operator-only ACL and quota checks, separate from production cycles."""

import time

import psycopg
from mdp_functions.sandbox import maintain
from mdp_functions.sandbox_policy import POLICY, message


def check(warehouse, control):
    alerts = maintain(warehouse)
    for schema, code in alerts:
        # A single open warning per sandbox; no values or statement text enter alerts.
        control.execute(
            "INSERT INTO control.runbook(slug,title,body_md) VALUES (%s,%s,%s) ON CONFLICT(slug) DO NOTHING",
            (code.replace("_", "-"), code.replace("_", " "), message(code)),
        )
        control.execute(
            "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) SELECT %s,'warning','sandbox',%s,%s WHERE NOT EXISTS (SELECT 1 FROM control.alert WHERE class=%s AND subject_id=%s AND resolved_at IS NULL)",
            (code, schema, code.replace("_", "-"), code, schema),
        )
    for code in ("sandbox_quota_warning", "sandbox_acl_alert"):
        active = [schema for schema, kind in alerts if kind == code]
        control.execute(
            "UPDATE control.alert SET resolved_at=now() WHERE subject_type='sandbox' AND class=%s AND resolved_at IS NULL AND NOT (subject_id=ANY(%s::text[]))",
            (code, active),
        )
    warehouse.commit()
    control.commit()


def main():
    while True:
        try:
            with (
                psycopg.connect(
                    "dbname=warehouse user=postgres host=/var/run/postgresql"
                ) as warehouse,
                psycopg.connect(
                    "dbname=control user=postgres host=/var/run/postgresql"
                ) as control,
            ):
                check(warehouse, control)
        except (psycopg.Error, ValueError):
            print(
                "Sandbox checks need attention. Inspect the sandbox-check worker and rerun mdp warehouse sandbox status --all.",
                flush=True,
            )
        time.sleep(POLICY["poll_seconds"])


if __name__ == "__main__":
    main()
