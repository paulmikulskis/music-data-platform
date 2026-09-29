"""Synthetic outcomes and a real dbt replay prove the measurement boundaries."""

import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ops.backtest import evaluate, metrics, warehouse
from ops.backtest.extract import extract, initialize


def runner_fixture():
    return [
        {
            "cadence": cadence,
            "scope": "global",
            "started_at": datetime.fromisoformat(stamp),
            "closed_at": datetime.fromisoformat(stamp) + timedelta(minutes=1),
        }
        for cadence, stamp in [
            ("hourly", "2026-09-26T07:41:00+00:00"),
            ("daily", "2026-09-26T02:41:00+00:00"),
            ("weekly", "2026-09-21T03:41:00+00:00"),
        ]
    ]


@pytest.mark.parametrize(
    "stamp,allowed",
    [
        ("2026-09-26T08:38:00+00:00", True),
        ("2026-09-26T08:39:00+00:00", False),
        ("2026-09-26T08:41:00+00:00", False),
        ("2026-09-26T08:51:59+00:00", False),
        ("2026-09-26T08:52:00+00:00", True),
    ],
)
def test_capture_windows(stamp, allowed):
    assert (
        warehouse.quiet_seconds(datetime.fromisoformat(stamp), runner_fixture()) > 0
    ) == allowed


def test_capture_follows_moved_runner_and_fails_closed():
    starts = runner_fixture()
    now = datetime(2026, 9, 26, 8, 20, tzinfo=UTC)
    assert warehouse.quiet_seconds(now, starts) > 0
    starts[0]["started_at"] = now - timedelta(hours=1)
    starts[0]["closed_at"] = now - timedelta(minutes=59)
    assert warehouse.quiet_seconds(now, starts) == 0
    with pytest.raises(ValueError, match="runner starts"):
        warehouse.quiet_seconds(now, [])
    starts[0]["started_at"] -= timedelta(days=1)
    with pytest.raises(ValueError, match="runner starts"):
        warehouse.quiet_seconds(now, starts)


def test_read_dsn_avoids_secret_store_and_stays_on_read_proxy(monkeypatch):
    class Connection:
        def close(self):
            pass

    captured = {}

    def connect(dsn, **kwargs):
        captured.update(warehouse.conninfo_to_dict(dsn))
        return Connection()

    monkeypatch.setenv(
        "MDP_BACKTEST_READ_URL", "postgresql://service_read:fixture@ignored/warehouse"
    )
    monkeypatch.setattr(
        warehouse, "command", lambda *a, **k: pytest.fail("secret store must not run")
    )
    monkeypatch.setattr(warehouse.psycopg, "connect", connect)
    monkeypatch.setattr(warehouse, "await_quiet", lambda conn: None)
    warehouse.live_connection()
    assert captured["host"] == captured["hostaddr"] == "127.0.0.1"
    assert captured["port"] == "15471"
    assert "default_transaction_read_only=on" in captured["options"]
    monkeypatch.setenv(
        "MDP_BACKTEST_READ_URL", "postgresql://postgres:fixture@ignored/warehouse"
    )
    with pytest.raises(ValueError, match="service_read"):
        warehouse.live_connection()


def test_selection_dates_are_required_even_with_an_override(monkeypatch):
    state = {
        "methods": {
            "early": {"chosen_on": "2026-09-20"},
            "later": {"chosen_on": "2026-09-25"},
        }
    }
    for method in state["methods"].values():
        method["sha"] = "fixture"
    choices = json.loads(json.dumps(state["methods"]))
    monkeypatch.setattr(warehouse, "committed_choices", lambda: choices)
    assert evaluate.selection_cutoff(state, None) == date(2026, 9, 25)
    assert evaluate.selection_cutoff(state, "2026-09-01") == date(2026, 9, 25)
    assert evaluate.selection_cutoff(state, "2026-09-27") == date(2026, 9, 27)
    del state["methods"]["later"]["chosen_on"]
    with pytest.raises(ValueError, match="choices.json"):
        evaluate.selection_cutoff(state, "2026-09-27")


def test_manifest_is_table_and_scope_bound_and_rejects_open_cycles():
    cycle = {"id": "day-d", "manifest_mode": "stamp", "close_no": 4}
    predicate = warehouse.manifest_predicate(cycle, "raw.playlist_items").as_string()
    assert "scope='global'" in predicate and "close_no<=4" in predicate
    assert (
        "target_table='raw.playlist_items'" in predicate
        and "cycle_id='day-d'" in predicate
    )
    cycle["close_no"] = None
    with pytest.raises(ValueError):
        warehouse.manifest_predicate(cycle, "raw.playlist_items")
    cycle["manifest_mode"] = "list"
    assert (
        "dump_stamps"
        not in warehouse.manifest_predicate(cycle, "raw.playlist_items").as_string()
    )


def test_seeded_baseline_does_not_depend_on_row_order():
    keys = ["track-" + str(i) for i in range(30)]
    first = metrics.random_order(date(2026, 9, 26), keys)
    assert first == metrics.random_order(date(2026, 9, 26), list(reversed(keys)))
    assert first != metrics.random_order(date(2026, 9, 27), keys)
    assert set(first) == set(keys)


