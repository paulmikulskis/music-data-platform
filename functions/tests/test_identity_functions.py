"""design identity functions: mb_spine lands a validated generation once, mb_resolve looks tracks up
on the mirror, and track_isrc_crosswalk searches publicly, each against a small MusicBrainz fixture."""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound, url_database
from mdp_functions import musicbrainz as mb
from mdp_functions.errors import ServiceError
from mdp_functions.musicbrainz import SPINE, connect, platform_url, resolve
from mdp_functions.registry import discover
from mdp_functions.sources.track_isrc_crosswalk.function import score
from psycopg import sql
from psycopg.rows import dict_row

REPO = Path(__file__).resolve().parents[2]
G1 = "20260923-002121"


def load_mirror(admin_url: str, name: str) -> str:
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(url_database(admin_url, name), autocommit=True) as conn:
        conn.execute((REPO / "functions/tests/fixtures/mb_mirror.sql").read_text())
        conn.execute((REPO / "ops/fly/mb-db/mdp-schema.sql").read_text())
        conn.execute(
            "INSERT INTO mdp.generation(generation,export_date,replication_sequence,schema_sequence,validated_at,counts) "
            "VALUES (%s,'2026-09-23 00:21:21+00',189207,31,now(),'{}')",
            (G1,),
        )
        conn.execute("ALTER ROLE mb_reader PASSWORD 'mb_reader'")
    options = psycopg.conninfo.conninfo_to_dict(url_database(admin_url, name))
    options.update(user="mb_reader", password="mb_reader")
    return psycopg.conninfo.make_conninfo(**options)


@pytest.fixture(scope="module")
def mirror():
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    name = "mbt_fn_" + uuid4().hex[:10]
    url = load_mirror(admin, name)
    yield {"url": url, "admin": url_database(admin, name)}
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_platform_urls_parse_the_tracked_shapes():
    assert platform_url("https://open.spotify.com/track/SynthTrack000000000001") == ("spotify", "track", "SynthTrack000000000001")
    assert platform_url("https://music.apple.com/us/album/twin-ep/1500000202") == ("apple_music", "album", "1500000202")
    assert platform_url("https://itunes.apple.com/GB/album/x-single/id943047758") == ("apple_music", "album", "943047758")
    assert platform_url("https://music.apple.com/us/album/x/1500000202?i=77") == ("apple_music", "track", "77")
    assert platform_url("https://music.apple.com/ae/song/1209651308") == ("apple_music", "track", "1209651308")
    assert platform_url("https://synthetic-artist.bandcamp.com/track/neverland") == ("bandcamp", "track", "synthetic-artist.bandcamp.com/track/neverland")
    assert platform_url("https://soundcloud.com/artist/a-song") == ("soundcloud", "track", "artist/a-song")
    assert platform_url("https://open.spotify.com/") is None and platform_url("https://www.discogs.com/release/1") is None


def track(**kw):
    return {"platform": "spotify", "platform_track_id": "t", "title": None, "artist_names": "[]",
            "duration_ms": None, "platform_album_id": None, "position": 1, **kw}


@pytest.mark.docker
def test_lookups_follow_the_release_then_trigram_order(mirror):
    conn = connect(mirror["url"])
    conn.autocommit = True
    mb.lookup_session(conn)
    # A platform album URL to its release, then the candidate by title, credit and duration.
    flowers = resolve(conn, track(platform_track_id="f1", title="Synthetic Song Alpha", artist_names='["Fixture Artist Alpha"]',
                                  duration_ms=200400, platform_album_id="SynthAlbum000000000001"))
    assert (flowers["status"], flowers["method"], flowers["recording_id"], flowers["isrc"], flowers["confidence"]) == \
        ("resolved", "mb_release", 102, "USXXX2600002", 1.0)
    assert (flowers["mb_generation"], flowers["mb_sequence"]) == (G1, 189207)
    # The answer lands the spine rows it touched: recording, ISRC, release and group, credit, artist and
    # the release's tracked URL, as mb_spine lands them.
    touched = {(r["mb_table"], r["mb_key"]) for r in flowers["closure"]}
    assert touched == {("recording", "102"), ("isrc", "2"), ("release", "201"), ("release_group", "301"),
                       ("artist_credit", "1"), ("artist_credit_name", "1:0"), ("artist", "1"), ("url_link", "release:1")}
    album = next(r for r in flowers["closure"] if r["mb_table"] == "url_link")
    assert (album["url_platform"], album["url_kind"], album["url_platform_id"]) == ("spotify", "album", "SynthAlbum000000000001")
    # Playlist position never enters the match.
    moved = resolve(conn, track(platform_track_id="f1", title="Synthetic Song Alpha", artist_names='["Fixture Artist Alpha"]',
                                duration_ms=200400, platform_album_id="SynthAlbum000000000001", position=99))
    assert moved["recording_id"] == 102
    # Two recordings of one title and length on the release: unresolved, with the count.
    twin = resolve(conn, track(platform="apple_music", platform_track_id="a1", title="Twin",
                               artist_names='["The Twins"]', duration_ms=180200, platform_album_id="1500000202"))
    assert (twin["status"], twin["method"], twin["candidate_count"], twin["recording_id"]) == ("ambiguous", "mb_release", 2, None)
    # No release candidate: title and credit by trigram, length within two seconds.
    hero = resolve(conn, track(platform_track_id="h1", title="Synthetic Song Beta", artist_names='["Fixture Artist Beta"]', duration_ms=200000))
    assert (hero["status"], hero["method"], hero["recording_id"], hero["isrc"]) == ("resolved", "mb_trigram", 104, None)
    # The artist's reference and social links (Wikidata, Instagram) ride with its streaming link.
    assert {(r["mb_table"], r["mb_key"]) for r in hero["closure"]} == {
        ("recording", "104"), ("artist_credit", "2"), ("artist_credit_name", "2:0"), ("artist", "2"), ("url_link", "artist:1"),
        ("url_link", "artist:2"), ("url_link", "artist:3")}
    # A length outside two seconds matches nothing; the negative result still carries the generation.
    late = resolve(conn, track(platform_track_id="f2", title="Synthetic Song Alpha", artist_names='["Fixture Artist Alpha"]',
                               duration_ms=203000, platform_album_id="SynthAlbum000000000001"))
    assert (late["status"], late["method"], late["candidate_count"], late["mb_generation"]) == ("unmatched", None, 0, G1)
    assert late["closure"] is None and twin["closure"] is None
    conn.close()


