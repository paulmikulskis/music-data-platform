"""frozen specs reach raw.targets in bulk; exports freeze only the kinds a cadence reads;
revisions count for a cycle by close number."""

import subprocess
from uuid import uuid4

import psycopg
from conftest import bound
from mdp_functions.targets import export_targets


def targets(databases, revision_id):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return conn.execute(
            "SELECT platform_account_id,resource_kind,canonical_key,params_json,taken_at FROM raw.targets "
            "WHERE _revision_id=%s ORDER BY platform_account_id",
            (revision_id,),
        ).fetchall()


async def test_frozen_specs_are_mirrored_once_per_revision(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        target = conn.execute("SELECT id FROM control.target WHERE platform_account_id='acceptance-001'").fetchone()[0]
        conn.execute(
            "INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES (%s,'account','fixture:acceptance-001','{\"cadence\": \"hourly\"}')",
            (target,),
        )
    binding = await rt.cycles.bind_cycle("hourly", "global", "h:" + uuid4().hex, "scheduled", "local:hourly", runner="core")
    revision = export_targets(rt.db, rt.warehouse, binding["cycle_id"])
    rows = targets(databases, revision["id"])
    assert [r[:4] for r in rows] == [
        ("acceptance-001", "account", "fixture:acceptance-001", {"cadence": "hourly"}),
        ("acceptance-002", None, None, {}),
    ]
    assert all(r[4] == revision["taken_at"] for r in rows)
    # Write-once: exporting the same revision again adds no rows.
    export_targets(rt.db, rt.warehouse, binding["cycle_id"])
    assert len(targets(databases, revision["id"])) == 2


async def test_a_cadence_exports_only_the_kinds_its_functions_read(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.target_set(kind,name) VALUES ('playlist','Fixture playlists')")
    # Only the playlist and account sets exist here; no weekly collector reads targets.
    for cadence, expected in (("hourly", {"account"}), ("daily", {"playlist"}), ("weekly", set())):
        dbt_run = f"{cadence}:{uuid4().hex}"
        binding = await rt.cycles.bind_cycle(cadence, "global", dbt_run, "scheduled", f"local:{cadence}", runner="core")
        run = rt.admit("targets_export", dbt_run_id=dbt_run)
        await rt.execute(run["id"])
        kinds = {
            r["kind"] for r in rt.db.all(
                "SELECT s.kind FROM control.target_export e JOIN control.target_set s ON s.id=e.target_set_id WHERE e.cycle_id=%s",
                (binding["cycle_id"],),
            )
        }
        assert kinds == expected, cadence


async def test_the_export_payload_carries_the_kinds_it_freezes(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.target_set(kind,name) VALUES ('playlist','Fixture playlists')")
    dbt_run = "hourly:" + uuid4().hex
    binding = await rt.cycles.bind_cycle("hourly", "global", dbt_run, "scheduled", "local:hourly", runner="core")
    # mdp_export_kinds() passes the list; the service freezes exactly it, not its own guess.
    run = rt.admit("targets_export", dbt_run_id=dbt_run, target_kinds=["playlist"])
    # A fixture-mode runtime also records its scenario, so holdings can leave fixture runs out.
    fixture = {"fixture": True, "fixture_scenario": rt.settings.fixture_scenario} if rt.settings.fixture else {}
    assert run["resolved_config"] == {"target_kinds": ["playlist"], **fixture}
    await rt.execute(run["id"])
    kinds = {
        r["kind"] for r in rt.db.all(
            "SELECT s.kind FROM control.target_export e JOIN control.target_set s ON s.id=e.target_set_id WHERE e.cycle_id=%s",
            (binding["cycle_id"],),
        )
    }
    assert kinds == {"playlist"}


async def test_revisions_count_for_a_cycle_by_close_number(rt, databases):
    _, run = await bound(rt, "fixture_accounts")
    rt.cycles.close(run["cycle_id"])
    second = await rt.cycles.bind_cycle("hourly", "global", "h:" + uuid4().hex, "scheduled", "local:hourly", runner="core")
    export_targets(rt.db, rt.warehouse, second["cycle_id"])
    rt.cycles.close(second["cycle_id"])
    cycle = rt.db.one("SELECT * FROM control.cycle WHERE id=%s", (run["cycle_id"],))
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        counted = {
            str(r[0]) for r in conn.execute(
                f"SELECT DISTINCT _cycle_id FROM raw.targets WHERE {revision_filter(cycle)} AND _cycle_id=ANY(%s)",
                ([run["cycle_id"], second["cycle_id"]],),
            )
        }
    # A replay of the first cycle never sees the later cycle's revision.
    assert counted == {str(run["cycle_id"])}


def revision_filter(cycle):
    context = {
        "cycle_id": str(cycle["id"]), "cadence": cycle["cadence"], "scope": cycle["scope"],
        "manifest_mode": cycle["manifest_mode"], "close_no": cycle["close_no"],
        "global_close_no": cycle["global_close_no"],
    }
    render = f"""
from pathlib import Path
from jinja2 import Environment
captured = []
environment = Environment(extensions=['jinja2.ext.do'])
environment.globals['return'] = lambda value: captured.append(value) or ''
environment.from_string(Path('dbt/macros/mdp_context.sql').read_text()).module.mdp_revision_sql({context!r}, '_cycle_id')
print(captured[-1])
"""
    return subprocess.run(
        ["uv", "run", "--project", "dbt", "python", "-c", render], capture_output=True, text=True, check=True
    ).stdout