def test_all_baselines_rank_the_day_pool_and_keep_the_largest_cutoff():
    class Recorder:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            text = query if isinstance(query, str) else query.as_string()
            self.queries.append((text, params))

    recorder = Recorder()
    extract(
        recorder,
        {"name": "fixed", "schema": "fixture", "sha": "fixture"},
        {"id": "cycle", "opened_at": "2026-09-01", "close_no": 1},
    )
    with duckdb.connect() as conn:
        conn.execute("CREATE SCHEMA backtest")
        initialize(conn)
        keys = ["song-" + str(i) for i in range(60)]
        for key in keys:
            conn.execute(
                "INSERT INTO backtest.pool VALUES ('cycle','fixed','2026-09-01',?,'new_entries',1,0,0,0,0,0,0,0)",
                [key],
            )
        conn.execute(
            "UPDATE backtest.pool SET playlist_adds=3,follower_exposure_gain=1000,playlist_followers=100,shazam_charts=1,shazam_spread_gain=4 WHERE song_key='song-0'"
        )
        conn.execute(
            "UPDATE backtest.pool SET follower_exposure_gain=5000,playlist_followers=500,shazam_charts=3,stream_rate_gain=0.5 WHERE song_key='song-1'"
        )
        for query, params in recorder.queries:
            if query.strip().startswith("WITH ranked AS"):
                conn.execute(query.replace("%s", "?"), params)

        def songs(list_name):
            return [
                r[0]
                for r in conn.execute(
                    "SELECT song_key FROM backtest.predictions WHERE list=? ORDER BY rank",
                    [list_name],
                ).fetchall()
            ]

        assert songs("random") == metrics.random_order(date(2026, 9, 1), keys)[:50]
        assert songs("size_followers")[0] == "song-1"
        assert songs("size_charts")[0] == "song-1"
        assert songs("family_playlists") == ["song-0", "song-1"]
        assert songs("family_shazam") == ["song-0"]
        assert songs("family_streams") == ["song-1"]
        assert (
            conn.execute("SELECT max(rank) FROM backtest.predictions").fetchone()[0]
            == 50
        )


def test_precision_counts_one_song_per_week_and_excludes_pending():
    monday = date(2026, 9, 7)
    rows = [
        {
            "day": monday + timedelta(days=i),
            "rank": 1,
            "song": "a",
            "hit": True,
            "mature": True,
        }
        for i in range(4)
    ]
    rows += [
        {"day": monday, "rank": 2, "song": "b", "hit": False, "mature": True},
        {"day": monday, "rank": 3, "song": "c", "hit": False, "mature": False},
        {
            "day": monday + timedelta(days=7),
            "rank": 1,
            "song": "a",
            "hit": False,
            "mature": True,
        },
    ]
    result = metrics.precision(rows, 10)
    assert result["n"] == 3
    assert result["value"] == pytest.approx(1 / 3)
    assert result["weeks"] == 2 and result["interval"] is not None
    assert metrics.precision(rows, 1)["value"] == 0.5
    assert metrics.precision(rows, 10) == result


def test_leads_and_coverage_include_unflagged_events():
    day = date(2026, 9, 20)
    events = [
        {"song": song, "day": day, "kind": "chart_market", "dimension": "market"}
        for song in ("a", "b")
    ]
    flags = [{"song": "a", "day": day - timedelta(days=3)}, {"song": "a", "day": day}]
    result = metrics.event_metrics(events, flags, 7, 3)
    assert result["recall"]["value"] == 0.5
    assert result["lead_median"]["value"] == 3
    assert result["lead_p25"]["n"] == 1
    assert metrics.event_metrics(events, flags, 7, 7)["recall"]["value"] == 0
    cover = metrics.coverage(
        events, {"a": [(day - timedelta(days=1), 2)], "b": [(day, 3)]}, 7
    )
    assert cover["value"] == 0.5 and cover["n"] == 2
    assert cover["interval"] is None


def test_paired_bootstrap_uses_shared_weeks():
    rows = [
        {
            "song": "a",
            "day": date(2026, 9, 7) + timedelta(days=7 * i),
            "rank": 1,
            "hit": True,
            "mature": True,
        }
        for i in range(3)
    ]
    other = [{**r, "hit": False} for r in rows[:2]]
    result = metrics.paired_difference(rows, other, 10)
    assert result == {"value": 1, "n": 2, "weeks": 2, "interval": [1, 1]}


@pytest.fixture
def labels_db():
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA backtest")
    conn.execute(
        "CREATE TABLE backtest.aliases(cycle_id text,method text,alias_key text,song_key text)"
    )
    conn.execute(
        "CREATE TABLE backtest.artists(cycle_id text,method text,song_key text,artist_key text)"
    )
    conn.execute(
        "CREATE TABLE backtest.facts(cycle_id text,method text,song_key text,day date,kind text,dimension text,candidate boolean,value double)"
    )
    yield conn
    conn.close()


def apply_labels(conn):
    query = (Path(__file__).parent / "labels.sql").read_text()
    conn.execute(query.replace("%(cycle)s", "'now'").replace("%(method)s", "'fixed'"))
    return conn.execute(
        "SELECT song_key,day,kind,dimension FROM backtest.events ORDER BY 1,2,3"
    ).fetchall()


