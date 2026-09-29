"""Acceptance for the identity chain, on the non-local Postgres target with real close
stamps: every build binds a crafted closed cycle, so each manifest filter reads its own close_no."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from identity_harness import Warehouse, ident

pytestmark = pytest.mark.docker

HOURLY = "int_reference__current int_identity__track_inputs int_identity__resolution_inputs int_identity__crosswalk_inputs int_track_identity"
DAILY = "int_track_identity__daily int_identity__audit int_artist_identity +mart_playlist_events +mart_editorial_entries +mart_track_playlist_followers +mart_track_playlist_followers_by_variant"
DAY = datetime(2026, 9, 21, 8, tzinfo=UTC)
G1, G2, G3, G4 = "20260923-002121", "20261021-001500", "20261118-001500", "20261216-001500"
SP_ARTIST = "SynthArtist00000000001"


def reference_rows(drop_isrc: int | None = None) -> dict[str, list[dict]]:
    rec = lambda i: "00000000-0000-4000-b000-000000000" + str(i)
    urls = [
        ("recording", 1, 101, "https://open.spotify.com/track/SynthTrack000000000001", "spotify", "track", "SynthTrack000000000001"),
        ("recording", 2, 108, "https://music.apple.com/us/song/1600000108", "apple_music", "track", "1600000108"),
        ("release", 1, 201, "https://open.spotify.com/album/SynthAlbum000000000001", "spotify", "album", "SynthAlbum000000000001"),
        ("artist", 1, 2, f"https://open.spotify.com/artist/{SP_ARTIST}", "spotify", "artist", SP_ARTIST),
    ]
    return {
        "url_link": [{"mb_key": f"{e}:{i}", "entity_type": e, "link_id": i, "entity_id": ent, "url_id": 1000 + n, "url": u,
                      "link_type": "free streaming", "ended": False, "url_platform": p, "url_kind": k, "url_platform_id": pid}
                     for n, (e, i, ent, u, p, k, pid) in enumerate(urls)],
        "isrc": [{"mb_key": str(i), "isrc_id": i, "isrc": isrc, "recording_id": r}
                 for i, isrc, r in [(1, "USXXX2600001", 101), (2, "USXXX2600002", 102), (3, "USXXX2600003", 108)] if i != drop_isrc],
        "recording": [{"mb_key": str(i), "recording_id": i, "recording_gid": rec(i), "name": n, "artist_credit_id": ac,
                       "length_ms": ln, "video": False}
                      for i, n, ac, ln in [(101, "Fixture item 56802", 1, 202460), (102, "Synthetic Song Alpha", 1, 200455), (108, "Synthetic Song Gamma", 2, 239560)]],
        "redirect": [{"mb_key": "recording:" + rec(109), "entity_type": "recording", "gid": rec(109), "new_id": 101}],
        "artist": [{"mb_key": "2", "artist_id": 2, "artist_gid": "00000000-0000-4000-a000-000000000002", "name": "Fixture Artist Beta",
                    "sort_name": "Beta, Fixture Artist", "type_id": 1}],
    }


def tracks():
    return {
        "bass": {"id": "SynthTrack000000000001", "title": "Fixture item 56802", "artists": ["Fixture Artist Alpha"], "duration": 202460},
        "flowers": {"id": "FLW0000000000000000001", "title": "Synthetic Song Alpha", "artists": ["Fixture Artist Alpha"], "duration": 200400},
        "hero": {"id": "HER0000000000000000001", "title": "Synthetic Song Beta", "artists": ["Fixture Artist Beta"], "duration": 200690},
        "embed_x": {"id": "EMBX000000000000000001", "title": "Relinked", "artists": ["Someone"], "duration": 150000},
        "nonprio": {"id": "NOP0000000000000000001", "title": "Obscure", "artists": ["Nobody"], "duration": 100000},
        "late": {"id": "LATE000000000000000001", "title": "Late Snapshot", "artists": ["Late"], "duration": 120000},
    }


def page(t, **kw):
    return {**t, **kw}


@pytest.fixture(scope="module")
def wh(tmp_path_factory):
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    warehouse = Warehouse(admin, tmp_path_factory.mktemp("schemas"))
    seed = warehouse.cycle("daily", 0, DAY - timedelta(days=2))
    warehouse.dbt(seed, "identity_confidence_floors rights_registry source_priority track_candidates_fixture",
                  command="seed")
    yield warehouse
    warehouse.drop()


def rows(wh, relation, where="true", columns="*"):
    return wh.query(f"SELECT {columns} FROM {relation} WHERE {where} ORDER BY 1")


def resolution(t, fields_hash, reference_version, retry_week, **result):
    return {"platform": t.get("platform", "spotify"), "platform_track_id": t["id"], "status": "resolved", "method": "mb_release",
            "recording_id": 102, "recording_gid": "00000000-0000-4000-b000-000000000102", "isrc": "USXXX2600002",
            "isrc_count": 1, "release_id": 201, "release_gid": None, "candidate_count": 1, "confidence": 1.0,
            "evidence": "fixture", "mb_generation": G1, "mb_sequence": 189207, "lookup_ms": 1.0,
            "input_ref": ident("ref:" + t["id"]), "input_version": ident(f"{fields_hash}:{reference_version}:{retry_week}"),
            "fields_hash": fields_hash, "reference_version": reference_version, "retry_week": retry_week,
            "scope": "global", "step": "external:mb_resolve", "config_version": "1", "learning_eligible": False,
            "_source_keys": ["sp_playlist"], **result}


def test_identity_chain_is_cycle_bound(wh):
    t = tracks()
    # Close 1: generation G1, and the day-one observations. The Spotify page pairs with the embed in one
    # observation group: it enriches the matched row (artist ids) and the relinked row (album id).
    wh.generation(G1, 1, reference_rows())
    group = ident("group:edit1:1")
    wh.observation("spotify", "edit1", DAY, [t["bass"], t["flowers"], t["embed_x"]], "editorial", 1, group=group, followers=1000)
    wh.observation("spotify", "edit1", DAY + timedelta(seconds=30),
                   [t["bass"], page(t["flowers"], album="SynthAlbum000000000001"),
                    page(t["embed_x"], id="PAGX000000000000000001", album="ALB7")],
                   "editorial", 1, surface="sp_playlist_page", group=group, followers=1000)
    cur_group = ident("group:cur1:1")
    wh.observation("spotify", "cur1", DAY, [t["hero"], t["nonprio"]], "curator", 1, group=cur_group)
    wh.observation("spotify", "cur1", DAY + timedelta(seconds=30), [page(t["hero"], artist_ids=[SP_ARTIST]), t["nonprio"]],
                   "curator", 1, surface="sp_playlist_page", group=cur_group)
    wh.observation("apple_music", "amtop", DAY, [
        {"id": "1600000108", "title": "Synthetic Song Gamma", "artists": ["Fixture Artist Beta"], "duration": 239560, "album": "900"},
        {"id": "AMISRC", "title": "Synthetic Song Alpha", "artists": ["Fixture Artist Alpha"], "duration": 200455, "isrc": "USXXX2600002"},
    ], "chart", 1)
    # A snapshot stamped one close after its items.
    wh.observation("spotify", "late1", DAY, [t["late"]], "editorial", 1, snapshot_close_no=2)
    h1 = wh.cycle("hourly", 1, DAY + timedelta(hours=1))
    wh.dbt(h1, HOURLY)

    inputs = {r[0]: r for r in rows(wh, "intermediate.int_identity__track_inputs",
                                     columns="platform_track_id, curated, platform_album_id, platform_artist_ids, fields_hash, first_landed_seq")}
    # Surface enrichment: the embed row takes the page's album id when titles and durations agree.
    assert inputs["EMBX000000000000000001"][2] == "ALB7"
    assert inputs["FLW0000000000000000001"][2] == "SynthAlbum000000000001"
    assert SP_ARTIST in inputs["HER0000000000000000001"][3]
    assert inputs["LATE000000000000000001"][1] is False
    identity = {r[0]: r[1:] for r in rows(wh, "intermediate.int_track_identity",
                                          columns="platform_track_id, isrc, isrc_method, mb_recording_gid, recording_method")}
    # Exact ISRC and URL matches resolve in SQL, with no function call.
    assert identity["SynthTrack000000000001"] == ("USXXX2600001", "mb_url", "00000000-0000-4000-b000-000000000101", "mb_url")
    assert identity["1600000108"] == ("USXXX2600003", "mb_url", "00000000-0000-4000-b000-000000000108", "mb_url")
    assert identity["AMISRC"] == ("USXXX2600002", "platform_isrc", "00000000-0000-4000-b000-000000000102", "platform_isrc")
    resolve_inputs = {r[0] for r in rows(wh, "intermediate.int_identity__resolution_inputs", columns="platform_track_id")}
    crosswalk_inputs = {r[0] for r in rows(wh, "intermediate.int_identity__crosswalk_inputs", columns="platform_track_id")}
    # Lookups only for priority tracks SQL cannot give a recording; never for a track that is not priority.
    # The relinked page row is its own observed track id, on the same editorial playlist.
    expected = {"FLW0000000000000000001", "EMBX000000000000000001", "PAGX000000000000000001"}
    assert resolve_inputs == expected and crosswalk_inputs == expected

    # Close 2: the late snapshot meets its items; a replay of this cycle reproduces the same inputs.
    h2 = wh.cycle("hourly", 2, DAY + timedelta(hours=2))
    wh.dbt(h2, HOURLY)
    assert rows(wh, "intermediate.int_identity__track_inputs", "platform_track_id = 'LATE000000000000000001'", "curated") == [(True,)]
    incremental = rows(wh, "intermediate.int_identity__track_inputs", columns="platform_track_id, fields_hash, curated, first_landed_seq, title, artist_names, platform_artist_ids, platform_album_id, duration_ms, platform_isrc, _source_keys")
    wh.dbt(h2, HOURLY, vars_={"cycle_id": h2["id"]})
    assert rows(wh, "intermediate.int_identity__track_inputs", columns="platform_track_id, fields_hash, curated, first_landed_seq, title, artist_names, platform_artist_ids, platform_album_id, duration_ms, platform_isrc, _source_keys") == incremental
    assert "LATE000000000000000001" in {r[0] for r in rows(wh, "intermediate.int_identity__resolution_inputs", columns="platform_track_id")}

    # mb_resolve lands its result for Synthetic Song Alpha in an hourly cycle that closes (3) before the daily cycle
    # (4), but its dump commits after the daily close and is stamped at close 5.
    flowers = rows(wh, "intermediate.int_identity__resolution_inputs", "platform_track_id='FLW0000000000000000001'",
                   "fields_hash, reference_version, retry_week, input_ref, input_version")[0]
    h3 = wh.cycle("hourly", 3, DAY + timedelta(hours=3))
    result = resolution(t["flowers"], flowers[0], flowers[1], flowers[2], input_ref=flowers[3], input_version=flowers[4])
    late_dump = wh.land("raw.mb_resolve", [result], None, "mb_resolve", cycle_id=h3["id"])
    d4 = wh.cycle("daily", 4, DAY + timedelta(hours=4))
    wh.stamp(late_dump, "raw.mb_resolve", 5, "mb_resolve")
    wh.dbt(d4, DAILY)
    daily = rows(wh, "intermediate.int_track_identity__daily", "platform_track_id='FLW0000000000000000001'", "mb_recording_gid, recording_method")
    assert daily == [(None, None)]
    events = {r[0]: r[1:] for r in rows(wh, "marts.mart_playlist_events", "event_type='baseline'",
                                        "platform_track_id, isrc, mb_recording_gid")}
    # Events carry ISRC and recording where matched.
    assert events["SynthTrack000000000001"] == ("USXXX2600001", "00000000-0000-4000-b000-000000000101")
    assert events["FLW0000000000000000001"] == (None, None)
    provenance = "daily_manifest_close_no, as_of"
    expected_close = [(4, d4["closed_at"].replace(tzinfo=None))]
    assert wh.query(f"SELECT DISTINCT {provenance} FROM marts.mart_playlist_events") == expected_close
    # The next hourly cycle reads it; a daily replay after it still returns the original identities.
    h5 = wh.cycle("hourly", 5, DAY + timedelta(hours=5))
    wh.dbt(h5, "int_track_identity")
    assert rows(wh, "intermediate.int_track_identity", "platform_track_id='FLW0000000000000000001'", "recording_method, isrc") == [("mb_release", "USXXX2600002")]
    wh.dbt(d4, DAILY, vars_={"cycle_id": d4["id"]})
    assert rows(wh, "intermediate.int_track_identity__daily", "platform_track_id='FLW0000000000000000001'", "mb_recording_gid") == [(None,)]
    assert wh.query(f"SELECT DISTINCT {provenance} FROM marts.mart_playlist_events") == expected_close
    d6 = wh.cycle("daily", 6, DAY + timedelta(hours=6))
    wh.dbt(d6, DAILY)
    assert wh.query(f"SELECT DISTINCT {provenance} FROM marts.mart_playlist_events") == [
        (6, d6["closed_at"].replace(tzinfo=None))]
    assert rows(wh, "intermediate.int_track_identity__daily", "platform_track_id='FLW0000000000000000001'", "recording_method") == [("mb_release",)]
    followers = rows(wh, "marts.mart_track_playlist_followers", "platform='spotify'", "mb_recording_gid, list_count, playlist_followers")
    assert ("00000000-0000-4000-b000-000000000102", 1, 1000) in followers



def test_pick_prefers_current_fields_then_reference_then_week_then_close(wh):
    t = tracks()["nonprio"]
    fields = rows(wh, "intermediate.int_identity__track_inputs", f"platform_track_id='{t['id']}'", "fields_hash")[0][0]
    ref = rows(wh, "intermediate.int_track_identity", f"platform_track_id='{t['id']}'", "reference_version")[0][0]
    gid = lambda n: f"00000000-0000-4000-b000-00000000{n:04}"
    c11, c12, c13 = (wh.cycle("hourly", n, DAY + timedelta(hours=n)) for n in (11, 12, 13))
    # Other fields, newest of all: never read while the track's fields differ.
    wh.land("raw.mb_resolve", [resolution(t, "old-fields", ref, "2026-W45", recording_gid=gid(1))], 13, "mb_resolve", cycle_id=c13["id"])
    wh.land("raw.mb_resolve", [resolution(t, fields, ref, "2026-W40", recording_gid=gid(2))], 12, "mb_resolve", cycle_id=c12["id"])
    wh.land("raw.mb_resolve", [resolution(t, fields, ref, "2026-W41", recording_gid=gid(3))], 13, "mb_resolve", cycle_id=c13["id"])
    # A replayed older cycle lands the same week later: it never displaces the newer cycle's.
    wh.land("raw.mb_resolve", [resolution(t, fields, ref, "2026-W41", recording_gid=gid(4))], 14, "mb_resolve", cycle_id=c11["id"])
    h14 = wh.cycle("hourly", 14, DAY + timedelta(days=30))
    wh.dbt(h14, "int_track_identity")
    assert rows(wh, "intermediate.int_track_identity", f"platform_track_id='{t['id']}'", "mb_recording_gid") == [(gid(3),)]
    # A resolution whose reference_version equals the track's wins over a newer week that differs.
    wh.land("raw.mb_resolve", [resolution(t, fields, "other-reference", "2026-W43", recording_gid=gid(5))], 15, "mb_resolve", cycle_id=c13["id"])
    h15 = wh.cycle("hourly", 15, DAY + timedelta(days=31))
    wh.dbt(h15, "int_track_identity")
    assert rows(wh, "intermediate.int_track_identity", f"platform_track_id='{t['id']}'", "mb_recording_gid") == [(gid(3),)]
    # A merged-away recording follows its redirect to the current recording.
    wh.land("raw.mb_resolve", [resolution(t, fields, ref, "2026-W44", recording_gid="00000000-0000-4000-b000-000000000109")], 16,
            "mb_resolve", cycle_id=c13["id"])
    h16 = wh.cycle("hourly", 16, DAY + timedelta(days=32))
    wh.dbt(h16, "int_track_identity")
    assert rows(wh, "intermediate.int_track_identity", f"platform_track_id='{t['id']}'", "mb_recording_gid, redirected") == \
        [("00000000-0000-4000-b000-000000000101", True)]


def test_generations_switch_only_when_they_reconcile(wh):
    before = dict(rows(wh, "intermediate.int_track_identity", columns="platform_track_id, reference_version"))
    # A reload of unchanged rows restamps them in a new generation and changes no reference_version.
    wh.generation(G2, 20, reference_rows())
    h20 = wh.cycle("hourly", 20, DAY + timedelta(days=40))
    wh.dbt(h20, HOURLY)
    assert rows(wh, "intermediate.int_reference__current", columns="distinct ref_generation") == [(G2,)]
    assert dict(rows(wh, "intermediate.int_track_identity", columns="platform_track_id, reference_version")) == before
    # G3 drops an ISRC, but its ISRC dump is not in the manifest: staging keeps G2, and no tombstone.
    dumps = wh.generation(G3, 21, reference_rows(drop_isrc=3), unstamped=("isrc",))
    h21 = wh.cycle("hourly", 21, DAY + timedelta(days=40, hours=1))
    wh.dbt(h21, HOURLY)
    assert rows(wh, "intermediate.int_reference__current", columns="distinct ref_generation") == [(G2,)]
    assert rows(wh, "intermediate.int_reference__current", "tombstoned", "count(*)") == [(0,)]
    # The resumed landing commits: G3 reconciles, and the key it no longer carries is a tombstone.
    wh.stamp(dumps["isrc"], "raw.mb_isrc", 22, "mb_spine")
    h22 = wh.cycle("hourly", 22, DAY + timedelta(days=40, hours=2))
    wh.dbt(h22, HOURLY)
    assert rows(wh, "intermediate.int_reference__current", columns="distinct ref_generation") == [(G3,)]
    assert rows(wh, "intermediate.int_reference__current", "tombstoned", "mb_table, mb_key") == [("isrc", "3")]
    after = dict(rows(wh, "intermediate.int_track_identity", columns="platform_track_id, reference_version"))
    assert after["1600000108"] != before["1600000108"]
    assert after["SynthTrack000000000001"] == before["SynthTrack000000000001"]
    # A replay of the incomplete cycle rebuilds from its own manifest: G2 again.
    wh.dbt(h21, "int_reference__current", vars_={"cycle_id": h21["id"]})
    assert rows(wh, "intermediate.int_reference__current", columns="distinct ref_generation") == [(G2,)]


def test_resolution_closures_join_and_retention_keeps_two_generations(wh):
    from mdp_functions.reference import retain

    # The hourly cycle at close 21 (test_generations_switch_only_when_they_reconcile) read G2 and G1.
    ((h21_id, h21_run),) = wh.query("SELECT c.id, a.dbt_run_id FROM raw.cycles c JOIN raw.cycle_attempts a ON a.cycle_id = c.id "
                                    "WHERE c.cadence = 'hourly' AND c.close_no = 21")
    h21 = {"id": str(h21_id), "run_id": h21_run, "cadence": "hourly", "close_no": 21}

    t, rec = tracks()["hero"], "00000000-0000-4000-b000-000000000104"
    fields = rows(wh, "intermediate.int_identity__track_inputs", f"platform_track_id='{t['id']}'", "fields_hash")[0][0]
    ref = rows(wh, "intermediate.int_track_identity", f"platform_track_id='{t['id']}'", "reference_version")[0][0]
    touched = [{"mb_table": "recording", "mb_key": "104", "recording_id": 104, "recording_gid": rec, "name": "Synthetic Song Beta",
                "artist_credit_id": 2, "length_ms": 200690, "video": False},
               {"mb_table": "isrc", "mb_key": "9", "isrc_id": 9, "isrc": "USXXX2200004", "recording_id": 104}]
    # An answer read from G3 carries a recording and ISRC the scoped spine lacks; one read from G1 is stale.
    c23 = wh.cycle("hourly", 23, DAY + timedelta(days=40, hours=3))
    wh.land("raw.mb_resolve", [resolution(t, fields, ref, "2026-W47", recording_id=104, recording_gid=rec, mb_generation=G3)],
            23, "mb_resolve", cycle_id=c23["id"])
    wh.land("raw.mb_resolve_closure", [{**r, "mb_generation": G3} for r in touched], 23, "mb_resolve", cycle_id=c23["id"])
    wh.land("raw.mb_resolve_closure", [{**touched[1], "mb_key": "8", "isrc_id": 8, "mb_generation": G1}], 23, "mb_resolve",
            cycle_id=c23["id"])
    h24 = wh.cycle("hourly", 24, DAY + timedelta(days=40, hours=4))
    wh.dbt(h24, HOURLY)
    assert rows(wh, "intermediate.int_reference__current", "(mb_table, mb_key) in (('recording','104'), ('isrc','9'), ('isrc','8'))",
                "mb_table, mb_key, tombstoned, row_generation") == [("isrc", "9", False, G3), ("recording", "104", False, G3)]
    # Retention keeps the newest two reconciled generations: G1 goes, G3 and G2 stay, and G4, still
    # landing (its ISRC dump unstamped), is never touched.
    wh.generation(G4, 25, reference_rows(), unstamped=("isrc",))
    kept = retain(wh.url)
    assert kept["kept"] == [G3, G2] and kept["deleted"]["raw.mb_generation"] == 1
    assert wh.query("SELECT mb_generation, count(*) FROM raw.mb_isrc GROUP BY 1 ORDER BY 1") == [(G2, 3), (G3, 2), (G4, 3)]
    for table in ("mb_url_link", "mb_recording", "mb_redirect", "mb_artist", "mb_generation"):
        assert wh.query(f"SELECT count(*) FROM raw.{table} WHERE mb_generation = %s", (G1,)) == [(0,)], table
    assert retain(wh.url)["deleted"] == {}
    # Current state is unchanged: G3 with G2's tombstone.
    h26 = wh.cycle("hourly", 26, DAY + timedelta(days=40, hours=6))
    wh.dbt(h26, HOURLY)
    assert rows(wh, "intermediate.int_reference__current", columns="distinct ref_generation") == [(G3,)]
    assert rows(wh, "intermediate.int_reference__current", "tombstoned", "mb_table, mb_key") == [("isrc", "3")]
    # A Replay of an older cycle whose manifest read G1 fails loudly; it never resolves on G2 alone.
    with pytest.raises(AssertionError, match=f"reference_generation_incomplete: this cycle's manifest reads MusicBrainz generation {G1}"):
        wh.dbt(h21, "int_reference__current", vars_={"cycle_id": h21["id"]})
    wh.dbt(h26, "int_reference__current")


def test_each_hour_turns_over_about_one_in_168(wh):
    # 3,360 priority tracks: about 20 change input_version between two consecutive hourly closes.
    many = [{"id": f"MANY{n:018}", "title": f"Song {n}", "artists": ["Many"], "duration": 180000} for n in range(3360)]
    wh.observation("apple_music", "bigchart", DAY + timedelta(days=41), many, "chart", 30)
    first = wh.cycle("hourly", 30, datetime(2026, 11, 4, 10, tzinfo=UTC))
    wh.dbt(first, HOURLY)
    before = dict(rows(wh, "intermediate.int_identity__resolution_inputs", "platform_track_id like 'MANY%'", "platform_track_id, input_version"))
    second = wh.cycle("hourly", 31, datetime(2026, 11, 4, 11, tzinfo=UTC))
    wh.dbt(second, HOURLY)
    after = dict(rows(wh, "intermediate.int_identity__resolution_inputs", "platform_track_id like 'MANY%'", "platform_track_id, input_version"))
    changed = sum(before[k] != after[k] for k in before)
    assert len(before) == 3360 and 5 <= changed <= 45, changed


def test_followers_count_each_playlist_once_and_stop_on_time(wh):
    fday = datetime(2026, 12, 1, 8, tzinfo=UTC)
    # Own a complete reference generation so this test also runs without the earlier spine tests.
    wh.generation(G4, 39, reference_rows())
    bass = tracks()["bass"]
    cardigan = {"id": "1600000108", "title": "Synthetic Song Gamma", "artists": ["Fixture Artist Beta"], "duration": 239560}
    am_bass = {"id": "AMBASS", "title": "Fixture item 56802", "artists": ["Fixture Artist Alpha"], "duration": 202460, "isrc": "USXXX2600001"}
    # One Spotify playlist in two markets, the same recording twice in each.
    for day in range(3):
        for variant in ("US", "GB"):
            wh.observation("spotify", "dup1", fday + timedelta(days=day), [bass, bass], "editorial", 40, variant=variant,
                           followers=5000)
    # An Apple list observed complete once: its stream stops one window (24 h + one daily cycle) later.
    wh.observation("apple_music", "stale1", fday, [cardigan], "chart", 40)
    # An Apple list whose target is deactivated on day 1 and promoted again on day 3.
    target, target_set = ident("deact1-target"), ident("deact1-set")
    other = (ident("keeper-target"), "apple_music", "keeper")
    wh.revision(target_set, [(target, "apple_music", "deact1"), other], fday - timedelta(days=1), 41)
    wh.revision(target_set, [other], fday + timedelta(days=1, hours=4), 42)
    wh.revision(target_set, [(target, "apple_music", "deact1"), other], fday + timedelta(days=3) - timedelta(hours=8), 43)
    for day in (0, 1, 3, 4):
        wh.observation("apple_music", "deact1", fday + timedelta(days=day), [am_bass], "chart", 44, target_id=target)
    daily = wh.cycle("daily", 50, fday + timedelta(days=5))
    wh.dbt(daily, DAILY)
    since = f"day >= '{fday.date()}'"
    followers = rows(wh, "marts.mart_track_playlist_followers", since,
                     "platform, mb_recording_gid, day, list_count, playlist_followers")
    by_day = {(p, g[-3:], str(d)): (n, f) for p, g, d, n, f in followers}
    days = [str((fday + timedelta(days=n)).date()) for n in range(6)]
    # Duplicate occurrences and two markets count the playlist once, with its follower count once.
    assert [by_day.get(("spotify", "101", d)) for d in days] == [(1, 5000)] * 5 + [None]
    # Each market keeps its own measure: the playlist once per variant, never summed across them.
    assert wh.query(
        f"SELECT variant, count(*), max(list_count), max(playlist_followers) FROM marts.mart_track_playlist_followers_by_variant "
        f"WHERE {since} AND platform='spotify' GROUP BY 1 ORDER BY 1") == [("GB", 5, 1, 5000), ("US", 5, 1, 5000)]
    # The stale full stream stops after its last complete observation plus the window.
    assert [by_day.get(("apple_music", "108", d)) for d in days] == [(1, None)] * 3 + [None] * 3
    # The deactivated target stops at deactivation and keeps its intervals after promotion again.
    assert [by_day.get(("apple_music", "101", d)) for d in days] == [(1, None), None, None, (1, None), (1, None), (1, None)]
