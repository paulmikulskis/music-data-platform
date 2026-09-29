"""Tests for kexp."""


import json
from uuid import uuid4

import httpx
import pytest
from free_source_fixture import (
    ROOT,
    WINDOW,
    dbt_models,
    kexp_page,
    registry,
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions.errors import ServiceError
from mdp_functions.http import FixtureTransport, TracedClient
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from test_fetch_runtime import FakeDB, no_page


def test_kexp_ships_disabled_unverified_and_learn_false_with_no_review_on_file():
    manifest, row = discover()["kexp_plays"], registry()["kexp_plays"]
    assert manifest.knobs["enabled"] is False and manifest.hosts == ["api.kexp.org"] and not manifest.tenant_bound
    assert (row["license_ref"], row["learning_eligible"], row["resale_permitted"], row["review_ref"]) == (
        "unverified", "false", "false", "")


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_kexp_lands_track_plays_without_peoples_words(scenario, monkeypatch):
    from mdp_functions import kexp

    monkeypatch.setattr(kexp, "backfill_guard", lambda ctx, start, end: True)
    manifest = discover()["kexp_plays"]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None,
           "backfill_window": WINDOW}
    ctx = Ctx(manifest, run)
    refused = []
    async with TracedClient(ctx, FakeDB(), run, no_page,
                            transport=FixtureTransport([ROOT / "kexp_plays" / "fixtures" / f"{scenario}.jsonl"])) as client:
        ctx.http = client
        try:
            async for row in manifest.function(ctx):
                ctx.yield_row(row)
        except ServiceError as exc:
            refused.append(exc.error_class)
    rows = ctx.outputs.get("raw.radio_plays", [])
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())
    assert "PLACEHOLDER" not in json.dumps([rows, ctx.rejected], default=str)
    for row in rows:
        kexp.RadioPlay.model_validate(row)
        assert not {"comment", "show", "location_name", "host"} & set(row)
    if scenario == "drift":
        assert not rows and [r["reason"] for r in ctx.rejected] == ["envelope_mismatch:kexp_plays:results"]
        return
    if scenario == "not_found":
        assert refused == ["stale_target"] and not rows
        return
    assert [(r["play_id"], r["rotation_status"], r["is_local"]) for r in rows] == [
        (3700001, "Heavy", False), (3700002, "Light", True), (3700004, None, False)]
    assert rows[0]["recording_mbid"] == "00000000-0000-4000-b000-000000000001" and rows[2]["recording_mbid"] is None
    assert json.loads(rows[0]["label_mbids"]) == ["00000000-0000-4000-7000-000000000701"]
    reasons = [r["reason"] for r in ctx.rejected]
    assert reasons == ([] if scenario == "normal" else ["drift:kexp_plays:play"])
    assert ctx.exclusions == {"not_a_trackplay": 1}
    # A backfill window never moves the daily watermark.
    assert not ctx.pending_cursors


async def test_kexp_daily_reads_from_its_watermark_and_advances_it(monkeypatch):

    manifest = discover()["kexp_plays"]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    ctx = Ctx(manifest, run, cursors={"": {"cursor_value": {"airdate": "2026-09-22T10:00:00+00:00"}}})
    seen = []

    def station(request):
        seen.append(request)
        return httpx.Response(200, json={"next": None, "results": [
            {"id": 1, "airdate": "2026-09-22T09:30:00Z", "play_type": "trackplay", "artist_ids": [], "label_ids": []},
            {"id": 2, "airdate": "2026-09-22T11:00:00Z", "play_type": "trackplay", "artist_ids": [], "label_ids": []}]})

    async with TracedClient(ctx, FakeDB(), run, no_page, transport=httpx.MockTransport(station)) as client:
        ctx.http = client
        rows = [row async for row in manifest.function(ctx)]
    # Two hours of overlap before the watermark catch late-logged plays; staging keeps one row per play.
    assert seen[0].url.params["airdate_after"] == "2026-09-22T08:00:00Z" and len(rows) == 2
    assert seen[0].url.params["ordering"] == "airdate"
    assert ctx.pending_cursors[""] == {"airdate": "2026-09-22T11:00:00+00:00"}