def test_labels_need_new_entries_and_preserve_old_features(labels_db):
    c = labels_db
    c.execute(
        "INSERT INTO backtest.aliases VALUES ('old','fixed','apple:1','apple:1'),('now','fixed','apple:1','recording')"
    )
    c.execute(
        "INSERT INTO backtest.artists VALUES ('now','fixed','recording','artist'),('now','fixed','second','artist')"
    )
    c.executemany(
        "INSERT INTO backtest.facts VALUES ('now','fixed',?,?,?,?,?,NULL)",
        [
            ("recording", date(2026, 9, 1), "shazam_country", "old", False),
            ("recording", date(2026, 9, 2), "shazam_country", "old", True),
            ("recording", date(2026, 9, 2), "shazam_country", "new", True),
            ("recording", date(2026, 9, 2), "shazam_city", "city", True),
            ("recording", date(2026, 9, 2), "chart_market", "market", True),
            ("recording", date(2026, 9, 2), "tier1_editorial", "list", True),
            ("second", date(2026, 9, 3), "chart_market", "market", True),
        ],
    )
    events = apply_labels(c)
    assert len(events) == 5
    assert not any(e[2] == "artist_first_chart" or e[3] == "old" for e in events)
    assert (
        c.execute(
            "SELECT canonical_key FROM backtest.outcome_keys WHERE cycle_id='old'"
        ).fetchone()[0]
        == "recording"
    )
    assert (
        c.execute(
            "SELECT song_key FROM backtest.aliases WHERE cycle_id='old'"
        ).fetchone()[0]
        == "apple:1"
    )


def test_artist_debut_and_ambiguous_aliases(labels_db):
    c = labels_db
    c.execute(
        "INSERT INTO backtest.aliases VALUES ('old','fixed','a','merged'),('old','fixed','b','merged'),('now','fixed','a','one'),('now','fixed','b','two')"
    )
    c.execute("INSERT INTO backtest.artists VALUES ('now','fixed','one','artist')")
    c.execute(
        "INSERT INTO backtest.facts VALUES ('now','fixed','one','2026-09-10','chart_market','market',true,NULL)"
    )
    events = apply_labels(c)
    assert any(e[2] == "artist_first_chart" for e in events)
    assert (
        c.execute(
            "SELECT canonical_key FROM backtest.outcome_keys WHERE cycle_id='old'"
        ).fetchone()[0]
        is None
    )


@pytest.mark.parametrize(
    "baseline,missing,raised,expected",
    [
        (100, None, 150, True),
        (100, 3, 150, False),
        (0, None, 150, False),
        (100, None, 149, False),
    ],
)
def test_stream_label_needs_seven_baseline_days_and_three_raised_days(
    labels_db, baseline, missing, raised, expected
):
    start = date(2026, 9, 1)
    for i in range(10):
        if i != missing:
            labels_db.execute(
                "INSERT INTO backtest.facts VALUES ('now','fixed','song',?,'stream_rate','',false,?)",
                [start + timedelta(days=i), baseline if i < 7 else raised],
            )
    events = apply_labels(labels_db)
    assert bool(events) == expected
    if expected:
        assert events == [("song", start + timedelta(days=9), "stream_surge", "")]


@pytest.mark.docker
def test_real_replay_later_identity_cannot_change_day_d(tmp_path):
    from functions.tests.identity_harness import Warehouse

    state = warehouse.start_local(tmp_path)
    wh = None
    try:
        with warehouse.local_connection(state) as conn:
            conn.execute("CREATE ROLE reader_wh")
        url = f"postgresql://postgres:backtest@127.0.0.1:{state['port']}/postgres"
        wh = Warehouse(url, tmp_path)
        old = wh.cycle("daily", 1, datetime(2026, 9, 1, 4, tzinfo=UTC))
        wh.generation("20260901-000000", 1, {})
        wh.observation(
            "apple_music",
            "list",
            datetime(2026, 9, 1, 1, tzinfo=UTC),
            [{"id": "track", "title": "fixture"}],
            "editorial",
            1,
        )
        # The harness routes SQL into a method schema; this fixture uses the same pinned project.
        method = warehouse.prepare_method(tmp_path, "fixed", "HEAD", state)
        assert "raw.mb_generation" in method["raw"]
        profiles = Path(method["path"]) / "profiles/profiles.yml"
        profiles.write_text(
            profiles.read_text().replace("dbname: postgres", "dbname: " + wh.name)
        )

        def build(cycle):
            bound = {"id": cycle["id"], "opened_by_dbt_run_id": cycle["run_id"]}
            with wh.connect() as conn:
                conn.execute("DROP SCHEMA IF EXISTS bt_fixed CASCADE")
            warehouse.dbt(method, "seed", state, bound, method["seeds"])
            warehouse.dbt(method, "run", state, bound, method["models"])
            return wh.query(
                "SELECT song_key,day,playlist_followers,list_count FROM bt_fixed.mart_song_day ORDER BY 1,2"
            )

        before = build(old)
        later = wh.cycle("daily", 2, datetime(2026, 9, 2, 4, tzinfo=UTC))
        wh.observation(
            "apple_music",
            "list",
            datetime(2026, 9, 2, 1, tzinfo=UTC),
            [{"id": "track", "title": "fixture", "isrc": "USAAA2600001"}],
            "editorial",
            2,
        )
        future = build(later)
        assert any(r[0] == "isrc:USAAA2600001" for r in future)
        assert before == build(old)
        assert before and all(r[0] == "apple:track" for r in before)
    finally:
        if wh:
            wh.drop()
        warehouse.cleanup(tmp_path, state)


