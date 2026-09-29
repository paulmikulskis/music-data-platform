"""A gold input that failed on its own in `park_after` runs sits out the read (derived.parked_inputs):
the run and the streamline show the count, an inputs_parked alert opens, and releasing it (resolving
that alert, streamlines.unpark) returns the inputs. Envelope or shape misses are surface_drift only
three in a row on inputs never rejected before, with no parsed input between; any other miss is its
input's own and parks."""

from uuid import uuid4

import psycopg
import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.layers import gold
from mdp_functions.registry import REGISTRY, sync
from test_enrichment_runtime import inputs


async def closed_run(rt, name):
    """A gold run reads the inputs its closed cycle's manifest shows complete."""
    cadence = REGISTRY[name].cadence
    binding = await rt.cycles.bind_cycle(cadence, "global", "local:" + uuid4().hex, "scheduled", "local:" + cadence, runner="core")
    rt.cycles.close(binding["cycle_id"])
    run = rt.admit(name, dbt_run_id=binding["dbt_run_id"])
    await rt.execute(run["id"])
    return run


def attrs(rt, run, kind):
    row = rt.db.one("SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type=%s ORDER BY at DESC LIMIT 1", (run["id"], kind))
    return row["attrs"] if row else None


async def test_an_input_that_fails_on_its_own_parks_is_shown_and_is_released(rt, databases):
    inputs(databases, 3)
    name = "parked_" + uuid4().hex[:8]
    calls, dead = [], []

    @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=["raw." + name], external=True,
          input_version=["followers"], park_after=2)
    async def lookup(ctx, rows):
        ref = rows[0]["input_ref"]
        calls.append(ref)
        dead[:] = dead or [ref]
        if ref == dead[0]:
            raise ServiceError("vendor_4xx", "HTTP 404", vendor_status=404)
        yield {"value": 1, "followers": -1}

    sync(rt.db)
    try:
        runs = [await closed_run(rt, name) for _ in range(3)]
        # Run 1 tries all three, run 2 only the dead one, run 3 leaves it out: parked.
        assert calls.count(dead[0]) == 2 and len(calls) == 4, calls
        statuses = [rt.db.one("SELECT status,error_class FROM control.run WHERE id=%s", (r["id"],)) for r in runs]
        assert statuses[:2] == [{"status": "partial", "error_class": "vendor_4xx"},
                               {"status": "failed", "error_class": "vendor_4xx"}]
        assert statuses[2]["status"] == "succeeded", statuses
        assert attrs(rt, runs[0], "inputs_rejected")["parkable"][0]["input_ref"] == dead[0]
        assert attrs(rt, runs[2], "input_snapshot")["parked"] == 1
        assert attrs(rt, runs[2], "inputs_parked") == {"parked": 1, "input_refs": [dead[0]]}
        alert = rt.db.one("SELECT id,severity,resolved_at FROM control.alert WHERE run_id=%s AND class='inputs_parked'", (runs[2]["id"],))
        assert alert["severity"] == "info" and alert["resolved_at"] is None
        # One open alert per streamline: a fourth run with the input still parked opens none.
        again = await closed_run(rt, name)
        assert attrs(rt, again, "input_snapshot")["parked"] == 1
        assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE class='inputs_parked' AND resolved_at IS NULL")["n"] == 1
        # Releasing it (what streamlines.unpark does) returns the input to the next read.
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("UPDATE control.alert SET resolved_at=now() WHERE id=%s", (alert["id"],))
        released = await closed_run(rt, name)
        assert attrs(rt, released, "input_snapshot")["parked"] == 0 and calls[-1] == dead[0]
    finally:
        REGISTRY.pop(name)


