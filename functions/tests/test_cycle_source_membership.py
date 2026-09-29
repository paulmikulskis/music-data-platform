"""Tests for cycle source membership."""


import os
from uuid import uuid4

import psycopg
from free_source_fixture import (
    FREE_SOURCE_KEYS,
    MEMBERSHIP,
    frozen_cycle,
    refused,
    seed_charts,
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions.registry import discover


async def test_a_rebuild_of_a_cycle_frozen_before_a_new_source_records_not_in_cycle(rt, databases):
    """The deploy's rebuild rebinds the newest closed daily cycle, whose export froze the previous release's
    kinds. sz_chart and its chart set came after, so its invoke records not_in_cycle and succeeds empty, and
    the close proceeds."""
    _, rebuild, cycle_id = await frozen_cycle(rt, ["artist_page", "curator", "playlist"])
    seed_charts(databases)
    run = rt.admit("sz_chart", dbt_run_id=rebuild)
    assert (run["status"], run["coverage"], run["error_class"], run["expected_batches"]) == (
        "succeeded", "empty", "not_in_cycle", 0)
    assert run["cycle_id"] == cycle_id and run["revision_id"] is None
    assert run["error_message"] == (f"not_in_cycle: cycle {cycle_id} froze its membership without the chart kind, "
                                    "so sz_chart has nothing to collect in it")
    await rt.execute(run["id"])
    receipt = rt.receipts(run["id"])
    assert receipt["run"]["status"] == "succeeded" and receipt["run"]["rows_written"] == 0
    assert [(r["status"], r["coverage"], r["message"]) for r in receipt["receipts"]] == [
        ("succeeded", "empty", run["error_message"])]
    assert not rt.db.all("SELECT id FROM control.call_ledger WHERE run_id=%s", (run["id"],))
    assert rt.db.one("SELECT count(*) AS n FROM control.run_event WHERE run_id=%s AND event_type='not_in_cycle'",
                     (run["id"],))["n"] == 1
    # The rebuild's invoke is idempotent, and its close proceeds on the closed cycle.
    assert rt.admit("sz_chart", dbt_run_id=rebuild)["id"] == run["id"]
    close = rt.admit("cycle_close", dbt_run_id=rebuild)
    await rt.execute(close["id"])
    assert rt.receipts(close["id"])["run"]["status"] == "succeeded"
    # The next scheduled cycle exports the chart membership and collects it.
    fresh = "local:" + uuid4().hex
    await rt.cycles.bind_cycle("daily", "global", fresh, "scheduled", "local:daily", runner="core")
    await rt.execute(rt.admit("targets_export", dbt_run_id=fresh, target_kinds=["chart", "playlist"])["id"])
    run = rt.admit("sz_chart", dbt_run_id=fresh)
    assert run["revision_id"] and run["error_class"] is None and run["expected_batches"] >= 1


async def test_a_rebuild_of_an_open_cycle_frozen_before_a_new_source_records_not_in_cycle(rt, databases):
    """A scheduled run that died after its export leaves its cycle open; the deploy's rebuild attaches to it,
    and the export it reuses froze no chart membership."""
    _, rebuild, cycle_id = await frozen_cycle(rt, ["playlist"], close=False)
    seed_charts(databases)
    run = rt.admit("sz_chart", dbt_run_id=rebuild)
    assert (run["status"], run["error_class"], run["cycle_id"]) == ("succeeded", "not_in_cycle", cycle_id)
    # The rebuild's export is the scheduled run's frozen export, so no chart revision appears.
    export = rt.admit("targets_export", dbt_run_id=rebuild, target_kinds=["chart", "playlist"])
    await rt.execute(export["id"])
    assert rt.admit("sz_chart", dbt_run_id=rebuild)["id"] == run["id"]
    await rt.execute(rt.admit("cycle_close", dbt_run_id=rebuild)["id"])
    assert rt.db.one("SELECT status FROM control.cycle WHERE id=%s", (cycle_id,))["status"] == "closed"


async def test_a_rebuild_after_a_listed_kind_s_target_set_came_records_not_in_cycle(rt, databases):
    """The export listed the chart kind before its set existed, so it froze no chart membership."""
    _, rebuild, cycle_id = await frozen_cycle(rt, ["chart", "playlist"])
    assert not rt.db.one("SELECT count(*) AS n FROM control.target_export WHERE cycle_id=%s", (cycle_id,))["n"]
    seed_charts(databases)
    run = rt.admit("sz_chart", dbt_run_id=rebuild)
    assert run["error_class"] == "not_in_cycle" and run["error_message"] == (
        f"not_in_cycle: cycle {cycle_id} froze its membership before its chart target set existed, "
        "so sz_chart has nothing to collect in it")


async def test_missing_membership_in_a_new_cycle_or_one_its_export_owes_stays_scope_mismatch(rt, databases):
    seed_charts(databases)
    # A new cycle that has not exported fails loudly.
    fresh = "local:" + uuid4().hex
    await rt.cycles.bind_cycle("daily", "global", fresh, "scheduled", "local:daily", runner="core")
    assert refused(rt, "sz_chart", fresh) == MEMBERSHIP
    # So does a new cycle whose own export froze no chart kind (a payload behind the functions image).
    await rt.execute(rt.admit("targets_export", dbt_run_id=fresh, target_kinds=["playlist"])["id"])
    assert refused(rt, "sz_chart", fresh) == MEMBERSHIP
    await rt.execute(rt.admit("cycle_close", dbt_run_id=fresh)["id"])
    # A closed cycle whose export froze the chart set but holds no chart revision is a genuine gap.
    _, rebuild, cycle_id = await frozen_cycle(rt, ["chart", "playlist"])
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.target_export_member m USING control.target_export e, control.target_set s "
                     "WHERE m.revision_id=e.id AND e.target_set_id=s.id AND e.cycle_id=%s AND s.kind='chart'", (cycle_id,))
        conn.execute("DELETE FROM control.target_export e USING control.target_set s "
                     "WHERE e.target_set_id=s.id AND e.cycle_id=%s AND s.kind='chart'", (cycle_id,))
    assert refused(rt, "sz_chart", rebuild) == MEMBERSHIP
    # An export with no frozen kind list, or one that did not succeed, gives no evidence of absence.
    for change in ("resolved_config=NULL", "status='failed'"):
        _, rebuild, cycle_id = await frozen_cycle(rt, ["playlist"])
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute(f"UPDATE control.run SET {change} WHERE cycle_id=%s AND kind='export'", (cycle_id,))
        assert refused(rt, "sz_chart", rebuild) == MEMBERSHIP, change
    # A kind with no target set at all is refused.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.target_set SET kind='chart_retired' WHERE kind='chart'")
    _, rebuild, _ = await frozen_cycle(rt, ["playlist"])
    assert refused(rt, "sz_chart", rebuild) == MEMBERSHIP