@pytest.mark.docker
def test_score_excludes_selection_days_pending_horizons_and_pairs_methods(
    tmp_path, monkeypatch
):
    state = warehouse.start_local(tmp_path)
    start = date(2026, 9, 1)
    state["methods"] = {
        name: {"sha": "fixture", "chosen_on": "2026-09-01"}
        for name in ("fixed", "paired")
    }
    monkeypatch.setattr(warehouse, "committed_choices", lambda: state["methods"])
    state["cycles"] = [
        {"id": str(i), "opened_at": str(start + timedelta(days=i)), "close_no": i}
        for i in range(15)
    ]
    try:
        with warehouse.local_connection(state) as conn:
            initialize(conn)
            for i in range(15):
                day = start + timedelta(days=i)
                for method in state["methods"]:
                    conn.execute(
                        "INSERT INTO backtest.builds VALUES (%s,%s,%s,%s,'fixture',1,1,1)",
                        (str(i), method, day, i),
                    )
                    conn.execute(
                        "INSERT INTO backtest.pool(cycle_id,method,day,song_key,movement_list,families) VALUES (%s,%s,%s,'song','new_entries',2)",
                        (str(i), method, day),
                    )
                    conn.execute(
                        "INSERT INTO backtest.predictions(cycle_id,method,day,song_key,list,movement_list,rank,families) VALUES (%s,%s,%s,'song','movers','new_entries',1,2)",
                        (str(i), method, day),
                    )
                    conn.execute(
                        "INSERT INTO backtest.aliases VALUES (%s,%s,'apple:1','song')",
                        (str(i), method),
                    )
                conn.execute(
                    "INSERT INTO backtest.observations VALUES ('14','fixed','shazam_country','chart',%s,true)",
                    (day,),
                )
            conn.execute(
                "INSERT INTO backtest.targets VALUES ('14','fixed','shazam_country','chart',NULL)"
            )
            conn.execute(
                "INSERT INTO backtest.facts VALUES ('14','fixed','song','2026-09-10','shazam_country','new',true,NULL)"
            )
            evaluate.label(conn, state, tmp_path)
            evaluate.score(conn, state, None, tmp_path)
            evaluate.score(conn, state, "2026-09-01", tmp_path)
            report = json.loads((tmp_path / "report.json").read_text())
            assert "2026-09-01" not in report["evaluation_days"]
            selected = next(
                r
                for r in report["metrics"]
                if r["method"] == "fixed"
                and r["kind"] == "shazam_country"
                and r["horizon"] == 7
                and r["k"] == 10
            )
            assert selected["precision"]["n"] == 2
            assert selected["precision"]["value"] == 0.5
            assert max(r["day"] for r in selected["daily"]) == "2026-09-08"
            assert all(r["value"] in (0, None) for r in report["paired"])
            # Every already-chosen day is excluded, even with enough total history.
            evaluate.score(conn, state, "2026-09-15", tmp_path)
            report = json.loads((tmp_path / "report.json").read_text())
            assert report["evaluation_days"] == [] and report["metrics"] == []
    finally:
        warehouse.cleanup(tmp_path, state)


@pytest.mark.docker
def test_cleanup_removes_the_private_database_volume(tmp_path):
    state = warehouse.start_local(tmp_path)
    info = json.loads(
        warehouse.command(["docker", "inspect", state["container"]], text=True)
    )[0]
    volumes = [mount["Name"] for mount in info["Mounts"] if mount["Type"] == "volume"]
    assert volumes
    warehouse.cleanup(tmp_path, state)
    for volume in volumes:
        with pytest.raises(RuntimeError):
            warehouse.command(["docker", "volume", "inspect", volume])


def test_same_day_chart_baseline_cannot_make_an_artist_debut(labels_db):
    labels_db.execute(
        "INSERT INTO backtest.artists VALUES ('now','fixed','old-song','artist'),('now','fixed','new-song','artist')"
    )
    labels_db.execute("""
        INSERT INTO backtest.facts VALUES
        ('now','fixed','old-song','2026-09-10','chart_market','baseline',false,NULL),
        ('now','fixed','new-song','2026-09-10','shazam_country','new',true,NULL)
    """)
    events = apply_labels(labels_db)
    assert any(e[2] == "shazam_country" for e in events)
    assert not any(e[2] == "artist_first_chart" for e in events)


def test_negative_labels_require_every_target_for_the_song():
    day = date(2026, 9, 10)
    targets = [
        {"kind": "stream_surge", "target": "track-a", "song_key": "a"},
        {"kind": "stream_surge", "target": "track-b", "song_key": "b"},
        {"kind": "chart_market", "target": "read-list", "song_key": None},
        {"kind": "chart_market", "target": "unread-list", "song_key": None},
    ]
    observations = [
        {
            "kind": kind,
            "target": target,
            "day": day + timedelta(days=i),
            "complete": True,
        }
        for kind, target in [("stream_surge", "track-b"), ("chart_market", "read-list")]
        for i in range(-8, 8)
    ]
    coverage = evaluate.ObservationCoverage(targets, observations)
    assert coverage.mature("b", "stream_surge", day, 7)
    assert not coverage.mature("a", "stream_surge", day, 7)
    assert not coverage.mature("a", "chart_market", day, 7)
    observations += [
        {
            "kind": "chart_market",
            "target": "unread-list",
            "day": day + timedelta(days=i),
            "complete": i != 4,
        }
        for i in range(1, 8)
    ]
    assert not evaluate.ObservationCoverage(targets, observations).mature(
        "a", "chart_market", day, 7
    )
    observations[-4]["complete"] = True
    assert evaluate.ObservationCoverage(targets, observations).mature(
        "a", "chart_market", day, 7
    )
    # A missing baseline day cannot turn an unknown surge into a negative label.
    observations = [r for r in observations if r["day"] != day - timedelta(days=8)]
    assert not evaluate.ObservationCoverage(targets, observations).mature(
        "b", "stream_surge", day, 7
    )


