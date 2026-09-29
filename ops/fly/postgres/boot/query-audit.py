#!/usr/bin/env python3
"""Collect normalized query counters by staff role. Never persist statement text."""

import hashlib
import time
from datetime import datetime, timezone

import psycopg
import sqlglot
from mdp_functions.query_labels import describe, inputs, record
from mdp_functions.relation_labels import UNKNOWN
from sqlglot import exp


def normalized(query):
    """Defense in depth: pg_stat_statements representative text can retain comments."""
    try:
        trees = sqlglot.parse(query or "", read="postgres")
        for tree in trees:
            if not isinstance(
                tree, (exp.Query, exp.Insert, exp.Update, exp.Delete, exp.Merge)
            ):
                return None
            for node in tree.walk():
                node.pop_comments()
                if isinstance(node, exp.Literal):
                    node.replace(exp.Parameter(this=exp.Literal.number(1)))
        return "; ".join(tree.sql(dialect="postgres", comments=False) for tree in trees)
    except sqlglot.errors.SqlglotError:
        return None


def consume(conn):
    rows = conn.execute("""
        SELECT s.userid,s.queryid,s.stats_since,s.calls,s.query,r.rolname
        FROM catalog.pg_stat_statements s JOIN pg_roles r ON r.oid=s.userid
        WHERE s.dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
          AND s.toplevel AND s.calls>0 AND pg_has_role(r.oid,'explorer_ro','MEMBER')
    """).fetchall()
    for user_id, query_id, since, calls, query, actor in rows:
        previous = conn.execute(
            "SELECT calls FROM catalog.audit_counters WHERE userid=%s AND queryid=%s AND stats_since=%s",
            (user_id, query_id, since),
        ).fetchone()
        if previous and previous[0] >= calls:
            continue
        safe_query = normalized(query)
        labels = dict(UNKNOWN, tenants=[], cross_tenant=False, unresolved=True)
        if safe_query:
            try:
                with conn.transaction():
                    conn.execute("SET LOCAL statement_timeout='5s'")
                    labels = describe(conn, safe_query)
                labels["unresolved"] |= "$" in safe_query or any(
                    not s for s, _ in inputs(safe_query)[0]
                )
            except psycopg.Error:
                pass
        labels.update(
            calls=calls - (previous[0] if previous else 0),
            timing="observed aggregate; execution times unavailable",
        )
        identity = f"{user_id}:{query_id}:{since}:{calls}"
        record(
            conn,
            actor,
            safe_query or str(query_id),
            labels,
            "postgres",
            hashlib.sha256(identity.encode()).hexdigest(),
            datetime.now(timezone.utc),
        )
        conn.execute(
            """INSERT INTO catalog.audit_counters VALUES (%s,%s,%s,%s)
            ON CONFLICT(userid,queryid,stats_since) DO UPDATE SET calls=EXCLUDED.calls""",
            (user_id, query_id, since, calls),
        )
    conn.execute("""DELETE FROM catalog.audit_counters c WHERE NOT EXISTS (
        SELECT 1 FROM catalog.pg_stat_statements s WHERE s.userid=c.userid AND s.queryid=c.queryid AND s.stats_since=c.stats_since)
    """)
    conn.execute(
        "DELETE FROM catalog.query_audit WHERE occurred_at < now()-interval '30 days'"
    )
    conn.commit()


def main():
    while True:
        try:
            with psycopg.connect(
                "dbname=warehouse user=postgres host=/var/run/postgresql"
            ) as conn:
                consume(conn)
        except (psycopg.Error, ValueError):
            print(
                "Query audit needs attention; normalized counters remain available.",
                flush=True,
            )
        time.sleep(5)


if __name__ == "__main__":
    main()