@pytest.mark.parametrize("miss", ["envelope", "shape"])
async def test_an_envelope_or_shape_miss_trips_surface_drift_ends_the_reads_and_parks_nothing(rt, databases, miss):
    inputs(databases, 6)
    name = "drift_" + uuid4().hex[:8]
    calls = []

    @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=["raw." + name], external=True,
          input_version=["followers"], park_after=1)
    async def lookup(ctx, rows):
        calls.append(rows[0]["input_ref"])
        # The page state changed: every input misses its envelope, as releases.missing() records it,
        # or its record lost a field, as a parser's drift: reject records it.
        if miss == "shape":
            ctx.reject(rows[0], reason="drift:lookup:followers")
            return
        try:
            ctx.require({}, "entities.items", name)
        except ServiceError as exc:
            assert exc.error_class == "envelope_mismatch"
        return
        yield {}

    sync(rt.db)
    try:
        run = await closed_run(rt, name)
        # Three distinct misses end the reads: the other three inputs wait, unrejected.
        assert len(calls) == 3
        assert rt.db.one("SELECT status,error_class FROM control.run WHERE id=%s", (run["id"],)) == {
            "status": "failed", "error_class": "surface_drift"}
        assert attrs(rt, run, "inputs_rejected")["parkable"] == []
        assert rt.db.one("SELECT severity FROM control.alert WHERE run_id=%s AND class='surface_drift'", (run["id"],))["severity"] == "critical"
        # The critical alert pauses the streamline (the circuit) until an operator enables it again.
        assert rt.db.one("SELECT enabled FROM control.streamline WHERE source_key=%s", (name,))["enabled"] is False
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("UPDATE control.streamline SET enabled=true WHERE source_key=%s", (name,))
        assert sorted(k["input_ref"] for k in attrs(rt, run, "inputs_rejected")["drift"]) == sorted(calls)
        # park_after=1, yet nothing parked. The next run reads all six again: a miss on an input
        # rejected before is that input's own (it parks), and three fresh misses in a row are still
        # drift, so a page state that stays changed pauses the streamline again.
        later = await closed_run(rt, name)
        assert attrs(rt, later, "input_snapshot")["parked"] == 0 and len(calls) == 9
        assert rt.db.one("SELECT error_class FROM control.run WHERE id=%s", (later["id"],))["error_class"] == "surface_drift"
        rejected = attrs(rt, later, "inputs_rejected")
        assert sorted(k["input_ref"] for k in rejected["parkable"]) == sorted(calls[:3])
        assert sorted(k["input_ref"] for k in rejected["drift"]) == sorted(calls[6:])
        assert rt.db.one("SELECT enabled FROM control.streamline WHERE source_key=%s", (name,))["enabled"] is False
    finally:
        REGISTRY.pop(name)


async def test_scattered_missing_entity_pages_are_each_tracks_own_and_never_pause(rt, databases):
    """Three pages without their entity among parsed ones: no surface_drift and no pause. Each is its
    input's own failure; once the others complete they head the read together, and still count as
    their own (rejected before), so they park after park_after runs instead of pausing the rail."""
    inputs(databases, 8)
    name = "entity_" + uuid4().hex[:8]
    calls, dead = [], set()

    @gold(source_key=name, reads=["marts.mart_enrichment_fixture"], writes=["raw." + name], external=True,
          input_version=["followers"], park_after=2)
    async def lookup(ctx, rows):
        ref = rows[0]["input_ref"]
        calls.append(ref)
        if len(calls) in (1, 3, 6):
            dead.add(ref)
        if ref in dead:
            # releases.missing(): the page rendered without this track's entity.
            try:
                ctx.require({}, "entities.items", name)
            except ServiceError:
                return
        yield {"value": 1, "followers": -1}

    def state(run):
        return rt.db.one("SELECT status,error_class FROM control.run WHERE id=%s", (run["id"],))

    def paused():
        return rt.db.one("SELECT enabled FROM control.streamline WHERE source_key=%s", (name,))["enabled"] is False

    def drift_alerts():
        return rt.db.one("SELECT count(*) AS n FROM control.alert a JOIN control.run r ON r.id=a.run_id "
                         "JOIN control.streamline s ON s.id=r.streamline_id WHERE s.source_key=%s AND a.class='surface_drift'",
                         (name,))["n"]

    sync(rt.db)
    try:
        first = await closed_run(rt, name)
        # Miss, parse, miss, parse, parse, miss, parse, parse: every streak is broken by a parse.
        assert len(calls) == 8 and len(dead) == 3
        assert state(first) == {"status": "partial", "error_class": "input_rejected"}
        rejected = attrs(rt, first, "inputs_rejected")
        assert {k["input_ref"] for k in rejected["parkable"]} == dead and rejected["drift"] == []
        assert not paused() and drift_alerts() == 0
        # Only the three remain pending, one after another: rejected before, so still their own.
        second = await closed_run(rt, name)
        assert calls[8:] and set(calls[8:]) == dead and len(calls) == 11
        assert state(second) == {"status": "failed", "error_class": "input_rejected"}
        assert {k["input_ref"] for k in attrs(rt, second, "inputs_rejected")["parkable"]} == dead
        assert not paused() and drift_alerts() == 0
        # Rejected in two runs: parked, so the third read leaves them out.
        third = await closed_run(rt, name)
        assert attrs(rt, third, "input_snapshot")["parked"] == 3 and len(calls) == 11
        assert not paused() and drift_alerts() == 0
    finally:
        REGISTRY.pop(name)


def test_park_after_is_a_positive_gold_declaration():
    from mdp_functions.layers import silver
    from mdp_functions.registry import discover

    with pytest.raises(ServiceError, match="park_after"):
        @silver(source_key="park_" + uuid4().hex[:8], reads=["marts.mart_enrichment_fixture"], writes=["raw.x"], park_after=2)
        async def reshape(ctx, rows):
            yield {}
    assert discover()["sp_track_artists"].park_after == 3