def test_editorial_readd_uses_the_same_eligibility_for_precision_and_leads():
    day = date(2026, 9, 10)
    flag = {"song": "a", "day": day, "rank": 1, "mature": True}
    presence = {("a", day, "old-list")}
    old = {
        "song": "a",
        "kind": "tier1_editorial",
        "dimension": "old-list",
        "day": day + timedelta(days=3),
    }
    new = {**old, "dimension": "new-list"}

    def eligible(row, event):
        return evaluate.outcome_eligible(row, event, presence)

    assert metrics.precision([{**flag, "hit": eligible(flag, old)}], 10)["value"] == 0
    result = metrics.event_metrics([old], [flag], 7, 1, eligible=eligible)
    assert result["recall"]["value"] == 0
    assert result["lead_median"]["n"] == result["lead_p25"]["n"] == 0
    result = metrics.event_metrics([old, new], [flag], 7, 1, eligible=eligible)
    assert result["recall"]["value"] == 0.5
    assert result["lead_median"]["value"] == 3


def test_paired_n_counts_union_of_unique_song_weeks():
    row = {"song": "a", "day": date(2026, 9, 7), "rank": 1, "mature": True, "hit": True}
    left = [row, {**row, "day": row["day"] + timedelta(days=1)}]
    right = [row, {**row, "song": "b"}]
    assert metrics.paired_difference(left, right, 10)["n"] == 2


@pytest.fixture
def observation_db(tmp_path):
    state = warehouse.start_local(tmp_path)
    try:
        with warehouse.local_connection(state) as conn:
            initialize(conn)
            conn.execute("""
                CREATE SCHEMA raw;
                CREATE SCHEMA fixture;
                CREATE TABLE raw.targets(resource_kind text,platform text,canonical_key text,params_json json);
                CREATE TABLE raw.shazam_chart_entries(chart text,chart_date date,observed_at timestamp,
                    _dump_id text,position int,apple_song_id text);
                CREATE TABLE fixture.int_song_key__daily(song_key text,platform text,platform_track_id text);
                CREATE TABLE fixture.int_playlist__snapshots(platform text,playlist_id text,variant text,
                    stream text,snapshot_id text,observed_at timestamp,effective_coverage text,extent_valid boolean);
                CREATE TABLE fixture.int_playlist__observations(platform text,playlist_id text,variant text,
                    stream text,snapshot_id text,platform_track_id text);
                CREATE TABLE fixture.mart_track_daily_streams(platform text,platform_track_id text,day date,
                    count_status text,streams_since_last_update bigint,count_changed_at timestamp,
                    previous_count_changed_at timestamp);
                CREATE TABLE fixture.playlist_reach_tiers(platform text,playlist_id text,list_kind text,
                    market text,reach_tier int);
                CREATE TABLE fixture.int_song_entries__daily(song_key text,day date,platform text,list_id text,
                    snapshot_id text,event_type text,list_kind text,list_reach_tier int);
                CREATE TABLE fixture.int_song_followers__daily(song_key text,day date,platform text,playlist_id text);
                CREATE TABLE fixture.int_song_shazam__daily(song_key text,chart_date date,chart text,country text,city text);
            """)
            yield conn
    finally:
        warehouse.cleanup(tmp_path, state)


def extract_observation_fixture(conn):
    class Recorder:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            text = query if isinstance(query, str) else query.as_string()
            self.queries.append((query, text, params))

    recorder = Recorder()
    extract(
        recorder,
        {"name": "fixed", "schema": "fixture", "sha": "fixture"},
        {"id": "now", "opened_at": "2026-09-10", "close_no": 1},
    )
    for query, text, params in recorder.queries:
        if any(
            marker in text
            for marker in (
                "bt_playlist",
                "bt_shazam",
                "bt_stream",
                "INSERT INTO backtest.facts",
            )
        ):
            conn.execute(query, params)


@pytest.mark.docker
def test_chart_labels_need_complete_absence_on_the_exact_target(
    observation_db, tmp_path
):
    c = observation_db
    c.execute("INSERT INTO fixture.int_song_key__daily VALUES ('song','apple','track')")
    # One missing or rejected row in a prior chart must not prove absence.
    for country, count in [("complete", 200), ("rejected", 199)]:
        chart = "shazam:top-200:" + country
        c.execute(
            """
            INSERT INTO raw.shazam_chart_entries
            SELECT %s,'2026-09-01','2026-09-01 04:00','prior-' || %s,n,'other-' || n
            FROM generate_series(1,%s) n
        """,
            (chart, country, count),
        )
        c.execute(
            """
            INSERT INTO raw.shazam_chart_entries
            SELECT %s,'2026-09-02','2026-09-02 04:00','current-' || %s,n,
                CASE WHEN n=200 THEN 'track' ELSE 'other-' || n END
            FROM generate_series(1,200) n
        """,
            (chart, country),
        )
        c.execute(
            "INSERT INTO fixture.int_song_shazam__daily VALUES ('song','2026-09-02',%s,%s,NULL)",
            (chart, country),
        )
    c.execute("""
        INSERT INTO raw.targets VALUES ('chart','shazam','sz:chart:top-200:unread',
            '{"chart_type":"top-200","country":"unread"}');
    """)
    extract_observation_fixture(c)
    candidates = c.execute(
        "SELECT dimension,candidate FROM backtest.facts WHERE kind='shazam_country'"
    ).fetchall()
    by_place = {r["dimension"]: r["candidate"] for r in candidates}
    assert by_place[hashlib.md5(b"COMPLETE").hexdigest()]
    assert not by_place[hashlib.md5(b"REJECTED").hexdigest()]
    targets = c.execute("SELECT * FROM backtest.targets").fetchall()
    observations = c.execute("SELECT * FROM backtest.observations").fetchall()
    assert len(targets) == 3  # The unread frozen chart stays in the required set.
    assert not evaluate.ObservationCoverage(targets, observations).mature(
        "song", "shazam_country", date(2026, 9, 1), 1
    )
    c.execute("INSERT INTO backtest.artists VALUES ('now','fixed','song','artist')")
    evaluate.label(
        c,
        {
            "methods": {"fixed": {}},
            "cycles": [{"id": "now", "opened_at": "2026-09-10", "close_no": 1}],
        },
        tmp_path,
    )
    events = c.execute("SELECT * FROM backtest.events").fetchall()
    assert len([r for r in events if r["kind"] == "shazam_country"]) == 1
    # The rejected chart's first visible presence is a same-day baseline for this artist.
    assert not any(r["kind"] == "artist_first_chart" for r in events)


