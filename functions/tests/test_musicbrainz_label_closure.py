"""Tests for musicbrainz label closure."""


import json

import psycopg
from conftest import bound
from free_source_fixture import (
    REPO,
)
from free_source_fixture import (
    label_mirror as label_mirror,  # noqa: PLC0414 - pytest fixture
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions import reference
from test_identity_functions import CATALOG, tracked


def test_reference_and_social_urls_parse_and_never_seed():
    from mdp_functions.musicbrainz import TRACKED_URL_SQL, platform_url

    assert platform_url("https://www.wikidata.org/wiki/Q9000002") == ("wikidata", "entity", "Q9000002")
    assert platform_url("https://www.discogs.com/artist/123-Fixture-Act") == ("discogs", "artist", "123")
    assert platform_url("https://www.youtube.com/channel/UCfixtureActOne000000001") == ("youtube", "channel", "UCfixtureActOne000000001")
    assert platform_url("https://www.youtube.com/@fixtureact") == ("youtube", "profile", "fixtureact")
    assert platform_url("https://www.tiktok.com/@fixture_act") == ("tiktok", "profile", "fixture_act")
    assert platform_url("https://www.instagram.com/p/C0post/") is None and platform_url("https://www.discogs.com/release/1") is None
    # The mirror computes mdp.tracked_url from the same pattern at import.
    assert f"SELECT '{TRACKED_URL_SQL}'::text" in (REPO / "ops/fly/mb-db/mdp-schema.sql").read_text()


async def test_mb_spine_lands_the_label_closure_to_its_root_owner(rt, databases, label_mirror, monkeypatch):
    monkeypatch.setenv("MDP_MB_DB_URL", label_mirror["url"])
    tracked(databases, CATALOG)
    _, run = await bound(rt, "mb_spine")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        def rows(sql):
            return conn.execute(sql + " WHERE _run_id=%s ORDER BY 1, 2", (run["id"],)).fetchall()
        labels = rows("SELECT label_id, name FROM raw.mb_label")
        affiliations = rows("SELECT link_id, artist_id, label_id, link_type, begin_year, begin_month, begin_day, end_year, ended FROM raw.mb_l_artist_label")
        label_links = rows("SELECT label_id0, label_id1, link_type FROM raw.mb_l_label_label")
        codes = rows("SELECT artist_id, ipi FROM raw.mb_artist_ipi") + rows("SELECT artist_id, isni FROM raw.mb_artist_isni")
        (counts,) = conn.execute("SELECT mirror_counts FROM raw.mb_generation WHERE _run_id=%s", (run["id"],)).fetchone()
    # Artist 2's contracted labels, then parents, the distributor and the rename's successor up to the root;
    # never a subsidiary below the root (706) or a label an employment type names (707).
    assert labels == [(701, "Fixture Imprint"), (702, "Fixture Records"), (703, "Fixture Music Group"),
                      (704, "Fixture Distribution"), (705, "Fixture Old Records")]
    assert affiliations == [(1, 2, 701, "recording contract", 2019, 3, 1, None, False),
                            (2, 2, 705, "recording contract", 2012, None, None, 2018, True)]
    assert sorted(label_links) == [(702, 701, "imprint"), (703, 702, "label ownership"),
                                   (704, 702, "label distribution"), (705, 702, "label rename")]
    assert codes == [(2, "00000000001"), (2, "0000000123456789")]
    counts = json.loads(counts)
    assert (counts["raw.mb_label"], counts["raw.mb_l_artist_label"], counts["raw.mb_l_label_label"]) == (5, 2, 4)
    # The prepare step measured the closure trigger; the probe copies it to the Reference page.
    prepared = rt.db.one("SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='mb_spine_prepared'", (run["id"],))["attrs"]
    assert prepared["tracked_recordings"] >= 0 and prepared["projected_write_share"] is not None
    assert prepared["narrowing_due"] is False
    reference.probe(rt.db, databases["service_read_url"], label_mirror["url"])
    source = rt.db.one("SELECT closure_write_share, closure_recordings, closure_measured_at FROM control.reference_source WHERE source='musicbrainz'")
    assert source["closure_write_share"] == prepared["projected_write_share"]
    assert source["closure_recordings"] == prepared["tracked_recordings"] and source["closure_measured_at"] is not None
    # An mb_resolve answer never carries the extension tables.
    from mdp_functions.musicbrainz import EXTENSION, MbResolveClosure

    assert not {"label_gid", "label_id0", "ipi", "isni"} & set(MbResolveClosure.model_fields)
    # Retention and the volume guard cover the extension tables.
    assert all(t.removeprefix("raw.") in reference.MB_TABLES for t in EXTENSION)


def test_the_closure_trigger_crosses_at_either_line():
    assert (reference.NARROW_SHARE, reference.NARROW_RECORDINGS) == (0.3, 100_000)