async def test_the_other_free_sources_keys_admit_on_a_rebuild_of_a_cycle_frozen_before_them(rt, databases):
    """Only a bronze source declares Targets, so only sz_chart among the free sources reaches the frozen
    membership. The rest admit on the previous release's cycles as they do on any: paused while disabled,
    queued otherwise."""

    from conftest import url_database
    from mdp_functions.registry import REGISTRY
    from mdp_functions.streamline_defaults import seed_streamline_defaults
    from psycopg.conninfo import conninfo_to_dict

    catalog = discover()
    assert {m.layer for m in catalog.values() if m.targets} == {"bronze"}
    assert [k for k in FREE_SOURCE_KEYS if catalog[k].targets] == ["sz_chart"]
    control = url_database(os.environ.get("MDP_CONTROL_RT_URL") or os.environ["MDP_CONTROL_RT_DATABASE_URL"],
                           conninfo_to_dict(databases["control_url"])["dbname"])
    keys = FREE_SOURCE_KEYS[1:]
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.audit_log WHERE action='streamline_defaults' AND subject=ANY(%s)", (list(keys),))
    with psycopg.connect(databases["control_url"]) as functions, psycopg.connect(control) as rt_conn:
        seed_streamline_defaults(functions, rt_conn, {k: REGISTRY[k] for k in keys})

    def admitted(key, dbt_run_id, **kwargs):
        run = rt.admit(key, dbt_run_id=dbt_run_id, **kwargs)
        paused = not rt.db.one("SELECT enabled FROM control.streamline WHERE source_key=%s", (key,))["enabled"]
        assert (run["status"], run["error_class"]) == (("succeeded", "paused") if paused else ("queued", None)), key
        return run

    # The previous release's global kind lists: daily artist_page, curator and playlist; weekly none.
    _, daily, _ = await frozen_cycle(rt, ["artist_page", "curator", "playlist"])
    for key in ("kexp_plays", "lb_fresh_releases"):
        admitted(key, daily)
    _, weekly, _ = await frozen_cycle(rt, [], cadence="weekly")
    admitted("lb_sitewide", weekly)
    _, rebuild, _ = await frozen_cycle(rt, ["artist_page", "track"])
    for key in ("lb_popularity", "lb_similar_artists", "wiki_sitelinks", "wiki_pageviews"):
        relation = catalog[key].reads[0]
        admitted(key, rebuild, input_relation=relation)