@pytest.mark.docker
def test_head_entries_and_other_variants_cannot_prove_chart_debuts(observation_db):
    c = observation_db
    c.execute("INSERT INTO fixture.int_song_key__daily VALUES ('song','apple','track')")
    for name, event, complete, variant in [
        ("full-add", "add", True, "us"),
        ("head-entry", "entered_head", True, "us"),
        ("partial", "add", False, "us"),
        ("other-variant", "add", True, "gb"),
    ]:
        c.execute(
            "INSERT INTO fixture.playlist_reach_tiers VALUES ('apple_music',%s,'chart','US',1)",
            (name,),
        )
        c.execute(
            "INSERT INTO fixture.int_playlist__snapshots VALUES ('apple_music',%s,%s,'full',%s,'2026-09-01',%s,false)",
            (name, variant, name + "-prior", "full" if complete else "partial"),
        )
        c.execute(
            "INSERT INTO fixture.int_playlist__snapshots VALUES ('apple_music',%s,'us','full',%s,'2026-09-02','full',false)",
            (name, name + "-current"),
        )
        c.execute(
            "INSERT INTO fixture.int_playlist__observations VALUES ('apple_music',%s,'us','full',%s,'track')",
            (name, name + "-current"),
        )
        c.execute(
            "INSERT INTO fixture.int_song_followers__daily VALUES ('song','2026-09-02','apple',%s)",
            (name,),
        )
        c.execute(
            "INSERT INTO fixture.int_song_entries__daily VALUES ('song','2026-09-02','apple',%s,%s,%s,'chart',1)",
            (name, name + "-current", event),
        )
    extract_observation_fixture(c)
    candidates = c.execute(
        "SELECT candidate FROM backtest.facts WHERE kind='chart_market'"
    ).fetchall()
    assert len(candidates) == 4
    assert sum(r["candidate"] for r in candidates) == 1


@pytest.mark.docker
def test_score_keeps_unread_song_pending_and_excludes_editorial_readd_credit(
    observation_db, tmp_path, monkeypatch
):
    c = observation_db
    start = date(2026, 9, 1)
    state = {
        "methods": {"fixed": {"sha": "fixture", "chosen_on": "2026-09-01"}},
        "cycles": [
            {"id": str(i), "opened_at": str(start + timedelta(days=i)), "close_no": i}
            for i in range(15)
        ],
    }
    monkeypatch.setattr(warehouse, "committed_choices", lambda: state["methods"])
    for i in range(15):
        day = start + timedelta(days=i)
        c.execute(
            "INSERT INTO backtest.builds VALUES (%s,'fixed',%s,%s,'fixture',1,1,1)",
            (str(i), day, i),
        )
        c.execute(
            "INSERT INTO backtest.observations VALUES ('14','fixed','tier1_editorial','list',%s,true)",
            (day,),
        )
        c.execute(
            "INSERT INTO backtest.observations VALUES ('14','fixed','stream_surge','other-song-track',%s,true)",
            (day,),
        )
    c.execute("""
        INSERT INTO backtest.targets VALUES
            ('14','fixed','tier1_editorial','list',NULL),
            ('14','fixed','stream_surge','unread-track','song'),
            ('14','fixed','stream_surge','other-song-track','other-song');
        INSERT INTO backtest.predictions(cycle_id,method,day,song_key,list,movement_list,rank,families)
            VALUES ('1','fixed','2026-09-02','song','movers','new_entries',1,2);
        INSERT INTO backtest.facts VALUES
            ('14','fixed','song','2026-09-02','tier1_editorial','list',false,NULL),
            ('14','fixed','song','2026-09-09','tier1_editorial','list',true,NULL),
            ('14','fixed','song','2026-09-05','billboard_debut','hot-100',true,NULL);
        INSERT INTO backtest.billboard_members VALUES ('14','fixed','song','song');
    """)
    evaluate.label(c, state, tmp_path)
    evaluate.score(c, state, "2026-09-01", tmp_path)
    report = json.loads((tmp_path / "report.json").read_text())
    results = {
        r["kind"]: r for r in report["metrics"] if r["horizon"] == 7 and r["k"] == 10
    }
    assert results["billboard_debut"]["precision"]["value"] == 1
    assert results["stream_surge"]["precision"]["n"] == 0
    result = results["tier1_editorial"]
    assert result["precision"]["n"] == 1 and result["precision"]["value"] == 0
    for lead in result["leads"].values():
        assert lead["recall"]["value"] == 0
        assert lead["lead_median"]["n"] == lead["lead_p25"]["n"] == 0


