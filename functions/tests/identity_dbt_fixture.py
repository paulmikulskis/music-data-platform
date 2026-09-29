"""Load/check the identity chain in a disposable DuckDB after dbt bootstrap.

MDP_DEV_DB=/tmp/identity/identity.duckdb uv run --project functions python functions/tests/identity_dbt_fixture.py load
Run the fixture build, then invoke this script with `check`. Local builds read every landed row, so
this proves the DuckDB SQL of the reference, input, exact and pick macros; the cycle-bound behaviour
is proven on Postgres by test_identity_spine.py.
"""

import json
import os
import sys
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import duckdb

G1, G2 = "20260923-002121", "20261021-001500"
REC = "00000000-0000-4000-b000-000000000"


def ident(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "mdp-identity-duckdb:" + value))


def insert(conn, table, rows, lineage):
    for row in rows:
        record = {**row, **lineage}
        conn.execute(
            f"insert into raw.{table} ({','.join(record)}) values ({','.join('?' for _ in record)})",
            [json.dumps(v) if isinstance(v, (dict, list)) else v for v in record.values()],
        )


def generation(conn, gen, seq, tables):
    run = ident("run:" + gen)
    stamp = {"mb_channel": "dump", "mb_generation": gen, "mb_sequence": 189207}
    outputs, counts = {}, {}
    for name, rows in tables.items():
        dump = ident(f"dump:{gen}:{name}")
        insert(conn, "mb_" + name, [{**stamp, **r} for r in rows],
               {"_run_id": run, "_dump_id": dump, "_landed_seq": seq, "_source_key": "mb_spine", "_extra": "{}"})
        outputs["raw.mb_" + name], counts["raw.mb_" + name] = {dump: len(rows)}, len(rows)
    dump = ident(f"dump:{gen}:generation")
    insert(conn, "mb_generation", [{**stamp, "mb_key": gen, "export_date": datetime(2026, 9, 23, tzinfo=UTC),
                                    "schema_sequence": 31, "imported_at": datetime(2026, 9, 23, tzinfo=UTC),
                                    "validated_at": datetime(2026, 9, 23, tzinfo=UTC), "mirror_counts": json.dumps(counts),
                                    "read_at": datetime(2026, 9, 23, tzinfo=UTC)}],
           {"_run_id": run, "_dump_id": dump, "_landed_seq": seq + 1, "_source_key": "mb_spine", "_extra": "{}"})
    outputs["raw.mb_generation"] = {dump: 1}
    insert(conn, "_run_completion", [{"run_id": run, "source_key": "mb_spine", "outputs": outputs,
                                      "recorded": {"generation": gen, "sequence": 189207, "mirror_counts": counts}}],
           {"_run_id": run, "_dump_id": ident(f"completion:{gen}"), "_landed_seq": seq + 2, "_source_key": "mb_spine", "_extra": "{}"})


def reference(drop_isrc=None):
    return {
        "url_link": [{"mb_key": "recording:1", "entity_type": "recording", "link_id": 1, "entity_id": 101, "url_id": 1001,
                      "url": "https://open.spotify.com/track/SynthTrack000000000001", "link_type": "free streaming", "ended": False,
                      "url_platform": "spotify", "url_kind": "track", "url_platform_id": "SynthTrack000000000001"},
                     {"mb_key": "artist:1", "entity_type": "artist", "link_id": 1, "entity_id": 2, "url_id": 1005,
                      "url": "https://open.spotify.com/artist/SynthArtist00000000001", "link_type": "free streaming", "ended": False,
                      "url_platform": "spotify", "url_kind": "artist", "url_platform_id": "SynthArtist00000000001"}],
        "isrc": [{"mb_key": str(i), "isrc_id": i, "isrc": v, "recording_id": r}
                 for i, v, r in [(1, "USXXX2600001", 101), (2, "USXXX2600002", 102)] if i != drop_isrc],
        "recording": [{"mb_key": str(i), "recording_id": i, "recording_gid": REC + str(i), "name": n, "artist_credit_id": 1,
                       "length_ms": 200000, "video": False} for i, n in [(101, "Fixture item 56802"), (102, "Synthetic Song Alpha")]],
        "artist": [{"mb_key": "2", "artist_id": 2, "artist_gid": "00000000-0000-4000-a000-000000000002", "name": "Fixture Artist Beta",
                    "sort_name": "Beta, Fixture Artist", "type_id": 1}],
        "redirect": [],
    }


def load(conn):
    for table in ("mb_url_link", "mb_isrc", "mb_recording", "mb_artist", "mb_redirect", "mb_generation", "_run_completion",
                  "mb_resolve", "mb_resolve_closure", "track_isrc_crosswalk"):
        conn.execute("delete from raw." + table)
    # G2 drops ISRC 2: the newest generation, with that key as its tombstone.
    generation(conn, G1, 100, reference())
    generation(conn, G2, 200, reference(drop_isrc=2))
    # A resolution for the Synthetic Song Alpha track of the playlist fixture's first rows: its fields are read back
    # by `check`, so the fixture resolution carries whatever version the build computes.
    print("Loaded 2 MusicBrainz generations (G2 tombstones ISRC 2)")