# The live mirror's shape at small scale: 60,000 recordings titled "Night" by 100 other credits, all
# within two seconds of the input, 20,000 more credits, and 300,000 untracked URLs with 200,000 release
# links. The planner cannot tell how many names share a short title's trigrams, so name statistics are
# withheld; the tail index is newer than the table's statistics, as on the mirror before mdp-schema.sql
# analyzed it. "Night" also sits under "Testa & Voko", the live mirror's credit for it. "Limit Song"
# has 60 recordings by a credit below the artist floor that outrank, by summed similarity, the one
# candidate both floors keep ("Limit Songs Live" by "Limit Artist Band", 0.625 and 0.72).
WIDE = """
ALTER TABLE musicbrainz.recording SET (autovacuum_enabled = false);
ALTER TABLE musicbrainz.artist_credit SET (autovacuum_enabled = false);
INSERT INTO musicbrainz.artist_credit (id, name, artist_count, gid)
SELECT 100000 + i, 'Decoy Artist ' || i, 1, gen_random_uuid() FROM generate_series(1, 20000) i;
INSERT INTO musicbrainz.artist_credit (id, name, artist_count, gid) VALUES (99999, 'Testa', 1, gen_random_uuid());
INSERT INTO musicbrainz.recording (id, gid, name, artist_credit, length)
SELECT 1000000 + i, gen_random_uuid(), 'Night', 100001 + i % 100, 356000 + i % 1000 FROM generate_series(1, 60000) i;
INSERT INTO musicbrainz.recording (id, gid, name, artist_credit, length) VALUES (999999, gen_random_uuid(), 'Night', 99999, 356680);
INSERT INTO musicbrainz.artist_credit (id, name, artist_count, gid) VALUES
  (99998, 'Testa & Voko', 2, gen_random_uuid()), (99997, 'Limit Artist and the Crowd', 1, gen_random_uuid()),
  (99996, 'Limit Artist Band', 1, gen_random_uuid());
INSERT INTO musicbrainz.recording (id, gid, name, artist_credit, length) VALUES
  (2000200, gen_random_uuid(), 'Night', 99998, 356680), (2000100, gen_random_uuid(), 'Limit Songs Live', 99996, 240000);
INSERT INTO musicbrainz.recording (id, gid, name, artist_credit, length)
SELECT 2000000 + i, gen_random_uuid(), 'Limit Song', 99997, 240000 FROM generate_series(1, 60) i;
INSERT INTO musicbrainz.url (id, gid, url)
SELECT 1000000 + i, gen_random_uuid(), 'https://www.discogs.com/release/' || (1000000 + i) FROM generate_series(1, 300000) i;
INSERT INTO musicbrainz.l_release_url (id, link, entity0, entity1)
SELECT 1000000 + i, 4, 5000000 + i, 1000000 + i FROM generate_series(1, 200000) i;
ANALYZE musicbrainz.url, musicbrainz.l_release_url;
ANALYZE musicbrainz.artist_credit (id);
ANALYZE musicbrainz.recording (id, artist_credit, length);
DROP INDEX musicbrainz.url_idx_mdp_tail_id;
CREATE INDEX url_idx_mdp_tail_id ON musicbrainz.url (mdp.url_tail_id(url));
"""


@pytest.fixture(scope="module")
def wide_mirror():
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    name = "mbt_wide_" + uuid4().hex[:10]
    url = load_mirror(admin, name)
    with psycopg.connect(url_database(admin, name), autocommit=True) as conn:
        conn.execute(WIDE)
    yield url
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


class Recorded(psycopg.Connection):
    """Records each statement a lookup sends, so the test can count and replay them."""

    statements: list

    def execute(self, query, params=None, **kw):
        self.statements.append((query, params))
        return super().execute(query, params, **kw)


