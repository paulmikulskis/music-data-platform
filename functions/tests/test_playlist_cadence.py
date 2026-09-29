"""Tests for playlist cadence."""


import csv
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from mdp_functions import playlist, playlist_targets
from mdp_functions.http import FixtureTransport
from mdp_functions.layers import Ctx
from mdp_functions.playlist_targets import spec_hash, target_spec
from mdp_functions.registry import discover
from playlist_collector_fixture import REPO, ROOT, TARGETS, SeedConn, run, target


@pytest.mark.parametrize(
    ("daily", "weekly"),
    [
        ("am_playlist", "am_playlist_weekly"),
        ("sp_playlist", "sp_playlist_weekly"),
        ("sc_playlist", "sc_playlist_weekly"),
        ("sc_curator_playlists", "sc_curator_playlists_weekly"),
        ("bc_discover", "bc_discover_weekly"),
        ("bc_daily_list", "bc_daily_list_weekly"),
        ("bc_radio", "bc_radio_weekly"),
        ("bc_fan_playlist", "bc_fan_playlist_weekly"),
    ],
)
async def test_each_cadence_key_fetches_only_its_frozen_members(daily, weekly):
    catalog = discover()
    # Weekly sources run every day and serve the weekly bucket due that day.
    assert catalog[daily].cadence == catalog[weekly].cadence == "daily"
    assert catalog[daily].function is not catalog[weekly].function
    assert catalog[daily].writes == catalog[weekly].writes
    today = datetime.now(timezone.utc).weekday()
    for key, frozen, bucket in (
        (daily, "weekly", today),
        (weekly, "daily", today),
        (weekly, "weekly", (today + 1) % 7),
    ):
        make = TARGETS.get(daily)
        member = (
            make()
            if make
            else target(
                "apple_music" if daily == "am_playlist" else "spotify",
                ("us:pl.x" if daily == "am_playlist" else "US:Synth1531769ee345db9a2"),
            )
        )
        member["params_json"] = {
            **(member.get("params_json") or {}),
            "cadence": frozen,
            "weekday_bucket": bucket,
        }
        ctx, calls = await run(
            key,
            batch=[member],
            transport=httpx.MockTransport(lambda r: pytest.fail(str(r.url))),
        )
        assert not calls and not ctx.outputs and ctx.observed_count == 0


def test_missing_frozen_cadence_stays_daily():
    assert playlist.frozen_cadence(target("spotify", "US:x")) == "daily"
    assert (
        playlist.frozen_cadence(target("spotify", "US:x", cadence="weekly")) == "weekly"
    )


def test_target_probe_checks_weekly_members_before_their_due_day():
    weekly = type("Ctx", (), {"manifest": discover()["sp_playlist_weekly"], "is_probe": True})()
    monday = datetime(2026, 9, 21, tzinfo=timezone.utc)
    member = target("spotify", "US:x", cadence="weekly", weekday_bucket=3)
    assert playlist.cadence_matches(weekly, member, monday)
    assert not playlist.cadence_matches(weekly, target("spotify", "US:y", cadence="daily"), monday)


def test_weekly_targets_spread_by_frozen_weekday_bucket():
    weekly = type("Ctx", (), {"manifest": discover()["sp_playlist_weekly"]})()
    daily = type("Ctx", (), {"manifest": discover()["sp_playlist"]})()
    monday = datetime(2026, 9, 21, tzinfo=timezone.utc)
    members = [target("spotify", f"US:{n}", cadence="weekly") for n in range(700)]
    due = [
        sum(
            playlist.cadence_matches(weekly, m, monday + timedelta(days=d))
            for m in members
        )
        for d in range(7)
    ]
    # Every weekly target is due exactly one day a week, and the days share the load.
    assert sum(due) == 700 and min(due) > 60, due
    assert not any(playlist.cadence_matches(daily, m, monday) for m in members)
    frozen = target("spotify", "US:x", cadence="weekly", weekday_bucket=3)
    assert playlist.weekday_bucket(frozen) == 3
    assert playlist.cadence_matches(weekly, frozen, monday + timedelta(days=3))
    # A spec frozen before the bucket falls back to the same hash the seed freezes.
    loose = target("spotify", "US:y", cadence="weekly")
    digest = int(hashlib.sha1(str(loose["id"]).encode()).hexdigest(), 16) % 7
    assert playlist.weekday_bucket(loose) == digest


async def test_weekly_source_fetches_todays_bucket():
    member = TARGETS["bc_discover"]()
    member["params_json"] = {
        **member["params_json"],
        "cadence": "weekly",
        "weekday_bucket": datetime.now(timezone.utc).weekday(),
    }
    ctx, calls = await run(
        "bc_discover_weekly",
        batch=[member],
        transport=FixtureTransport([ROOT / "bc_discover/fixtures/normal.jsonl"]),
    )
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert len(calls) == 1 and snapshot["cadence"] == "weekly"


def test_seed_freezes_weekday_bucket_and_render_cadence():
    conn = SeedConn()
    playlist_targets.seed_playlist_targets(conn, REPO / "inputs/playlist_seed.csv")
    for target_id, canonical, params in conn.specs:
        if params["cadence"] == "weekly":
            assert params["weekday_bucket"] == playlist.weekday_bucket(
                {"id": target_id}
            )
        else:
            assert "weekday_bucket" not in params
        if canonical.startswith(("sp:", "am:")):
            assert params["render_cadence"] == "weekly"
    assert sum(p["cadence"] == "weekly" for _, _, p in conn.specs) == 2


def test_seed_targets_freeze_cadence_and_canonical_keys():
    with (REPO / "inputs/playlist_seed.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    platforms = {r["platform"] for r in rows}
    for row in rows:
        canonical, params = target_spec(row)
        assert params["cadence"] in ("daily", "weekly"), row
        if params.get("owner_class") in ("editorial", "chart", "dsp_algorithmic"):
            assert params["cadence"] == "daily", row
        if params.get("owner_class") in ("curator", "user"):
            assert params["cadence"] == "weekly", row
        if row["platform_account_id"].startswith("discover:"):
            assert row["platform_account_id"] == "discover:" + spec_hash(params["spec"])
        assert canonical.split(":")[0] in ("am", "sp", "sc", "bc")
    count = {p: sum(r["platform"] == p for r in rows) for p in platforms}
    kinds = [
        r["platform_account_id"].split(":")[0]
        for r in rows
        if r["platform"] == "bandcamp"
    ]
    assert kinds.count("discover") == 1 and kinds.count("daily") == 1
    assert kinds.count("radio") == 1 and count["soundcloud"] == 1


def test_weekly_bucket_comes_from_the_bound_cycle_not_the_clock():
    manifest = discover()["sp_playlist_weekly"]
    opened = datetime(2026, 9, 21, 23, 59, tzinfo=timezone.utc)  # a Monday
    ctx = Ctx(manifest, {"id": uuid4(), "cycle_id": uuid4(), "cycle_opened_at": opened})
    monday = target("spotify", "US:m", cadence="weekly", weekday_bucket=0)
    tuesday = target("spotify", "US:t", cadence="weekly", weekday_bucket=1)
    # However late the batch runs, it serves the bucket of the day its cycle opened.
    assert playlist.cadence_matches(ctx, monday)
    assert not playlist.cadence_matches(ctx, tuesday)
