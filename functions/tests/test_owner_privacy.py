"""Owner privacy at parse: only an owner observed to be the platform's own account lands a name; a
target's label never counts. Every collector validates its header through `assemble`, so the rule
holds for Apple, Spotify, SoundCloud and Bandcamp alike; staging pseudonymises the ids of the rest
from the same evidence (playlist_dbt_fixture.py)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from mdp_functions.playlist import PUBLIC_OWNERS, assemble, parse_apple, parse_page
from playlist_collector_fixture import run

COMMON = {
    "platform": "spotify",
    "playlist_id": "Synth1531769ee345db9a2",
    "variant": "US",
    "stream": "full",
    "snapshot_id": "snapshot",
    "observed_at": datetime(2026, 9, 23, tzinfo=timezone.utc),
    "fetch_surface": "sp_playlist_page",
}


def landed(header, label=None):
    result = assemble(
        COMMON,
        "https://example.test/list",
        [],
        header,
        lambda raw: pytest.fail("no rows"),
        lambda raw: pytest.fail("no rows"),
        owner_class=label,
    )
    snapshot = result.snapshot
    # The observed class lands beside the label; every owner decision reads it.
    assert snapshot["owner_class_observed"] == header()["owner_class"]
    return snapshot["owner_class"], snapshot["owner_name"], snapshot["owner_id"]


def observed(owner_class):
    def header():
        return {
            "title": "A list",
            "owner_id": "owner-1",
            "owner_name": "A Person",
            "owner_class": owner_class,
            "track_count_reported": 0,
            "coverage": "full",
        }

    return header


def test_public_owners_are_the_platforms_own_lists():
    assert PUBLIC_OWNERS == {"editorial", "chart", "dsp_algorithmic"}


@pytest.mark.parametrize(
    ("parsed", "label", "named"),
    [
        ("editorial", None, True),
        ("chart", None, True),
        ("dsp_algorithmic", None, True),
        ("user", None, False),
        ("unknown", None, False),
        ("curator", None, False),
        # The observed owner decides; a target's label never keeps a name.
        ("user", "curator", False),
        ("user", "editorial", False),
        ("unknown", "chart", False),
        ("editorial", "user", True),
    ],
)
def test_only_an_observed_platform_owner_keeps_a_name(parsed, label, named):
    owner_class, name, owner_id = landed(observed(parsed), label)
    assert owner_class == (label or parsed)
    assert name == ("A Person" if named else None)
    # The id lands; staging pseudonymises it for every other owner.
    assert owner_id == "owner-1"


def spotify_page(username, name):
    return {
        "entities": {
            "items": {
                "spotify:playlist:" + COMMON["playlist_id"]: {
                    "name": "A list",
                    "followers": 10,
                    "content": {"items": [], "totalCount": 0},
                    "ownerV2": {"data": {"username": username, "name": name}},
                }
            }
        }
    }


@pytest.mark.parametrize(
    ("username", "name", "named"),
    [("spotify", "Spotify", True), ("jane-doe", "Jane Doe", False)],
)
def test_spotify_names_only_its_own_account_whatever_the_label(username, name, named):
    root = spotify_page(username, name)
    owner_class, landed_name, owner_id = landed(
        lambda: parse_page(root, COMMON["playlist_id"]), label="editorial"
    )
    assert owner_class == "editorial" and owner_id == username
    assert landed_name == (name if named else None)


def apple_root(link):
    header = {"title": "A list", "trackCount": 0, "subtitleLinks": [link]}
    return {
        "data": [
            {
                "data": {
                    "sections": [
                        {"itemKind": "containerDetailHeaderLockup", "items": [header]},
                        {"id": "track-list - pl.1fa57a04cd794a8aa482a3492f26fbcd", "items": []},
                    ]
                }
            }
        ]
    }


def link(title, kind, identifier):
    descriptor = {"kind": kind, "identifiers": {"storeAdamID": identifier}}
    return {"title": title, "segue": {"destination": {"contentDescriptor": descriptor}}}


@pytest.mark.parametrize(
    ("subtitle", "parsed", "named"),
    [
        (link("Fixture item 34517", "appleCurator", "1526756058"), "editorial", True),
        # Apple's curator kind decides, never the display name.
        (link("Up Next", "appleCurator", "7"), "editorial", True),
        (link("Apple Music Fan", "socialProfile", "4"), "user", False),
    ],
)
def test_apple_names_only_its_own_curator_kind(subtitle, parsed, named):
    root = apple_root(subtitle)
    dated = '<script type="application/ld+json">{"datePublished": "2026-09-18T04:18:11Z"}</script>'
    assert parse_apple(root, BeautifulSoup(dated, "html.parser"))["owner_class"] == parsed
    _, name, _ = landed(
        lambda: parse_apple(root, BeautifulSoup(dated, "html.parser")), label="curator"
    )
    assert name == (subtitle["title"] if named else None)


async def test_listed_candidates_land_no_owner_name():
    ctx, _ = await run("sc_hubs")
    candidates = ctx.outputs["raw.playlist_candidates"]
    assert candidates and all(c["hint_owner_name"] is None for c in candidates)
    assert any(c["hint_owner_id"] for c in candidates)


async def test_bandcamp_daily_names_the_column_never_the_byline():
    ctx, _ = await run("bc_daily_list")
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["owner_name"] == "Bandcamp Daily"
    assert snapshot["owner_class_observed"] == "editorial"


SHAPES = json.loads((Path(__file__).parent / "fixtures/apple_owner_shapes.json").read_text())["shapes"]


def apple_page(shape):
    """A structure-only Apple playlist page: the header, two track rows, and the JSON-LD date."""
    header = {
        "title": shape["case"],
        "trackCount": 2,
        "subtitleLinks": shape["links"],
        "contentDescriptor": {"kind": "playlist", "identifiers": {"storeAdamID": shape["id"]}},
    }
    rows = [{"id": f"t{n}", "title": f"Track {n}", **({"rankingText": str(n)} if shape["ranked"] else {})} for n in (1, 2)]
    root = {"data": [{"data": {"sections": [
        {"id": "playlist-detail-header-section - " + shape["id"], "itemKind": "containerDetailHeaderLockup", "items": [header]},
        {"id": "track-list - " + shape["id"], "itemKind": "trackLockup", "items": rows},
    ]}}]}
    ld = {"@type": "MusicPlaylist", "name": shape["case"]}
    if shape["date_published"]:
        ld["datePublished"] = shape["date_published"]
    soup = BeautifulSoup(f'<script type="application/ld+json">{json.dumps(ld)}</script>', "html.parser")
    return root, soup


@pytest.mark.parametrize("shape", SHAPES, ids=[s["case"] for s in SHAPES])
def test_apple_classes_its_own_charts_and_lists_by_payload(shape):
    root, soup = apple_page(shape)
    assert parse_apple(root, soup)["owner_class"] == shape["expected"]
    owner_class, name, _ = landed(lambda: parse_apple(root, soup))
    assert owner_class == shape["expected"]
    # Only the evidence link names the owner: the curator's or the bare "Apple Music" link's name.
    assert name == shape["name"], name