@pytest.mark.docker
def test_a_lookup_reads_a_bounded_part_of_the_mirror(wide_mirror):
    conn = Recorded.connect(wide_mirror, row_factory=dict_row, autocommit=True)
    conn.statements = []
    mb.lookup_session(conn)
    inputs = [track(platform_track_id="f1", title="Synthetic Song Alpha", artist_names='["Fixture Artist Alpha"]', duration_ms=200400,
                    platform_album_id="SynthAlbum000000000001"),
              track(platform="apple_music", platform_track_id="n1", title="Night", artist_names='["Testa", "Voko"]',
                    duration_ms=356680),
              # No ASCII letter or digit: no trigrams on the mirror's C ctype, so no trigram search.
              track(platform="apple_music", platform_track_id="y1", title="Lemon", artist_names='["試験歌手"]',
                    duration_ms=255000),
              track(platform="apple_music", platform_track_id="b1", title="!!!", artist_names='["Testa"]',
                    duration_ms=356680)]
    expected = [("resolved", "mb_release", 102), ("resolved", "mb_trigram", 999999),
                ("unmatched", None, None), ("unmatched", None, None)]
    for row, answer in zip(inputs, expected, strict=True):
        conn.statements = []
        result = resolve(conn, row)
        assert (result["status"], result["method"], result["recording_id"]) == answer
        searched = any(query == mb.TRIGRAM_CANDIDATES for query, _ in conn.statements)
        assert searched == (row["platform_track_id"] == "n1")
        # One statement per step, whatever the mirror holds: nothing repeats per candidate or row.
        assert len(conn.statements) <= 18
        # The pages each statement touches stay within a few index descents of the answer; scanning the
        # 60,000 titles or the 200,000 release links reads thousands.
        pages = 0
        for query, params in conn.statements:
            explain = psycopg.Connection.execute(conn, "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(query), params)
            plan = next(iter(explain.fetchone().values()))[0]["Plan"]
            pages += plan["Shared Hit Blocks"] + plan["Shared Read Blocks"]
        assert pages < 400, (row["title"], pages)
    conn.close()


# The title-first query the credit-first one replaced, at pg_trgm's default threshold, with the
# floors applied after its LIMIT.
TITLE_FIRST = """
SELECT r.id AS recording_id, similarity(r.name, %(title)s) AS title_sim, similarity(c.name, %(artist)s) AS artist_sim
FROM musicbrainz.recording r JOIN musicbrainz.artist_credit c ON c.id = r.artist_credit
WHERE r.name %% %(title)s AND c.name %% %(artist)s AND r.length BETWEEN %(low)s AND %(high)s
ORDER BY similarity(r.name, %(title)s) + similarity(c.name, %(artist)s) DESC, r.id
LIMIT 50"""


@pytest.mark.docker
def test_credit_first_candidates_keep_every_candidate_the_title_first_query_kept(wide_mirror):
    def kept(conn, query, title, artist, duration):
        rows = conn.execute(query, {"title": title, "artist": artist, "low": duration - mb.DURATION_MS,
                                    "high": duration + mb.DURATION_MS}).fetchall()
        return {r["recording_id"] for r in rows if min(r["title_sim"], r["artist_sim"]) >= mb.TRIGRAM_FLOOR}

    with connect(wide_mirror) as before, connect(wide_mirror) as after:
        before.execute("SET search_path = musicbrainz, public")
        mb.lookup_session(after)
        for title, artist, duration in [("Night", "Testa", 356680), ("Night", "Testa & Voko", 356680),
                                        ("Limit Song", "Limit Artist", 240000), ("Synthetic Song Alpha", "Fixture Artist Alpha", 200400)]:
            old = kept(before, TITLE_FIRST, title, artist, duration)
            new = kept(after, mb.TRIGRAM_CANDIDATES, title, artist, duration)
            assert old <= new, (title, artist)
        # The credit floor applies to the whole credit, so "Testa & Voko" stays below it for
        # "Testa" in both queries: the multi-artist known limit.
        assert kept(after, mb.TRIGRAM_CANDIDATES, "Night", "Testa", 356680) == {999999}
        # 60 candidates below the artist floor fill the title-first LIMIT 50 ahead of the one kept
        # candidate; the credit-first query ranks only candidates both floors keep.
        assert kept(before, TITLE_FIRST, "Limit Song", "Limit Artist", 240000) == set()
        assert kept(after, mb.TRIGRAM_CANDIDATES, "Limit Song", "Limit Artist", 240000) == {2000100}


@pytest.mark.docker
def test_connect_retries_a_refused_mirror_before_it_counts(mirror, monkeypatch):
    real, calls, waits = psycopg.connect, [], []

    def refusing(times):
        def attempt(*args, **kwargs):
            calls.append(1)
            if len(calls) <= times:
                raise psycopg.OperationalError("connection refused")
            return real(*args, **kwargs)
        return attempt

    monkeypatch.setattr(mb.time, "sleep", waits.append)
    monkeypatch.setattr(mb.psycopg, "connect", refusing(2))
    with connect(mirror["url"]) as conn:
        assert conn.execute("SELECT 1 AS one").fetchone() == {"one": 1}
    assert (len(calls), waits) == (3, list(mb.CONNECT_BACKOFF_S))
    calls.clear()
    monkeypatch.setattr(mb.psycopg, "connect", refusing(3))
    with pytest.raises(ServiceError) as refused:
        connect(mirror["url"])
    assert refused.value.error_class == "vendor_retryable" and len(calls) == 3


@pytest.mark.docker
def test_the_reader_cannot_write_the_mirror(mirror):
    with connect(mirror["url"]) as conn, pytest.raises(psycopg.Error):
        conn.execute("INSERT INTO mdp.generation(generation,export_date,replication_sequence,schema_sequence) VALUES ('x',now(),1,1)")


def test_paged_inputs_come_oldest_first(databases):
    """Both identity invokes take pending inputs oldest first: retry_week, then first_landed_seq."""
    from mdp_functions.derived import relation_pages
    from mdp_functions.settings import Settings

    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS intermediate")
        conn.execute("DROP TABLE IF EXISTS intermediate.ordered_inputs")
        conn.execute("CREATE TABLE intermediate.ordered_inputs (input_ref text, retry_week text, first_landed_seq bigint)")
        conn.execute("INSERT INTO intermediate.ordered_inputs VALUES ('c','2026-W40',5),('a','2026-W39',9),('d','2026-W40',7),('b','2026-W39',3)")
        conn.execute("GRANT USAGE ON SCHEMA intermediate TO service_read")
        conn.execute("GRANT SELECT ON intermediate.ordered_inputs TO service_read")
    settings = Settings(service_read_url=databases["service_read_url"])
    pages = list(relation_pages(settings, "intermediate.ordered_inputs", 2, order=["retry_week", "first_landed_seq"]))
    assert [[r["input_ref"] for r in page] for page in pages] == [["b", "a"], ["c", "d"]]


def test_the_hourly_job_selects_no_daily_playlist_model():
    """Hourly track inputs read raw playlist dumps through the hourly manifest; the hourly selector
    holds none of the daily playlist models."""
    import subprocess

    listing = subprocess.run(
        ["uv", "run", "--project", "dbt", "dbt", "ls", "--project-dir", "dbt", "--profiles-dir", "dbt/profiles",
         "--target", "ci", "--selector", "hourly_global_transform", "--resource-type", "model", "--output", "name", "--quiet"],
        cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    assert {"int_identity__track_inputs", "int_reference__current", "int_track_identity"} <= set(listing)
    assert not [m for m in listing if m.startswith(("stg_playlist__", "int_playlist__", "mart_playlist_"))]
    source = (REPO / "dbt/models/intermediate/int_identity__track_inputs.sql").read_text()
    assert "mdp_track_inputs(" in source and "playlist__" not in source


def test_declarations_order_inputs_and_bound_time():
    catalog = discover()
    resolve_m, crosswalk_m, spine_m = catalog["mb_resolve"], catalog["track_isrc_crosswalk"], catalog["mb_spine"]
    assert resolve_m.input_order == crosswalk_m.input_order == ["retry_week", "first_landed_seq"]
    assert (resolve_m.time_budget_s, crosswalk_m.time_budget_s) == (1200, 1800)
    assert resolve_m.knobs["allow_partial"] and crosswalk_m.knobs["allow_partial"]
    assert resolve_m.input_version == crosswalk_m.input_version == ["fields_hash", "reference_version", "retry_week"]
    assert resolve_m.reads == ["intermediate.int_identity__resolution_inputs"]
    assert crosswalk_m.reads == ["intermediate.int_identity__crosswalk_inputs"]
    assert spine_m.completion and spine_m.cadence == "weekly" and spine_m.writes[-1] == "raw.mb_generation"
    assert spine_m.layer == "universal" and spine_m.external and spine_m.reads == ["intermediate.int_identity__track_inputs"]
    assert resolve_m.writes == ["raw.mb_resolve", "raw.mb_resolve_closure"]
    assert resolve_m.table_physical_key("raw.mb_resolve_closure")[-2:] == ["mb_table", "mb_key"]
    assert set(spine_m.writes) == set(SPINE) | {"raw.mb_generation"}
    # The mirror's precomputed tracked URLs use the parser's pattern.
    schema = (REPO / "ops/fly/mb-db/mdp-schema.sql").read_text()
    assert f"SELECT '{mb.TRACKED_URL_SQL}'::text" in schema
    # The dbt reconciliation checks every table mb_spine writes.
    macro = (REPO / "dbt/macros/mdp_reference.sql").read_text()
    assert all(f"'{t.removeprefix('raw.mb_')}':" in macro for t in SPINE)


def test_crosswalk_requires_artist_title_and_duration_to_agree():
    row = {"title": "Fixture item 56802", "artist_names": '["Fixture Artist Alpha"]', "duration_ms": 202460}
    hit = {"title": "Fixture item 56802", "duration": 202, "artist": {"name": "Fixture Artist Alpha"}, "isrc": "USXXX2600001"}
    assert score(row, hit) == 1.0
    assert score(row, {**hit, "title": "Fixture item 56802 (Remix)"}) == 0.9
    assert score(row, {**hit, "artist": {"name": "Someone Else"}}) is None
    assert score(row, {**hit, "duration": 210}) is None
    assert score({**row, "album_title": "Other"}, {**hit, "album": {"title": "Endless"}}) is None


def tracked(databases, inputs):
    """The relation mb_spine reads its seeds from, as the hourly chain builds it."""
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS intermediate")
        conn.execute("DROP TABLE IF EXISTS intermediate.int_identity__track_inputs")
        conn.execute("CREATE TABLE intermediate.int_identity__track_inputs (platform text, platform_track_id text, "
                     "platform_isrc text, platform_album_id text, platform_artist_ids text)")
        for row in inputs:
            conn.execute("INSERT INTO intermediate.int_identity__track_inputs VALUES (%s,%s,%s,%s,%s)", row)
        conn.execute("GRANT USAGE ON SCHEMA intermediate TO service_read")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA intermediate TO service_read")


# A Spotify track with its album and artist, an Apple song, and a track that reports an ISRC.
CATALOG = [("spotify", "SynthTrack000000000001", None, "SynthAlbum000000000001", '["SynthArtist00000000001"]'),
           ("apple_music", "1600000108", None, None, None),
           ("spotify", "FLW0000000000000000001", "USXXX2600002", None, None)]


def spine_rows(databases, table):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        return conn.execute(sql.SQL("SELECT * FROM {} ORDER BY mb_key").format(sql.Identifier("raw", table))).fetchall()


async def test_mb_spine_lands_a_validated_generation_once(rt, databases, mirror, monkeypatch):
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    tracked(databases, CATALOG)
    _, run = await bound(rt, "mb_spine")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        (outputs, recorded, seq), = conn.execute(
            "SELECT outputs,recorded,_landed_seq FROM raw._run_completion WHERE _source_key='mb_spine'").fetchall()
        gen_seq = conn.execute("SELECT max(_landed_seq) FROM raw.mb_generation").fetchone()[0]
        urls = conn.execute("SELECT url_platform,url_kind,url_platform_id,entity_type,entity_id FROM raw.mb_url_link ORDER BY mb_key").fetchall()
        recordings = {r[0] for r in conn.execute("SELECT recording_id FROM raw.mb_recording")}
        redirects = conn.execute("SELECT entity_type,new_id FROM raw.mb_redirect").fetchall()
        releases = {r[0] for r in conn.execute("SELECT release_id FROM raw.mb_release")}
        generation = conn.execute("SELECT mb_generation,mb_channel,mb_sequence,mirror_counts FROM raw.mb_generation").fetchone()
    assert recorded["generation"] == G1 and recorded["sequence"] == 189207
    # raw.mb_generation is the function's last output; the runtime's completion row follows it.
    assert gen_seq < seq and generation[:3] == (G1, "dump", 189207)
    counts = json.loads(generation[3])
    assert counts == recorded["mirror_counts"] == {t: sum(v.values()) for t, v in outputs.items() if t != "raw.mb_generation"}
    # The tracked URL relationships of the closure, never an untracked domain (release 203's discogs
    # link) or an untracked release (the Twin EP's Apple album).
    assert sorted(urls) == sorted([
        ("spotify", "track", "SynthTrack000000000001", "recording", 101),
        ("apple_music", "track", "1600000108", "recording", 108),
        ("spotify", "album", "SynthAlbum000000000001", "release", 201),
        ("spotify", "artist", "SynthArtist00000000001", "artist", 2),
        ("wikidata", "entity", "Q9000002", "artist", 2),
        ("instagram", "profile", "fixture.act", "artist", 2)])
    # The closure of the tracked catalog: URL and ISRC recordings, the releases they sit on, and the
    # redirects into them; recordings 104, 106 and 107 and release 202 are untracked.
    assert recordings == {101, 102, 108} and releases == {201, 203}
    assert redirects == [("recording", 101)]
    # Nothing newer: the next run lands nothing.
    rt.cycles.close(run["cycle_id"])
    _, again = await bound(rt, "mb_spine")
    await rt.execute(again["id"])
    assert len(spine_rows(databases, "mb_generation")) == 1
    # A newer validated generation lands whole, from its own snapshot.
    rt.cycles.close(again["cycle_id"])
    with psycopg.connect(mirror["admin"], autocommit=True) as conn:
        conn.execute("DELETE FROM musicbrainz.isrc WHERE id=3")
        conn.execute("INSERT INTO mdp.generation(generation,export_date,replication_sequence,schema_sequence,validated_at) "
                     "VALUES ('20261021-001500','2026-10-21 00:15:00+00',190000,31,NULL)")
    _, unvalidated = await bound(rt, "mb_spine")
    await rt.execute(unvalidated["id"])
    assert len(spine_rows(databases, "mb_generation")) == 1
    rt.cycles.close(unvalidated["cycle_id"])
    with psycopg.connect(mirror["admin"], autocommit=True) as conn:
        conn.execute("UPDATE mdp.generation SET validated_at=now() WHERE generation='20261021-001500'")
    _, newer = await bound(rt, "mb_spine")
    await rt.execute(newer["id"])
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        by_gen = dict(conn.execute("SELECT mb_generation,count(*) FROM raw.mb_isrc GROUP BY 1").fetchall())
    assert by_gen == {G1: 3, "20261021-001500": 2}
    # Once a third generation reconciles, the next run's prepare step deletes the oldest as loader_wh,
    # the service's warehouse role and the one writer of raw.mb_*.
    rt.cycles.close(newer["cycle_id"])
    with psycopg.connect(mirror["admin"], autocommit=True) as conn:
        conn.execute("INSERT INTO mdp.generation(generation,export_date,replication_sequence,schema_sequence,validated_at) "
                     "VALUES ('20261118-001500','2026-11-18 00:15:00+00',191000,31,now())")
    _, third = await bound(rt, "mb_spine")
    await rt.execute(third["id"])
    rt.cycles.close(third["cycle_id"])
    assert psycopg.conninfo.conninfo_to_dict(rt.warehouse.url)["user"] == "loader_wh"
    _, fourth = await bound(rt, "mb_spine")
    await rt.execute(fourth["id"])
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        by_gen = dict(conn.execute("SELECT mb_generation,count(*) FROM raw.mb_isrc GROUP BY 1").fetchall())
        generations = [r[0] for r in conn.execute("SELECT mb_generation FROM raw.mb_generation ORDER BY 1")]
    assert by_gen == {"20261021-001500": 2, "20261118-001500": 2}
    assert generations == ["20261021-001500", "20261118-001500"]
    prepared = rt.db.one("SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='mb_spine_prepared'", (fourth["id"],))["attrs"]
    assert prepared["kept"] == ["20261118-001500", "20261021-001500"] and prepared["deleted"]["raw.mb_isrc"] == 3
    with psycopg.connect(mirror["admin"], autocommit=True) as conn:
        conn.execute("DELETE FROM mdp.generation WHERE generation IN ('20261021-001500','20261118-001500')")
        conn.execute("INSERT INTO musicbrainz.isrc (id, recording, isrc) VALUES (3, 108, 'USXXX2600003')")


async def test_mb_spine_resumes_a_failed_attempt_where_it_stopped(rt, databases, mirror, monkeypatch):
    from mdp_functions import musicbrainz
    from mdp_functions.sources.mb_spine import function as spine_fn

    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    monkeypatch.setattr(spine_fn, "PAGE_ROWS", 2)
    calls = {"n": 0}
    original = musicbrainz.spine_query

    def failing(table, ids, after):
        calls["n"] += 1
        if table == "raw.mb_track" and calls.get("failed") is None:
            calls["failed"] = True
            raise ServiceError("vendor_retryable", "mirror connection dropped")
        return original(table, ids, after)

    monkeypatch.setattr(spine_fn, "spine_query", failing)
    tracked(databases, CATALOG)
    # A fresh warehouse per test run keeps the earlier generations out of this one's counts.
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        for table in [*SPINE, "raw.mb_generation", "raw._run_completion"]:
            conn.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(*table.split("."))))
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.cursor WHERE streamline_id=(SELECT id FROM control.streamline WHERE source_key='mb_spine')")
    _, run = await bound(rt, "mb_spine")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] != "succeeded"
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        # Tables before the failure landed once: the retry resumed after them.
        assert conn.execute("SELECT count(*), count(DISTINCT mb_key) FROM raw.mb_isrc").fetchone() == (3, 3)
        assert conn.execute("SELECT count(*), count(DISTINCT mb_key) FROM raw.mb_track").fetchone() == (3, 3)
        (outputs, recorded), = conn.execute(
            "SELECT outputs,recorded FROM raw._run_completion WHERE _source_key='mb_spine' ORDER BY _landed_seq DESC LIMIT 1").fetchall()
    assert recorded["mirror_counts"] == {t: sum(v.values()) for t, v in outputs.items() if t != "raw.mb_generation"}


