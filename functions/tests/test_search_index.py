"""Build the Library contract on both engines. Run pytest on this file to check it."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from identity_harness import Warehouse

pytestmark = pytest.mark.docker
DAY = datetime(2026, 9, 25, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def inputs(conn, prefix):
    """Small upstream facts distinguish real presence from a zero-filled song day."""
    statements = [
        (
            "insert into {i}.int_song_key__daily(platform,platform_track_id,song_key,title_text,artist_text,primary_artist_id,primary_artist_key,source_keys) values "
            "('apple','a','apple:a','Night song','Test act','artist-a','apple:artist-a','[\"am_playlist\"]'),"
            "('spotify','b','spotify:b','Night copy','Test act','artist-b','artist-mbid','[\"sp_playlist\"]'),"
            "('apple','old','apple:old','Old song','Old act','old-artist','apple:old-artist','[\"am_playlist\"]'),"
            "('apple','zero','apple:zero','Absent song','Absent act','zero-artist','apple:zero-artist','[\"am_playlist\"]')"
        ),
        "insert into {m}.mart_song_cluster_members(song_key,cluster_key,representative_song_key,cluster_methods) values ('apple:a','apple:a','apple:a','[]'),('spotify:b','apple:a','apple:a','[]')",
        (
            "insert into {m}.mart_song_day(song_key,day,list_count,shazam_best_position,source_keys,playlists_observed,shazam_observed,streams_observed) values "
            "('apple:a','2026-09-25',1,null,'[\"am_playlist\"]',true,false,false),('spotify:b','2026-09-24',null,1,'[\"sp_playlist\"]',false,true,false),"
            "('apple:old','2026-06-26',1,null,'[\"am_playlist\"]',true,false,false),('apple:zero','2026-09-25',0,null,'[\"am_playlist\"]',true,true,false)"
        ),
        "insert into {m}.mart_song_aliases(alias_key,song_key) values ('isrc:OLD','spotify:b')",
        "insert into {i}.int_artist_identity(platform,platform_artist_id,candidate_count,mb_artist_gid,mb_artist_name,wikidata_qid) values ('spotify','artist-b',1,'artist-mbid','Test act','Q123')",
        (
            "insert into {m}.mart_playlist_profile(platform,playlist_id,variant,stream,snapshot_id,observed_at,title,source_keys) values "
            "('apple','a','us','full','first','2026-09-24','Earlier playlist','[\"am_playlist\"]'),"
            "('apple','a','gb','full','second','2026-09-25','Night playlist','[\"am_playlist\"]')"
        ),
        # An operator's artist label is not evidence that an account is public.
        "insert into {m}.mart_shazam_chart_daily(chart,chart_date,position,country,city,chart_type,source_keys,apple_song_id,observed_at,learning_eligible,resale_permitted) values ('shazam:city:US:test','2026-09-25',1,'US','Test city','city','[\"sz_chart\"]','a','2026-09-25',false,false)",
        "insert into {m}.mart_chart_history(chart_name,chart_week,chart_position,source_keys) values ('hot-100','2026-09-25',1,'[\"billboard_hot100\"]')",
    ]
    statements.extend(
        [
            "insert into {m}.mart_shazam_chart_daily(chart,chart_date,position,country,city,chart_type,source_keys,apple_song_id,observed_at,learning_eligible,resale_permitted) values ('shazam:city:US:test','2026-09-24',1,'US','Older city name','city','[\"sz_chart\"]','a','2026-09-24',false,false)",
            "insert into {i}.int_identity__track_inputs_daily(platform,platform_track_id,platform_artist_ids,artist_names) values ('apple_music','a','[\"artist-a\",\"guest\"]','[\"Test act\",\"Guest act\"]')",
            "insert into {i}.int_artist_identity(platform,platform_artist_id,candidate_count,mb_artist_gid,mb_artist_name,wikidata_qid) values ('apple_music','artist-a',2,null,null,null)",
        ]
    )
    for statement in statements:
        conn.execute(statement.format(i=prefix + "intermediate", m=prefix + "marts"))


def check(conn, schema):
    rows = conn.execute(
        f"select object_key,kind,display_text,context,aliases,last_seen,learning_eligible,resale_permitted,source_keys from {schema}.mart_search_index order by object_key"
    ).fetchall()
    by_key = {row[0]: row for row in rows}
    assert all(row[1] != "account" for row in rows)
    for private_text in ("private-account", "private_fixture", "Private fixture"):
        assert private_text not in str(rows)
    assert len(rows) == len(by_key) == 7
    assert {row[1] for row in rows} == {
        "song",
        "artist",
        "playlist",
        "chart",
    }
    assert "song:apple:old" not in by_key
    assert "song:apple:zero" not in by_key
    song = by_key["song:apple:a"]
    assert song[2] == "Night song"
    assert json.loads(song[4]) == ["apple:a", "isrc:OLD", "spotify:b"]
    assert song[6:8] == (False, False)
    assert {"am_playlist", "sp_playlist"}.issubset(json.loads(song[8]))
    assert by_key["playlist:apple:a"][2] == "Night playlist"
    assert json.loads(by_key["artist:artist-mbid"][3])["wikidata_qid"] == "Q123"
    assert "artist:apple:old-artist" not in by_key
    assert by_key["artist:apple:guest"][2] == "Guest act"
    assert "artist:apple:artist-a" in by_key
    assert by_key["chart:shazam:city:US:test"][2] == "Test city Shazam city"
    return [
        (row[0], row[1], row[2], json.loads(row[3]), json.loads(row[4])) for row in rows
    ]


def test_library_contract_on_both_engines(tmp_path):
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Set MDP_CONTROL_ADMIN_URL to a disposable Postgres server.")
    wh = Warehouse(admin, tmp_path)
    try:
        with wh.connect() as conn:
            conn.execute("create extension if not exists pg_trgm")
        cycle = wh.cycle("daily", 1, DAY)
        print(wh.dbt(cycle, "*", command="seed"))
        print(wh.dbt(cycle, "+mart_search_index"))
        with wh.connect() as conn:
            inputs(conn, "")
        print(wh.dbt(cycle, "mart_search_index"))
        with wh.connect() as conn:
            expected = check(conn, "marts")
            indexes = conn.execute(
                "select indexdef from pg_indexes where tablename='mart_search_index'"
            ).fetchall()
            assert any(
                "gin (lower(display_text) gin_trgm_ops)" in row[0] for row in indexes
            )
        # A second table swap must create a fresh index while the old table still exists.
        print(wh.dbt(cycle, "mart_search_index"))
        path = tmp_path / "search.duckdb"
        env = {**os.environ, "MDP_CI_DB": str(path), "DBT_MDP_SCOPE": "global"}
        duck_profiles = tmp_path / "duck-profiles"
        duck_profiles.mkdir()
        (duck_profiles / "profiles.yml").write_text(
            (ROOT / "dbt/profiles/profiles.example.yml").read_text()
        )
        settings = [
            "--project-dir",
            "dbt",
            "--profiles-dir",
            str(duck_profiles),
            "--target",
            "ci",
            "--target-path",
            str(tmp_path / "duck-target"),
            "--log-path",
            str(tmp_path / "duck-logs"),
            "--vars",
            json.dumps({"cycle_opened_at": DAY.isoformat()}),
        ]

        def run(args):
            result = subprocess.run(
                ["uv", "run", "--project", "dbt", "dbt", *args, *settings],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            print(result.stdout)
            assert result.returncode == 0, result.stderr

        run(
            [
                "build",
                "--select",
                "+mart_search_index",
                "--indirect-selection",
                "cautious",
            ]
        )
        with duckdb.connect(str(path)) as conn:
            inputs(conn, "main_")
        run(["build", "--select", "mart_search_index"])
        with duckdb.connect(str(path)) as conn:
            assert check(conn, "main_marts") == expected
    finally:
        wh.drop()