@pytest.mark.parametrize("order", ["oldest_first", "newest_first"])
async def test_a_failed_second_kexp_page_leaves_the_watermark_at_the_plays_read(order):
    """Page one lands and page two answers 503. Oldest first, the watermark stands at page one's newest play,
    so the retry rereads page two; a server that ignores `ordering=airdate` and answers newest first leaves
    the watermark where it was, since older plays may still be unread."""

    manifest = discover()["kexp_plays"]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    ctx = Ctx(manifest, run, cursors={"": {"cursor_value": {"airdate": "2026-09-22T10:00:00+00:00"}}})
    first = [(1, "2026-09-22T09:00:00Z"), (2, "2026-09-22T12:00:00Z")]
    if order == "newest_first":
        first.reverse()
    seen = []

    def station(request):
        seen.append(request)
        if request.url.params.get("offset"):
            return httpx.Response(503)
        return httpx.Response(200, json=kexp_page(first, f"{request.url}&offset=100"))

    rows = []
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=httpx.MockTransport(station)) as client:
        ctx.http = client
        with pytest.raises(ServiceError) as caught:
            async for row in manifest.function(ctx):
                rows.append(row)
    assert caught.value.error_class == "vendor_retryable" and len(rows) == 2
    assert all(r.url.params["ordering"] == "airdate" for r in seen)
    if order == "oldest_first":
        assert ctx.pending_cursors[""] == {"airdate": "2026-09-22T12:00:00+00:00"}
    else:
        assert "" not in ctx.pending_cursors


@pytest.mark.parametrize("window", [
    {"from": "2025-09-01T00:00:00+00:00", "to": "2026-09-22T00:00:00+00:00"},
    {"from": "2024-01-01T00:00:00+00:00", "to": "2024-06-01T00:00:00+00:00"},
    {"from": "2026-09-22T00:00:00+00:00", "to": "2026-09-22T00:00:00+00:00"},
])
async def test_a_kexp_backfill_window_longer_than_a_year_or_older_than_two_is_refused(window):
    from types import SimpleNamespace

    from mdp_functions import kexp

    manifest = discover()["kexp_plays"]
    ctx = Ctx(manifest, {"id": str(uuid4()), "cycle_id": str(uuid4()), "backfill_window": window})
    ctx.http = SimpleNamespace(settings=None, db=None)
    with pytest.raises(ServiceError) as caught:
        async for _ in kexp.plays(ctx):
            pass
    assert caught.value.error_class == "backfill_window_refused"


async def test_a_kexp_backfill_window_projects_its_write_and_stops_past_the_line(rt, databases, monkeypatch):
    run = rt.backfill("kexp_plays", window=WINDOW)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    # The air break stays counted as an expected exclusion.
    assert (result["status"], result["rows_written"], result["rows_rejected"]) == ("succeeded", 3, 0), result
    assert rt.receipts(run["id"])["receipts"][0]["row_rejection_share"] == 0
    assert rt.receipts(run["id"])["receipts"][0]["row_exclusions"] == {"not_a_trackplay": 1}
    projected = rt.db.one("SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='kexp_backfill_projected'", (run["id"],))["attrs"]
    assert projected["projected_plays"] == 400 and not projected["stop"] and projected["limit_bytes"] == int(20 * 10**9 * 0.6)
    monkeypatch.setattr(rt, "settings", rt.settings.model_copy(update={"pgdata_volume_bytes": 10**6}))
    stopped = rt.backfill("kexp_plays", window=WINDOW)
    await rt.execute(stopped["id"])
    assert rt.db.one("SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s", (stopped["id"],))["n"] == 0
    assert [a["class"] for a in rt.db.all("SELECT class FROM control.alert WHERE run_id=%s", (stopped["id"],))] == ["warehouse_disk_high"]


def test_kexp_rotation_is_available_as_an_operator_model():
    descendants = dbt_models("--select", "stg_kexp__plays+")
    assert set(descendants) == {"stg_kexp__plays", "int_radio__rotation_events", "mart_radio_rotation"}
    assert not [name for name, model in descendants.items() if model["config"].get("meta", {}).get("grain")]