async def test_mb_spine_stops_before_the_volume_ceiling(rt, databases, mirror, monkeypatch):
    """A landing that would take the pgdata volume past its ceiling lands nothing and opens an alert."""
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    monkeypatch.setattr(rt, "settings", rt.settings.model_copy(update={"pgdata_volume_bytes": 10**6}))
    tracked(databases, CATALOG)
    before = len(spine_rows(databases, "mb_isrc"))
    _, run = await bound(rt, "mb_spine")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    assert len(spine_rows(databases, "mb_isrc")) == before
    # On a volume this small the closure trigger (a projected write past 30%) has crossed too.
    assert rt.db.all("SELECT class, severity, runbook_slug FROM control.alert WHERE run_id=%s ORDER BY class", (run["id"],)) == [
        {"class": "spine_narrowing_due", "severity": "warning", "runbook_slug": "spine-narrowing-due"},
        {"class": "warehouse_disk_high", "severity": "critical", "runbook_slug": "warehouse-disk-high"}]
    prepared = rt.db.one("SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='mb_spine_prepared'", (run["id"],))["attrs"]
    assert prepared["stop"] and prepared["projected_bytes"] > prepared["limit_bytes"] == 600000
    assert prepared["narrowing_due"] and prepared["projected_write_share"] > 0.3