def test_billboard_labels_keep_one_first_group_week(labels_db):
    labels_db.execute("""
        INSERT INTO backtest.facts VALUES
        ('now','fixed','group-a','2026-09-12','billboard_debut','hot-100',false,NULL),
        ('now','fixed','group-a','2026-09-19','billboard_debut','hot-100',true,NULL),
        ('now','fixed','group-b','2026-09-19','billboard_debut','hot-100',true,NULL),
        ('now','fixed','group-b','2026-09-19','billboard_debut','hot-100',true,NULL),
        ('now','fixed','group-c','2026-09-19','billboard_debut','hot-100',false,NULL)
    """)
    assert apply_labels(labels_db) == [
        ("group-b", date(2026, 9, 19), "billboard_debut", "hot-100")
    ]


def test_billboard_negative_needs_each_weekly_issue():
    targets = [{"kind": "billboard_debut", "target": "hot-100", "song_key": None}]
    observed = [
        {
            "kind": "billboard_debut",
            "target": "hot-100",
            "day": date(2026, 9, 19),
            "complete": True,
        }
    ]
    coverage = evaluate.ObservationCoverage(targets, observed)
    assert coverage.mature("group", "billboard_debut", date(2026, 9, 14), 7)
    assert not coverage.mature("group", "billboard_debut", date(2026, 9, 14), 14)
    observed[0]["complete"] = False
    assert not evaluate.ObservationCoverage(targets, observed).mature(
        "group", "billboard_debut", date(2026, 9, 14), 7
    )


@pytest.mark.docker
def test_billboard_maturity_is_per_group_and_uses_all_member_titles(
    observation_db, tmp_path
):
    from ops.backtest.extract import extract_billboard

    conn = observation_db
    method = {"name": "fixed", "schema": "fixture"}
    cycle = {"id": "now", "opened_at": "2026-09-26", "close_no": 1}
    extract_billboard(conn, method, cycle)  # An older method has no bridge.
    conn.execute("""
        CREATE TABLE fixture.int_song_cluster__daily(song_key text,cluster_key text);
        INSERT INTO fixture.int_song_cluster__daily VALUES
            ('member','group'),('group','group'),('clear','clear'),('unknown','unknown');
        CREATE TABLE fixture.int_song_cluster_inputs__daily(song_key text,folded_title text);
        INSERT INTO fixture.int_song_cluster_inputs__daily VALUES
            ('member','alternate title'),('group','main title'),('clear','clear title'),('unknown','unknown title');
        CREATE TABLE fixture.int_billboard_song__daily(song_key text,chart_week date,
            is_debut boolean,week_complete boolean,track_title text);
        INSERT INTO fixture.int_billboard_song__daily VALUES
            ('clear','2026-09-19',false,true,'Clear title'),
            (NULL,'2026-09-19',NULL,true,'  ALTERNATE  TITLE '),
            (NULL,'2026-09-19',NULL,true,'Outside the universe'),
            ('unknown','2026-09-19',NULL,true,'Unknown title'),
            ('group','2026-09-26',true,true,'Main title'),
            (NULL,'2026-09-26',NULL,true,'Alternate title');
    """)
    extract_billboard(conn, method, cycle)
    evaluate.label(conn, {"methods": {"fixed": {}}, "cycles": [cycle]}, tmp_path)
    params = {"cycle": "now", "method": "fixed"}
    assert evaluate.billboard_summary(conn, params)["events"] == 1
    summary = evaluate.billboard_summary(conn, params)
    assert summary["week"]["keyed"] == 1 and summary["blocked_groups"] == 1
    coverage = evaluate.ObservationCoverage(
        conn.execute("SELECT * FROM backtest.targets"),
        conn.execute("SELECT * FROM backtest.observations"),
        conn.execute("SELECT * FROM backtest.billboard_blockers"),
    )
    # The first complete week can prove absence for an unrelated group.
    assert coverage.mature("clear", "billboard_debut", date(2026, 9, 14), 7)
    assert not coverage.mature("group", "billboard_debut", date(2026, 9, 14), 7)
    assert not coverage.mature("unknown", "billboard_debut", date(2026, 9, 14), 7)
    assert not coverage.mature("clear", "billboard_debut", date(2026, 9, 21), 14)