def resolution(conn):
    """After a first build: land an mb_resolve row for one resolution input with its own versions."""
    row = conn.execute(
        "select platform, platform_track_id, fields_hash, reference_version, retry_week, input_ref, input_version "
        "from main_intermediate.int_identity__resolution_inputs order by platform_track_id, platform limit 1").fetchone()
    if row is None:
        raise SystemExit("FAIL no resolution inputs in the fixture build")
    platform, track, fields, ref, week, input_ref, version = row
    # The spine rows the answer touched: recording 101 (also in the spine, which wins) and recording 103
    # with its ISRC, which only this answer carries until the next generation.
    touched = [{"mb_table": "recording", "mb_key": str(i), "recording_id": i, "recording_gid": REC + str(i), "name": n,
                "artist_credit_id": 1, "length_ms": 200000, "video": False} for i, n in [(101, "Fixture item 56802"), (103, "Closure Song")]]
    touched.append({"mb_table": "isrc", "mb_key": "3", "isrc_id": 3, "isrc": "USXX12600103", "recording_id": 103})
    insert(conn, "mb_resolve", [{
        "platform": platform, "platform_track_id": track, "status": "resolved", "method": "mb_release", "recording_id": 101,
        "recording_gid": REC + "101", "isrc": "USXXX2600001", "isrc_count": 1, "release_id": 201, "release_gid": None,
        "candidate_count": 1, "confidence": 1.0, "evidence": "fixture", "mb_generation": G2, "mb_sequence": 189207,
        "lookup_ms": 1.0, "input_ref": input_ref, "input_version": version, "fields_hash": fields, "reference_version": ref,
        "retry_week": week, "scope": "global", "step": "external:mb_resolve", "config_version": "1", "learning_eligible": False,
    }], {"_run_id": ident("resolve"), "_dump_id": ident("resolve-dump"), "_landed_seq": 300, "_source_key": "mb_resolve",
         "_cycle_id": ident("resolve-cycle"), "_extra": "{}"})
    insert(conn, "mb_resolve_closure", [{**r, "mb_generation": G2, "mb_sequence": 189207, "input_ref": input_ref,
                                         "input_version": version, "scope": "global", "step": "external:mb_resolve",
                                         "config_version": "1"} for r in touched],
           {"_run_id": ident("resolve"), "_dump_id": ident("resolve-closure-dump"), "_landed_seq": 301,
            "_source_key": "mb_resolve", "_cycle_id": ident("resolve-cycle"), "_extra": "{}"})
    print(f"Landed one mb_resolve row for {platform}:{track}")
    return platform, track


def check(conn):
    current = conn.execute("select distinct ref_generation from main_intermediate.int_reference__current").fetchall()
    assert current == [(G2,)], current
    tombstones = conn.execute("select mb_table, mb_key from main_intermediate.int_reference__current where tombstoned").fetchall()
    assert tombstones == [("isrc", "2")], tombstones
    touched = conn.execute(
        "select mb_table, mb_key, row_generation, _dump_id from main_intermediate.int_reference__current "
        "where mb_key in ('101', '103', '3') and mb_table in ('recording', 'isrc') order by 1, 2").fetchall()
    assert [(t, k) for t, k, _, _ in touched] == [("isrc", "3"), ("recording", "101"), ("recording", "103")], touched
    assert {k: str(d) == ident("resolve-closure-dump") for t, k, _, d in touched} == {"3": True, "101": False, "103": True}, touched
    inputs = conn.execute("select count(*), count(distinct platform || ':' || platform_track_id) from main_intermediate.int_identity__track_inputs").fetchone()
    assert inputs[0] == inputs[1] > 0, inputs
    resolved = conn.execute(
        "select count(*) from main_intermediate.int_track_identity where recording_method = 'mb_release'").fetchone()[0]
    daily = conn.execute(
        "select count(*) from main_intermediate.int_track_identity__daily where recording_method = 'mb_release'").fetchone()[0]
    assert resolved == daily == 1, (resolved, daily)
    events = conn.execute("select count(*) from main_marts.mart_playlist_events where mb_recording_gid is not null").fetchone()[0]
    for mart in ("mart_playlist_events", "mart_editorial_entries", "mart_editorial_presence"):
        mismatches = conn.execute(
            f"select count(*) from main_marts.{mart} m "
            "left join main_intermediate.int_track_identity__daily i "
            "on m.item_type='track' and m.platform=i.platform and m.platform_track_id=i.platform_track_id "
            "where m.confidence is distinct from i.recording_confidence "
            "or (m.recording_method is null) <> (m.confidence is null)").fetchone()[0]
        assert mismatches == 0, (mart, mismatches)
    followers = conn.execute("select count(*) from main_marts.mart_track_playlist_followers").fetchone()[0]
    artists = conn.execute("select platform_artist_id, mb_artist_gid from main_intermediate.int_artist_identity").fetchall()
    assert events > 0 and followers > 0, (events, followers)
    # Both midnight-observed lists count on Sep 1 under America/New_York too.
    # A timezone-bearing next_at used to cast to the previous day and drop one list.
    first_day = conn.execute(
        "select list_count from main_marts.mart_track_playlist_followers "
        "where day='2026-09-01' and platform='apple_music' and owner_class='editorial' "
        "and mb_recording_gid='00000000-0000-4000-b000-000000000101'").fetchone()
    assert first_day == (2,), first_day
    assert artists == [("SynthArtist00000000001", "00000000-0000-4000-a000-000000000002")], artists
    print(f"PASS identity: G2 current with 1 tombstone and 2 rows from an mb_resolve closure, {inputs[0]} track inputs, 1 mb_release pick hourly and daily, "
          f"{events} identified events, {followers} follower rows, 1 artist")


if __name__ == "__main__":
    with duckdb.connect(os.environ["MDP_DEV_DB"]) as connection:
        {"load": load, "resolution": resolution, "check": check}[sys.argv[1]](connection)