def resolution_inputs(databases, rows):
    """mb_resolve's input relation, as the hourly chain builds it, and empty output tables."""
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS intermediate")
        conn.execute("DROP TABLE IF EXISTS intermediate.int_identity__resolution_inputs")
        conn.execute("CREATE TABLE intermediate.int_identity__resolution_inputs (platform text, platform_track_id text, title text, "
                     "artist_names text, duration_ms bigint, platform_album_id text, input_ref text, input_version text, "
                     "fields_hash text, reference_version text, retry_week text, first_landed_seq bigint)")
        for n, (track, title, artist, duration, album) in enumerate(rows):
            conn.execute("INSERT INTO intermediate.int_identity__resolution_inputs VALUES "
                         "('spotify',%s,%s,%s,%s,%s,%s,'v1','f','r','2026-W39',%s)",
                         (track, title, json.dumps([artist]), duration, album, "ref-" + track, n))
        conn.execute("GRANT USAGE ON SCHEMA intermediate TO service_read")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA intermediate TO service_read")
        for table in ("mb_resolve", "mb_resolve_closure"):
            conn.execute(f"DROP TABLE IF EXISTS raw.{table}")


async def test_mb_resolve_lands_answers_and_their_closure_once(rt, databases, mirror, monkeypatch):
    """A resolved answer lands one raw.mb_resolve row and the spine rows it touched in
    raw.mb_resolve_closure, keyed per row; a negative lands no closure. A later run reuses both."""
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    mb.reset_lookup_connection()
    resolution_inputs(databases, [("f1", "Synthetic Song Alpha", "Fixture Artist Alpha", 200400, "SynthAlbum000000000001"),
                                  ("n1", "Nothing", "Nobody", 100000, None)])

    def landed():
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            answers = conn.execute("SELECT input_ref, status FROM raw.mb_resolve ORDER BY 1").fetchall()
            touched = conn.execute("SELECT input_ref, mb_table, mb_key, mb_generation FROM raw.mb_resolve_closure ORDER BY 2, 3").fetchall()
        return answers, touched

    _, run = await bound(rt, "mb_resolve")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    answers, touched = landed()
    assert answers == [("ref-f1", "resolved"), ("ref-n1", "unmatched")]
    assert {(t, k) for _, t, k, _ in touched} == {
        ("recording", "102"), ("isrc", "2"), ("release", "201"), ("release_group", "301"), ("artist_credit", "1"),
        ("artist_credit_name", "1:0"), ("artist", "1"), ("url_link", "release:1")}
    assert {(ref, gen) for ref, _, _, gen in touched} == {("ref-f1", G1)}
    rt.cycles.close(run["cycle_id"])
    # A later closed cycle's run finds both completions in its manifest and looks nothing up again.
    later = await rt.cycles.bind_cycle("hourly", "global", "local:" + uuid4().hex, "scheduled", "local:hourly", runner="core")
    rt.cycles.close(later["cycle_id"])
    again = rt.admit("mb_resolve", cadence="hourly", dbt_run_id=later["dbt_run_id"])
    await rt.execute(again["id"])
    assert landed() == (answers, touched)