@pytest.mark.docker
def test_group_families_and_sizes_deduplicate_shared_places(observation_db):
    conn = observation_db
    conn.execute("""
        ALTER TABLE fixture.int_song_followers__daily ADD COLUMN followers bigint;
        INSERT INTO fixture.int_song_followers__daily VALUES
            ('representative','2026-09-10','spotify','shared',10),
            ('member','2026-09-10','spotify','shared',10),
            ('member','2026-09-10','apple','shared',10),
            ('solo-member','2026-09-10','spotify','solo',10);
        INSERT INTO fixture.int_song_shazam__daily VALUES
            ('representative','2026-09-10','shared',NULL,NULL),
            ('member','2026-09-10','shared',NULL,NULL),
            ('member','2026-09-10','second',NULL,NULL),
            ('member','2026-09-10','third',NULL,NULL),
            ('solo-member','2026-09-10','shared',NULL,NULL),
            ('solo-member','2026-09-10','second',NULL,NULL);
        CREATE TABLE fixture.mart_song_cluster_members(song_key text,cluster_key text);
        INSERT INTO fixture.mart_song_cluster_members VALUES ('representative','representative'),('member','representative'),('solo-member','absent-representative');
        CREATE TABLE fixture.mart_song_day(day date,song_key text,list_count int,shazam_charts int,
            streams_observed boolean,playlist_followers double precision);
        INSERT INTO fixture.mart_song_day VALUES
            ('2026-09-10','representative',1,1,false,10),
            ('2026-09-10','member',2,3,true,20),
            ('2026-09-10','solo-member',1,2,false,10);
        CREATE TABLE fixture.int_song_movement__daily(day date,song_key text,movement_list text,
            playlist_adds double precision,follower_exposure_gain double precision,
            shazam_spread_gain double precision,stream_rate_gain double precision,chart_spread_gain double precision);
        INSERT INTO fixture.int_song_movement__daily VALUES
            ('2026-09-10','representative','new_entries',1,2,3,4,5),
            ('2026-09-10','absent-representative','new_entries',1,2,3,4,5);
    """)

    class Projection:
        def execute(self, query, params=()):
            text = query if isinstance(query, str) else query.as_string()
            if (
                "INSERT INTO backtest.pool" in text
                or "INSERT INTO backtest.family_audit" in text
            ):
                conn.execute(query, params)

    extract(
        Projection(),
        {"name": "fixed", "schema": "fixture", "sha": "fixture"},
        {"id": "now", "opened_at": "2026-09-10", "close_no": 1},
    )
    row = conn.execute(
        "SELECT * FROM backtest.pool WHERE song_key='representative'"
    ).fetchone()
    assert (
        row["families"] == 3
        and row["playlist_followers"] == 20
        and row["shazam_charts"] == 3
    )
    audit = conn.execute(
        "SELECT * FROM backtest.family_audit WHERE song_key='representative'"
    ).fetchone()
    assert audit["before_families"] == 2 and audit["after_families"] == 3
    absent = conn.execute(
        "SELECT * FROM backtest.family_audit WHERE song_key='absent-representative'"
    ).fetchone()
    assert absent["before_families"] == 0 and absent["after_families"] == 2


def test_methods_reuse_pinned_choices_and_refuse_missing_dates(monkeypatch, tmp_path):
    monkeypatch.setattr(
        warehouse,
        "prepare_method",
        lambda scratch, name, ref, state: {"name": name, "sha": ref},
    )
    state = {"methods": {"fixed": {"sha": "pinned", "chosen_on": "2026-09-20"}}}
    choices = json.loads(json.dumps(state["methods"]))
    monkeypatch.setattr(warehouse, "committed_choices", lambda: choices)
    methods = warehouse.methods(tmp_path, None, state)
    assert methods["fixed"]["sha"] == "pinned"
    assert methods["fixed"]["chosen_on"] == "2026-09-20"
    state["capture_methods"] = {"fixed": {"sha": "pinned", "chosen_on": "2026-09-25"}}
    with pytest.raises(ValueError, match="committed"):
        warehouse.methods(tmp_path, None, state)
    del state["capture_methods"]
    with pytest.raises(ValueError, match="choices.json"):
        warehouse.methods(tmp_path, ["new=revision"], state)
    with pytest.raises(ValueError, match="committed"):
        warehouse.methods(tmp_path, ["fixed=changed"], state)
    with pytest.raises(ValueError, match="committed"):
        warehouse.methods(tmp_path, None, state, ["fixed=2026-09-19"])


@pytest.mark.parametrize("fails", [False, True])
def test_weekly_publishes_only_reports_and_cleans_up(monkeypatch, tmp_path, fails):
    from contextlib import nullcontext

    from ops.backtest import run

    scratch = tmp_path / "private"
    output = tmp_path / "published"
    state = {
        "container": "fixture",
        "methods": {"fixed": {"sha": "pinned", "chosen_on": "2026-09-20"}},
        "cycles": [],
    }
    calls = []

    def capture(folder, specs, output, chosen_on):
        warehouse.save(folder / "state.json", state)
        (output / "predictions.csv").write_text("private predictions")
        calls.append("capture")

    def replay(*args):
        calls.append("replay")
        if fails:
            raise RuntimeError("fixture failure")

    def label(conn, state, output):
        warehouse.save(output / "labels.json", {})
        calls.append("label")

    def score(conn, state, chosen_through, output):
        warehouse.save(output / "report.json", {})
        calls.append("score")

    def report(state, output):
        (output / "report.md").write_text("Open report.json.")
        calls.append("report")

    def cleanup(folder, state, keep_choices):
        assert keep_choices
        calls.append("cleanup")

    monkeypatch.setenv("MDP_BACKTEST_SCRATCH", str(scratch))
    monkeypatch.setattr(sys, "argv", ["run.py", "weekly", "--output", str(output)])
    for name, function in [
        ("capture", capture),
        ("replay", replay),
        ("cleanup", cleanup),
    ]:
        monkeypatch.setattr(warehouse, name, function)
    monkeypatch.setattr(warehouse, "local_connection", lambda state: nullcontext(None))
    for name, function in [("label", label), ("score", score), ("report", report)]:
        monkeypatch.setattr(evaluate, name, function)
    assert run.main() == int(fails)
    assert calls == (
        ["capture", "replay", "cleanup"]
        if fails
        else ["capture", "replay", "label", "score", "report", "cleanup"]
    )
    assert not (scratch / "results").exists()
    assert {p.name for p in output.iterdir()} == (
        set() if fails else {"report.md", "report.json", "labels.json"}
    )
