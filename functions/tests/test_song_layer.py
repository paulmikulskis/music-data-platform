"""Build song history with real manifests and check the empty current stamp on Postgres."""

import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import duckdb
import pytest
from identity_harness import Warehouse

pytestmark = pytest.mark.docker
DAY = datetime(2026, 9, 25, 12, tzinfo=UTC)
SELECT = (
    "+mart_song_day +mart_top_movers_current +mart_song_aliases "
    "+mart_song_cluster_members +mart_early_signals_current +mart_readiness +mart_arrivals_current"
)


def duckdb_fixture(wh, tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "songs.duckdb"
    env = {**os.environ, "MDP_CI_DB": str(path), "DBT_MDP_SCOPE": "global"}
    args = ["uv", "run", "--project", "dbt", "dbt"]
    settings = [
        "--project-dir",
        "dbt",
        "--profiles-dir",
        "dbt/profiles",
        "--target",
        "ci",
        "--indirect-selection",
        "cautious",
        "--target-path",
        str(tmp_path / "duck-target"),
        "--log-path",
        str(tmp_path / "duck-logs"),
    ]

    def run(command):
        result = subprocess.run(
            args + command + settings,
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        print(result.stdout)
        assert result.returncode == 0, result.stderr

    run(["run-operation", "bootstrap_raw"])
    with duckdb.connect(str(path)) as conn:
        for table in (
            "playlist_items",
            "playlist_snapshots",
            "shazam_chart_entries",
            "apple_song_durations",
        ):
            with wh.connect() as pg:
                result = pg.execute("select * from raw." + table)
                names = [column.name for column in result.description]
                records = result.fetchall()
            values = [
                [
                    json.dumps(v)
                    if isinstance(v, (list, dict))
                    else str(v)
                    if isinstance(v, UUID)
                    else v
                    for v in row
                ]
                for row in records
            ]
            if values:
                conn.executemany(
                    "insert into raw."
                    + table
                    + " ("
                    + ",".join('"' + n + '"' for n in names)
                    + ") values ("
                    + ",".join("?" for _ in names)
                    + ")",
                    values,
                )
    run(
        [
            "build",
            "--select",
            *SELECT.split(),
            "--vars",
            json.dumps({"cycle_opened_at": DAY.isoformat()}),
        ]
    )
    with duckdb.connect(str(path)) as conn:
        assert conn.execute(
            "select song_key from main_marts.mart_top_movers_current"
        ).fetchall() == [("apple:123",)]
        assert conn.execute(
            "select day,followers from main_intermediate.int_song_followers__daily where song_key='apple:123' order by day"
        ).fetchall() == [(date(2026, 9, 19), 100), (date(2026, 9, 20), 200)]
        assert conn.execute(
            "select duration_ms, duration_source_key from main_intermediate.int_song_cluster_inputs__daily where platform_track_id='456'"
        ).fetchall() == [(180123, "apple_song_duration")]
        assert conn.execute(
            "select method from main_intermediate.int_song_cluster_edges__daily where left_key='apple:456' and right_key='spotify:duration-peer'"
        ).fetchall() == [("title_artist_duration",)]
        assert conn.execute(
            "select duration_ms from main_intermediate.int_song_cluster_inputs__daily where platform_track_id='123'"
        ).fetchall() == [(200000,)]
        evidence = json.loads(
            conn.execute(
                "select evidence from main_marts.mart_top_movers_current"
            ).fetchone()[0]
        )
        assert evidence
        for entry in evidence:
            assert entry["cluster_key"] == "apple:123"
            assert entry["member_song_keys"] == ["apple:123"]
            assert entry["cluster_methods"] == []
        print(
            "DuckDB fixture: one mover, two historical follower days. Open the dbt build log for timings."
        )


def test_song_history_and_empty_current_build(tmp_path):
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Set MDP_CONTROL_ADMIN_URL to a disposable Postgres server.")
    wh = Warehouse(admin, tmp_path)
    try:
        cycle = wh.cycle("daily", 1, DAY)
        wh.dbt(cycle, "*", command="seed")
        track = {
            "id": "123",
            "title": "Fixture song",
            "artists": ["Fixture act"],
            "duration": 200000,
        }
        wh.land(
            "raw.apple_song_durations",
            [
                {
                    "apple_song_id": "123",
                    "duration_ms": 999000,
                    "status": "found",
                    "observed_at": DAY,
                },
                {
                    "apple_song_id": "456",
                    "duration_ms": 180123,
                    "status": "found",
                    "observed_at": DAY,
                },
            ],
            1,
            "apple_song_duration",
        )
        wh.observation(
            "spotify",
            "duration-peer-list",
            DAY,
            [
                {
                    "id": "duration-peer",
                    "title": "Chart-only fixture",
                    "artists": ["Fixture act"],
                    "duration": 180123,
                }
            ],
            "editorial",
            1,
        )
        # The first read proves absence. The next read proves an add, then removal.
        for offset, tracks, followers in [
            (-7, [], 100),
            (-6, [track], 100),
            (-5, [track], 200),
            (-4, [], 300),
            (-3, [], 300),
            (-2, [], 300),
            (-1, [], 300),
            (0, [], 300),
        ]:
            wh.observation(
                "apple_music",
                "fixture-list",
                DAY + timedelta(days=offset),
                tracks,
                "editorial",
                1,
                followers=followers,
            )
        for offset, tracks in [(-2, []), (-1, [dict(track, id="789")])]:
            wh.observation(
                "apple_music",
                "early-list",
                DAY + timedelta(days=offset),
                tracks,
                "editorial",
                1,
                followers=100,
            )
        charts = []
        for offset, chart in [
            (-7, "shazam:top-50:US:one"),
            (0, "shazam:top-50:US:one"),
            (0, "shazam:top-50:US:two"),
        ]:
            charts.append(
                {
                    "chart": chart,
                    "chart_date": (DAY + timedelta(days=offset)).date(),
                    "position": 1,
                    "apple_song_id": "123",
                    "title_text": "Fixture song",
                    "artist_text": "Fixture act",
                    "observed_at": DAY + timedelta(days=offset),
                }
            )
        for offset in range(-26, 0):
            charts.append(
                {
                    "chart": "shazam:top-50:US:two",
                    "chart_date": (DAY + timedelta(days=offset)).date(),
                    "position": 2,
                    "apple_song_id": "456",
                    "title_text": "Chart-only fixture",
                    "artist_text": "Fixture act",
                    "observed_at": DAY + timedelta(days=offset),
                }
            )
        wh.land("raw.shazam_chart_entries", charts, 1, "sz_chart")
        # A missing Apple header keeps the last observed class and title.
        header_id = "pl.abcdefabcdefabcdefabcdefabcdefab"
        for offset in (-1, 0):
            wh.observation(
                "apple_music",
                header_id,
                DAY + timedelta(days=offset),
                [],
                "editorial",
                1,
            )
        with wh.connect() as conn:
            conn.execute(
                "UPDATE raw.playlist_snapshots SET owner_class_observed='editorial', owner_class='chart', owner_name='Apple Music', title='Fixture editorial' WHERE playlist_id=%s",
                (header_id,),
            )
            conn.execute(
                "UPDATE raw.playlist_snapshots SET owner_class_observed='unknown', owner_class='unknown', owner_name=NULL, title=NULL WHERE playlist_id=%s AND observed_at::date='2026-09-25'",
                (header_id,),
            )
        print(wh.dbt(cycle, SELECT))
        assert wh.query(
            "SELECT title,owner_class,owner_name FROM marts.mart_playlist_profile WHERE playlist_id=%s ORDER BY observed_at DESC LIMIT 1",
            (header_id,),
        ) == [("Fixture editorial", "chart", "Apple Music")]

        assert wh.query(
            "select song_key from intermediate.int_song_key__daily where platform='apple' and platform_track_id='123'"
        ) == [("apple:123",)]
        assert wh.query(
            "select day, followers from intermediate.int_song_followers__daily where song_key='apple:123' order by day"
        ) == [(date(2026, 9, 19), 100), (date(2026, 9, 20), 200)]
        assert wh.query("select count(*) from marts.mart_top_movers_current") == [(1,)]
        assert wh.query(
            "select family, song_key, value from marts.mart_early_signals_current"
        ) == [("playlists", "apple:789", 3.0)]
        early_evidence = json.loads(
            wh.query("select evidence from marts.mart_early_signals_current")[0][0]
        )
        assert early_evidence
        assert all(e["component"] == "playlist_adds" for e in early_evidence)
        assert all(e["row_key"]["event_type"] == "add" for e in early_evidence)
        assert all(e["input_build"]["cycle_id"] == cycle["id"] for e in early_evidence)
        assert wh.query(
            "select song_key from intermediate.int_song_key__daily where platform_track_id='456'"
        ) == [("apple:456",)]
        assert wh.query(
            "select new_entry from intermediate.int_song_shazam__daily where song_key='apple:123' and chart='shazam:top-50:US:two'"
        ) == [(1,)]
        evidence = json.loads(
            wh.query("select evidence from marts.mart_top_movers_current")[0][0]
        )
        assert evidence and all(isinstance(entry, dict) for entry in evidence)
        for entry in evidence:
            assert entry["cluster_key"] == "apple:123"
            assert entry["member_song_keys"] == ["apple:123"]
            assert entry["cluster_methods"] == []
        additions = [
            entry for entry in evidence if entry["component"] == "playlist_adds"
        ]
        assert additions
        for entry in additions:
            key = entry["row_key"]
            assert key["event_type"] == "add"
            assert wh.query(
                "select owner_class from marts.mart_playlist_events where platform=%s "
                "and playlist_id=%s and variant=%s and stream=%s and occurrence_key=%s "
                "and interval_id=%s and event_type=%s and observed_at=%s",
                tuple(
                    key[name]
                    for name in (
                        "platform",
                        "playlist_id",
                        "variant",
                        "stream",
                        "occurrence_key",
                        "interval_id",
                        "event_type",
                        "observed_at",
                    )
                ),
            ) == [("editorial",)]
        assert all(
            entry["input_build"]["cycle_id"] == cycle["id"] for entry in evidence
        ), [(e["relation"], e["input_build"]) for e in evidence]
        assert all(
            isinstance(entry["input_build"]["close_no"], str) for entry in evidence
        )
        assert wh.query(
            "select cycle_id from marts._build where relation='marts.mart_top_movers_current'"
        ) == [(cycle["id"],)]
        # Unit fixtures run on the real Postgres adapter too.
        print(wh.dbt(cycle, "tag:song_fixture", command="test"))
        for model in (
            "int_song_key__daily",
            "int_song_followers__daily",
            "mart_song_day",
            "mart_top_movers",
            "mart_top_movers_current",
            "mart_song_aliases",
        ):
            schema = "intermediate" if model.startswith("int_") else "marts"
            print(model, wh.query(f"select count(*) from {schema}.{model}")[0][0])
        assert wh.query(
            "select duration_ms, duration_source_key from intermediate.int_song_cluster_inputs__daily where platform_track_id='123'"
        ) == [(200000, None)]
        assert wh.query(
            "select duration_ms, duration_source_key from intermediate.int_song_cluster_inputs__daily where platform_track_id='456'"
        ) == [(180123, "apple_song_duration")]
        assert "apple_song_duration" in json.loads(
            wh.query(
                "select source_keys from intermediate.int_song_cluster__daily where song_key='apple:456'"
            )[0][0]
        )
        assert wh.query(
            "select method from intermediate.int_song_cluster_edges__daily where left_key='apple:456' and right_key='spotify:duration-peer'"
        ) == [("title_artist_duration",)]
        duckdb_fixture(wh, tmp_path)
        # A newer dump beyond the bound close cannot change this cycle's duration.
        wh.land(
            "raw.apple_song_durations",
            [
                {
                    "apple_song_id": "456",
                    "duration_ms": 400000,
                    "status": "found",
                    "observed_at": DAY + timedelta(days=1),
                },
            ],
            99,
            "apple_song_duration",
        )
        print(wh.dbt(cycle, "stg_apple__song_durations int_song_cluster_inputs__daily"))
        assert wh.query(
            "select duration_ms from intermediate.int_song_cluster_inputs__daily where platform_track_id='456'"
        ) == [(180123,)]
        # Each reviewed count has a stored relation and a stamp from the same daily build.
        for relation in (
            "intermediate.int_artist_identity",
            "intermediate.int_cluster_shazam__daily",
            "intermediate.int_song_windows__daily",
            "staging.stg_playlist__snapshots",
            "staging.stg_shazam__chart_entries",
        ):
            assert wh.query(
                "select relkind from pg_class where oid=to_regclass(%s)", (relation,)
            ) == [("r",)]
            assert wh.query(
                "select cycle_id from marts._build where relation=%s", (relation,)
            ) == [(cycle["id"],)]
        empty_cycle = wh.cycle("daily", 1, DAY + timedelta(days=1))
        print(
            wh.dbt(
                empty_cycle,
                "mart_top_movers_current mart_early_signals_current mart_readiness",
            )
        )
        assert wh.query("select count(*) from marts.mart_early_signals_current") == [
            (0,)
        ]
        for model in ("mart_early_signals_current", "mart_readiness"):
            assert wh.query(
                "select cycle_id from marts._build where relation=%s",
                ("marts." + model,),
            ) == [(empty_cycle["id"],)]
        assert wh.query("select distinct day from marts.mart_readiness") == [
            (date(2026, 9, 26),)
        ]
        assert wh.query("select count(*) from marts.mart_top_movers_current") == [(0,)]
        assert wh.query(
            "select cycle_id from marts._build where relation='marts.mart_top_movers_current'"
        ) == [(empty_cycle["id"],)]
        assert wh.query("select count(*) from marts.mart_top_movers")[0][0] > 0
        # Keep an ISRC URL even when the source later corrects that code.
        wh.observation(
            "apple_music",
            "fixture-list",
            DAY + timedelta(minutes=15),
            [{**track, "isrc": "USAAA2600001"}],
            "editorial",
            2,
            followers=300,
        )
        with_code = wh.cycle("daily", 2, DAY + timedelta(minutes=20))
        print(wh.dbt(with_code, "+mart_song_aliases"))
        assert wh.query(
            "select song_key from marts.mart_song_aliases where alias_key='isrc:USAAA2600001'"
        ) == [("isrc:USAAA2600001",)]
        wh.observation(
            "apple_music",
            "fixture-list",
            DAY + timedelta(minutes=25),
            [{**track, "isrc": "USAAA2600002"}],
            "editorial",
            3,
            followers=300,
        )
        # Resolve the same source id without changing its old URL.
        wh.generation(
            "20260925-fixture",
            3,
            {
                "isrc": [],
                "redirect": [],
                "recording": [
                    {
                        "mb_key": "1",
                        "recording_id": 1,
                        "recording_gid": "00000000-0000-4000-a000-000000000001",
                        "name": "Fixture song",
                        "artist_credit_id": 1,
                    }
                ],
                "url_link": [
                    {
                        "mb_key": "recording:1",
                        "entity_type": "recording",
                        "entity_id": 1,
                        "link_id": 1,
                        "url_id": 1,
                        "url": "https://music.apple.com/us/song/123",
                        "url_platform": "apple_music",
                        "url_kind": "track",
                        "url_platform_id": "123",
                        "ended": False,
                    }
                ],
            },
        )
        resolved = wh.cycle("daily", 3, DAY + timedelta(hours=1))
        print(wh.dbt(resolved, "+mart_song_aliases"))
        assert wh.query(
            "select song_key from marts.mart_song_aliases where alias_key='apple:123'"
        ) == [("00000000-0000-4000-a000-000000000001",)]
        assert wh.query(
            "select song_key from marts.mart_song_aliases where alias_key='isrc:USAAA2600001'"
        ) == [("00000000-0000-4000-a000-000000000001",)]
    finally:
        wh.drop()