async def test_a_mirror_error_rejects_one_input_and_the_run_ends_partial(rt, databases, mirror, monkeypatch):
    """A promote terminates mirror backends mid-run: that input is a typed reject, the others land, the run
    ends partial (never failed), and the next run looks the rejected input up again."""
    from mdp_functions.sources.mb_resolve import function as resolve_fn

    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    mb.reset_lookup_connection()
    resolution_inputs(databases, [("f1", "Synthetic Song Alpha", "Fixture Artist Alpha", 200400, "SynthAlbum000000000001"),
                                  ("h1", "Synthetic Song Beta", "Fixture Artist Beta", 200000, None),
                                  ("n1", "Nothing", "Nobody", 100000, None)])
    original, calls = resolve_fn.lookup, []

    def promote_midway(row):
        calls.append(row["platform_track_id"])
        if len(calls) == 2:
            # promote.sql: every mirror backend of the old generation is terminated.
            with psycopg.connect(mirror["admin"], autocommit=True) as admin:
                admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                              "WHERE usename = 'mb_reader' AND pid <> pg_backend_pid()")
        return original(row)

    monkeypatch.setattr(resolve_fn, "lookup", promote_midway)
    _, run = await bound(rt, "mb_resolve")
    await rt.execute(run["id"])
    status = rt.db.one("SELECT status, error_class FROM control.run WHERE id=%s", (run["id"],))
    assert status == {"status": "partial", "error_class": "vendor_retryable"}
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        landed = conn.execute("SELECT input_ref FROM raw.mb_resolve ORDER BY 1").fetchall()
    assert landed == [("ref-f1",), ("ref-n1",)]
    # The rejected input has no completion, so the next run looks it up again; the others are reused.
    rt.cycles.close(run["cycle_id"])
    later = await rt.cycles.bind_cycle("hourly", "global", "local:" + uuid4().hex, "scheduled", "local:hourly", runner="core")
    rt.cycles.close(later["cycle_id"])
    again = rt.admit("mb_resolve", cadence="hourly", dbt_run_id=later["dbt_run_id"])
    await rt.execute(again["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (again["id"],))["status"] == "succeeded"
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        landed = conn.execute("SELECT input_ref FROM raw.mb_resolve ORDER BY 1").fetchall()
    assert landed == [("ref-f1",), ("ref-h1",), ("ref-n1",)] and calls == ["f1", "h1", "n1", "h1"]


async def test_a_brief_mirror_refusal_rejects_no_input(rt, databases, mirror, monkeypatch):
    """Connects refused for a moment are retried inside one input's lookup, so the circuit never counts
    them and every input lands."""
    real, calls = psycopg.connect, []

    def refused_twice(*args, **kwargs):
        if args and args[0] == mirror["url"]:
            calls.append(1)
            if len(calls) <= 2:
                raise psycopg.OperationalError("connection refused")
        return real(*args, **kwargs)

    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    monkeypatch.setattr(mb, "CONNECT_BACKOFF_S", (0, 0))
    monkeypatch.setattr(mb.psycopg, "connect", refused_twice)
    mb.reset_lookup_connection()
    resolution_inputs(databases, [("f1", "Synthetic Song Alpha", "Fixture Artist Alpha", 200400, "SynthAlbum000000000001"),
                                  ("h1", "Synthetic Song Beta", "Fixture Artist Beta", 200000, None),
                                  ("n1", "Nothing", "Nobody", 100000, None)])
    _, run = await bound(rt, "mb_resolve")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status, rows_rejected FROM control.run WHERE id=%s", (run["id"],)) == \
        {"status": "succeeded", "rows_rejected": 0}
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute("SELECT count(*) FROM raw.mb_resolve").fetchone()[0] == 3
    assert len(calls) == 3


async def test_a_mirror_down_for_the_whole_run_rejects_every_input(rt, databases, monkeypatch):
    """Zero delivery fails even when partial work is allowed; no input is parked for an outage."""
    from mdp_functions.sources.mb_resolve import function as resolve_fn

    monkeypatch.setenv("MDP_MB_DB_URL", "postgresql://mb_reader:mb_reader@127.0.0.1:1/musicbrainz_db")
    monkeypatch.setattr(mb, "CONNECT_BACKOFF_S", (0, 0))
    mb.reset_lookup_connection()
    resolution_inputs(databases, [(f"d{n}", "Song", "Someone", 180000, None) for n in range(6)])
    calls = []
    original = resolve_fn.lookup
    monkeypatch.setattr(resolve_fn, "lookup", lambda row: calls.append(row) or original(row))
    _, run = await bound(rt, "mb_resolve")
    await rt.execute(run["id"])
    status = rt.db.one("SELECT status, error_class FROM control.run WHERE id=%s", (run["id"],))
    assert status == {"status": "failed", "error_class": "vendor_retryable"} and len(calls) == 3
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        # Nothing landed: the output table was never created.
        assert conn.execute("SELECT to_regclass('raw.mb_resolve')").fetchone()[0] is None
    rejected = rt.db.one("SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='inputs_rejected'", (run["id"],))
    # A run whose circuit opened parks nothing: the mirror, not the inputs, failed.
    assert rejected["attrs"] == {"error_class": "vendor_retryable", "rejected": 6, "parkable": [], "drift": []}


async def test_crosswalk_searches_once_per_input_and_lands_negatives(rt, databases):
    from mdp_functions.layers import Ctx
    from mdp_functions.registry import REGISTRY
    from mdp_functions.sources.track_isrc_crosswalk.function import track_isrc_crosswalk

    seen = []

    async def deezer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        data = [{"id": 1, "title": "Fixture item 56802", "duration": 202, "isrc": "USXXX2600001",
                 "artist": {"name": "Fixture Artist Alpha"}}] if "Fixture item 56802" in request.url.params["q"] else []
        return httpx.Response(200, json={"data": data})

    ctx = Ctx(REGISTRY["track_isrc_crosswalk"], {"id": uuid4(), "cycle_id": uuid4()})
    ctx.http = httpx.AsyncClient(transport=httpx.MockTransport(deezer))
    rows = [
        {"platform": "spotify", "platform_track_id": "SynthTrack000000000001", "title": "Fixture item 56802",
         "artist_names": '["Fixture Artist Alpha"]', "duration_ms": 202460, "input_ref": "r1", "input_version": "v1"},
        {"platform": "spotify", "platform_track_id": "zz", "title": "Nothing Here",
         "artist_names": '["Nobody"]', "duration_ms": 100000, "input_ref": "r2", "input_version": "v2"},
    ]
    out = [r async for r in track_isrc_crosswalk(ctx, rows)]
    assert [(r["status"], r["isrc"], r["confidence"]) for r in out] == [("resolved", "USXXX2600001", 1.0), ("unmatched", None, None)]
    assert seen == ["Fixture Artist Alpha Fixture item 56802", "Nobody Nothing Here"]


# Catalog depth fixtures on the mirror: a long-running act (13 albums with a guest on one, one of them a
# compilation and one a live remix set, the earliest dated release in 1979 from release_unknown_country), an act
# with two 2025 singles, a merged-away gid, special purpose artists by MBID ([anonymous], [Disney], Various
# Artists) and by bracketed name, and an artist whose EP has no date and whose other group has no type.
CATALOG_ACT = "a0000000-0000-4000-a000-000000000901"
ACT = "00000000-0000-4000-a000-000000002025"
MERGED = "00000000-0000-4000-a000-000000009999"
GONE = "00000000-0000-4000-a000-000000000404"
VARIOUS = "89ad4ac3-39f7-470e-963a-56509c546377"
ANONYMOUS = "f731ccc4-e22a-43af-a747-64213329e088"
DISNEY = "66ea0139-149f-4a0c-8fbf-5ea9ec4a6e49"
BRACKETED = "00000000-0000-4000-a000-000000000907"
UNDATED = "00000000-0000-4000-a000-000000000906"
CATALOG_ROWS = f"""
INSERT INTO musicbrainz.artist (id, gid, name, sort_name, type) VALUES
  (901, '{CATALOG_ACT}', 'CATALOG_ACT', 'CATALOG_ACT', 2), (902, '{ACT}', 'Fixture Act', 'Act, Fixture', 1),
  (904, '00000000-0000-4000-a000-000000000904', 'Guest', 'Guest', 1), (905, '{VARIOUS}', 'Various Artists', 'Various Artists', 3),
  (906, '{UNDATED}', 'Undated', 'Undated', 1), (907, '{BRACKETED}', '[crowd noise]', '[crowd noise]', 3),
  (908, '{ANONYMOUS}', '[anonymous]', '[anonymous]', 3), (909, '{DISNEY}', '[Disney]', '[Disney]', 3)
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.artist_credit (id, name, artist_count, gid) VALUES
  (901, 'CATALOG_ACT', 1, gen_random_uuid()), (902, 'Fixture Act', 1, gen_random_uuid()),
  (903, 'CATALOG_ACT feat. Guest', 2, gen_random_uuid()), (905, 'Various Artists', 1, gen_random_uuid()),
  (906, 'Undated', 1, gen_random_uuid()), (908, '[anonymous]', 1, gen_random_uuid())
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.artist_credit_name VALUES (901, 0, 901, 'CATALOG_ACT', ''), (902, 0, 902, 'Fixture Act', ''),
  (903, 0, 901, 'CATALOG_ACT', ' feat. '), (903, 1, 904, 'Guest', ''), (905, 0, 905, 'Various Artists', ''),
  (906, 0, 906, 'Undated', ''), (908, 0, 908, '[anonymous]', '')
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.artist_gid_redirect (gid, new_id) VALUES ('{MERGED}', 902) ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.release_group (id, gid, name, artist_credit, type)
SELECT 9000 + i, gen_random_uuid(), 'Album ' || i, CASE WHEN i = 13 THEN 903 ELSE 901 END, 1 FROM generate_series(1, 13) i
UNION ALL SELECT 9021, gen_random_uuid(), 'Single A', 902, 2 UNION ALL SELECT 9022, gen_random_uuid(), 'Single B', 902, 2
UNION ALL SELECT 9050, gen_random_uuid(), 'Compilation', 905, 1 UNION ALL SELECT 9060, gen_random_uuid(), 'Undated EP', 906, 3
UNION ALL SELECT 9061, gen_random_uuid(), 'Untyped', 906, NULL UNION ALL SELECT 9080, gen_random_uuid(), 'Anonymous', 908, 1
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.release_group_secondary_type_join (release_group, secondary_type) VALUES (9011, 1), (9012, 6), (9012, 7)
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.release (id, gid, name, artist_credit, release_group, status)
SELECT 9100 + i, gen_random_uuid(), 'Release ' || i, 901, 9000 + i, 1 FROM generate_series(1, 13) i
UNION ALL SELECT 9121, gen_random_uuid(), 'Single A', 902, 9021, 1 UNION ALL SELECT 9122, gen_random_uuid(), 'Single B', 902, 9022, 1
UNION ALL SELECT 9160, gen_random_uuid(), 'Undated EP', 906, 9060, 1
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.release_country (release, country, date_year)
SELECT 9100 + i, 1, 1979 + i FROM generate_series(1, 12) i
UNION ALL SELECT 9121, 1, 2025 UNION ALL SELECT 9121, 2, 2025 UNION ALL SELECT 9122, 1, 2025
ON CONFLICT DO NOTHING;
INSERT INTO musicbrainz.release_unknown_country (release, date_year) VALUES (9113, 1979) ON CONFLICT DO NOTHING;
"""


@pytest.mark.docker
def test_catalog_depth_counts_release_groups_by_kind_with_the_first_dated_release(mirror):
    with psycopg.connect(mirror["admin"], autocommit=True) as admin:
        admin.execute(CATALOG_ROWS)
    conn = connect(mirror["url"])
    conn.autocommit = True
    gids = (CATALOG_ACT, ACT, MERGED, GONE, VARIOUS, ANONYMOUS, DISNEY, BRACKETED, UNDATED)
    reading = {gid: mb.artist_catalog(conn, gid) for gid in gids}
    conn.close()
    kinds = {gid: [(g["primary_type"], g["secondary_types"], g["release_groups"], g["first_release_year"]) for g in groups]
             for gid, (_, groups) in reading.items()}
    # The guest credit counts as one of CATALOG_ACT's albums; secondary types join sorted with ';'; a dateless release
    # reads no year, never 0; a group with no type has an empty primary_type.
    assert kinds == {
        CATALOG_ACT: [("Album", "", 11, 1979), ("Album", "Compilation", 1, 1990), ("Album", "Live;Remix", 1, 1991)],
        ACT: [("Single", "", 2, 2025)], MERGED: [("Single", "", 2, 2025)], GONE: [], VARIOUS: [], ANONYMOUS: [],
        DISNEY: [], BRACKETED: [], UNDATED: [("", "", 1, None), ("EP", "", 1, None)]}
    status = {gid: lookup["status"] for gid, (lookup, _) in reading.items()}
    assert status == {CATALOG_ACT: "found", ACT: "found", MERGED: "found", GONE: "not_found", VARIOUS: "special_purpose",
                      ANONYMOUS: "special_purpose", DISNEY: "special_purpose", BRACKETED: "special_purpose",
                      UNDATED: "found"}
    assert reading[MERGED][0]["artist_gid"] == ACT and reading[GONE][0]["artist_gid"] is None
    assert {lookup["mb_generation"] for lookup, _ in reading.values()} == {G1}


def test_every_documented_special_purpose_artist_and_any_bracketed_name_is_excluded():
    assert len(mb.SPECIAL_PURPOSE_ARTISTS) == 12
    assert mb.special_purpose(ANONYMOUS, "[anonymous]") and mb.special_purpose(DISNEY, "Disney")
    assert mb.special_purpose(BRACKETED, " [crowd noise] ") and not mb.special_purpose(CATALOG_ACT, "CATALOG_ACT")


def catalog_inputs(databases, gids, week="2026-09-21"):
    """mb_artist_catalog's input relation, as the daily job builds it, and empty output tables."""
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS intermediate")
        conn.execute("DROP TABLE IF EXISTS intermediate.int_artist_catalog__inputs")
        conn.execute("CREATE TABLE intermediate.int_artist_catalog__inputs (mb_artist_gid text, catalog_week date, "
                     "_source_keys text, input_ref text, input_version text)")
        for gid in gids:
            conn.execute("INSERT INTO intermediate.int_artist_catalog__inputs VALUES (%s,%s,'[\"mb_spine\"]',%s,%s)",
                         (gid, week, "ref-" + gid, "v-" + gid + week))
        conn.execute("GRANT USAGE ON SCHEMA intermediate TO service_read")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA intermediate TO service_read")
        for table in ("mb_artist_catalog", "mb_artist_release_groups"):
            conn.execute(f"DROP TABLE IF EXISTS raw.{table}")


@pytest.mark.docker
async def test_mb_artist_catalog_lands_one_learning_eligible_reading_per_artist_once(rt, databases, mirror, monkeypatch):
    """Each artist lands one lookup row, negatives included, and its release groups by kind, with the spine as
    their only input lineage, so the readings stay learning-eligible. A later cycle in the same week reads
    nothing again."""
    with psycopg.connect(mirror["admin"], autocommit=True) as admin:
        admin.execute(CATALOG_ROWS)
    with psycopg.connect(databases["admin_control"]) as conn:
        for source in ("mb_spine", "mb_artist_catalog"):
            conn.execute("INSERT INTO control.rights_source(source_key,provider,category,learning_eligible) "
                         "VALUES (%s,'MusicBrainz','A',true) ON CONFLICT(source_key) DO UPDATE SET learning_eligible=true",
                         (source,))
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    mb.reset_lookup_connection()
    catalog_inputs(databases, [CATALOG_ACT, ACT, GONE])

    def landed():
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            lookups = conn.execute("SELECT mb_artist_gid, status, catalog_week, learning_eligible, _source_keys::text "
                                   "FROM raw.mb_artist_catalog ORDER BY 1").fetchall()
            groups = conn.execute("SELECT mb_artist_gid, primary_type, secondary_types, release_groups, first_release_year, "
                                  "learning_eligible FROM raw.mb_artist_release_groups ORDER BY 1, 2, 3").fetchall()
        return lookups, groups

    _, run = await bound(rt, "mb_artist_catalog")
    await rt.execute(run["id"])
    assert rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"] == "succeeded"
    lookups, groups = landed()
    week = "2026-09-21"
    assert [r[:3] for r in lookups] == [(GONE, "not_found", week), (ACT, "found", week), (CATALOG_ACT, "found", week)]
    assert {(r[3], r[4]) for r in lookups} == {(True, '["mb_spine"]')}
    assert groups == [(ACT, "Single", "", 2, 2025, True), (CATALOG_ACT, "Album", "", 11, 1979, True),
                      (CATALOG_ACT, "Album", "Compilation", 1, 1990, True), (CATALOG_ACT, "Album", "Live;Remix", 1, 1991, True)]
    rt.cycles.close(run["cycle_id"])
    later = await rt.cycles.bind_cycle("daily", "global", "local:" + uuid4().hex, "scheduled", "local:daily", runner="core")
    rt.cycles.close(later["cycle_id"])
    again = rt.admit("mb_artist_catalog", cadence="daily", dbt_run_id=later["dbt_run_id"])
    await rt.execute(again["id"])
    assert landed() == (lookups, groups)


async def test_a_malformed_gid_is_rejected_before_the_mirror():
    from mdp_functions.layers import Ctx
    from mdp_functions.registry import REGISTRY
    from mdp_functions.sources.mb_artist_catalog.function import mb_artist_catalog

    ctx = Ctx(REGISTRY["mb_artist_catalog"], {"id": uuid4(), "cycle_id": uuid4()})
    await mb_artist_catalog(ctx, [{"mb_artist_gid": "CATALOG_ACT", "input_ref": "r1", "input_version": "v1"}])
    assert [r["reason"] for r in ctx.rejected] == ["invalid_gid"] and ctx.outputs == {}


async def test_a_missing_mirror_url_rejects_the_input_as_vendor_4xx(monkeypatch):
    """Without MDP_MB_DB_URL the lookup refuses before any connect: the runtime rejects the input as vendor_4xx
    and its circuit opens after three, while movement keeps building from older readings (next test)."""
    from mdp_functions.layers import Ctx
    from mdp_functions.registry import REGISTRY
    from mdp_functions.sources.mb_artist_catalog.function import mb_artist_catalog

    monkeypatch.delenv("MDP_MB_DB_URL", raising=False)
    mb.reset_lookup_connection()
    ctx = Ctx(REGISTRY["mb_artist_catalog"], {"id": uuid4(), "cycle_id": uuid4()})
    with pytest.raises(ServiceError) as refused:
        await mb_artist_catalog(ctx, [{"mb_artist_gid": CATALOG_ACT, "input_ref": "r1", "input_version": "v1"}])
    assert refused.value.error_class == "vendor_4xx" and ctx.outputs == {}


def test_movement_never_waits_on_the_catalog_lookup():
    """The lookup's invoke has one dependent, the receipts table; staging reads landed rows with no edge to it,
    so a failed or disabled lookup never skips a movement mart."""
    import subprocess

    def models(select):
        return set(subprocess.run(
            ["uv", "run", "--project", "dbt", "dbt", "ls", "--project-dir", "dbt", "--profiles-dir", "dbt/profiles",
             "--target", "ci", "--select", select, "--resource-type", "model", "--output", "name", "--quiet"],
            cwd=REPO, capture_output=True, text=True, check=True).stdout.split())

    movement = models("+mart_early_signals_current +mart_top_movers_current +mart_arrivals_current")
    assert {"int_song_age__daily", "stg_mb__artist_catalog", "stg_mb__artist_release_groups"} <= movement
    assert "gold_invoke__mb_artist_catalog" not in movement
    assert models("gold_invoke__mb_artist_catalog+") == {"gold_invoke__mb_artist_catalog", "int_artist_catalog__receipts"}
