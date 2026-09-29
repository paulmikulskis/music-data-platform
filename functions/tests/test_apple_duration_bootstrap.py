"""An empty global track set lets disabled daily collection close safely."""

import ast
import os
from uuid import uuid4

import psycopg
import pytest
from conftest import url_database
from free_source_fixture import frozen_cycle
from mdp_functions.playlist_targets import seed_playlist_targets
from mdp_functions.registry import discover
from mdp_functions.settings import REPO
from mdp_functions.streamline_defaults import seed_streamline_defaults
from psycopg.conninfo import conninfo_to_dict

SOURCE = "apple_song_duration"
DAILY_KINDS = ["artist_page", "curator", "playlist", "track"]


def bootstrap_targets(conn, script):
    # Run the actual seed call from each entry point without migrations or secrets.
    path = REPO / script
    calls = [
        node
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "seed_playlist_targets"
    ]
    assert len(calls) == 1
    return eval(
        compile(ast.Expression(calls[0]), str(path), "eval"),
        {
            "conn": conn,
            "root": REPO,
            "REPO": REPO,
            "seed_playlist_targets": seed_playlist_targets,
        },
    )


@pytest.mark.parametrize(
    "bootstrap",
    ["ops/fly/bootstrap.py", "functions/src/mdp_functions/cli.py"],
    ids=["deployed", "local"],
)
@pytest.mark.parametrize("reason", ["scheduled", "restore"])
async def test_bootstrap_closes_daily_with_no_apple_targets(
    rt, databases, bootstrap, reason
):
    assert not rt.db.one(
        "SELECT id FROM control.target_set WHERE kind='track' AND tenant_id IS NULL"
    )
    if reason == "restore":
        # This is the cycle deployed before track bootstrap existed.
        _, run_id, _ = await frozen_cycle(rt, DAILY_KINDS)

    control_url = url_database(
        os.environ["MDP_CONTROL_RT_DATABASE_URL"],
        conninfo_to_dict(databases["control_url"])["dbname"],
    )
    with psycopg.connect(control_url) as conn:
        bootstrap_targets(conn, bootstrap)
        bootstrap_targets(conn, bootstrap)
    # Each runtime fixture starts with fresh streamlines, so seed their defaults too.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "DELETE FROM control.audit_log WHERE action='streamline_defaults' AND subject=%s",
            (SOURCE,),
        )
    with (
        psycopg.connect(databases["control_url"]) as functions,
        psycopg.connect(control_url) as control,
    ):
        seed_streamline_defaults(functions, control, {SOURCE: discover()[SOURCE]})

    async def close_daily(dbt_run_id, expected):
        export = rt.admit(
            "targets_export", dbt_run_id=dbt_run_id, target_kinds=DAILY_KINDS
        )
        await rt.execute(export["id"])
        run = rt.admit(SOURCE, dbt_run_id=dbt_run_id)
        assert (run["status"], run["error_class"], run["expected_batches"]) == (
            "succeeded",
            expected,
            0,
        )
        await rt.execute(run["id"])
        receipt = rt.receipts(run["id"])
        assert receipt["run"]["rows_written"] == 0
        receipt_status = "paused" if expected == "paused" else "succeeded"
        assert receipt["run"]["status"] == receipt_status
        assert [row["status"] for row in receipt["receipts"]] == [receipt_status]
        assert not rt.db.all(
            "SELECT id FROM control.call_ledger WHERE run_id=%s", (run["id"],)
        )
        close = rt.admit("cycle_close", dbt_run_id=dbt_run_id)
        await rt.execute(close["id"])
        assert rt.receipts(close["id"])["run"]["status"] == "succeeded"
        return run

    if reason == "restore":
        await close_daily(run_id, "not_in_cycle")
    # A new daily run freezes the empty set, including the first day after a restore.
    run_id = "local:" + uuid4().hex
    await rt.cycles.bind_cycle(
        "daily", "global", run_id, "scheduled", "local:daily", runner="core"
    )
    run = await close_daily(run_id, "paused")
    assert run["revision_id"] is not None
    assert (
        rt.db.one(
            "SELECT member_count FROM control.target_export WHERE id=%s",
            (run["revision_id"],),
        )["member_count"]
        == 0
    )
    assert (
        rt.db.one(
            "SELECT enabled FROM control.streamline WHERE source_key=%s", (SOURCE,)
        )["enabled"]
        is False
    )
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.target_set WHERE kind='track' AND tenant_id IS NULL"
        )["n"]
        == 1
    )
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.target t JOIN control.target_set s ON s.id=t.target_set_id "
            "WHERE s.kind='track' AND s.tenant_id IS NULL"
        )["n"]
        == 0
    )
