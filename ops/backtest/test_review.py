"""Regressions for uncertain negatives and independently recorded choices."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.backtest import evaluate, warehouse
from ops.backtest.extract import extract_billboard

pytest_plugins = ["ops.backtest.test_backtest"]


@pytest.mark.docker
@pytest.mark.parametrize(
    "member_title,entry_title",
    [
        ("Example Song (feat. Guest)", "Example Song"),
        ("Example Song - feat. Guest", "Example Song"),
        ("Example Song", "Example Song [Radio Edit]"),
        ("Example Song (Live) [Remastered]", "Example Song"),
        ("Example Song feat. Guest", "Example Song ft. Guest"),
        ("Example Song with Guest", "Example Song x Guest"),
        ("Example Song featuring Guest", "Example Song (Guest Version)"),
    ],
    ids=[
        "featured_suffix",
        "dashed_credit",
        "bracketed_version",
        "stacked_versions",
        "short_credits",
        "with_x_credits",
        "long_credit",
    ],
)
def test_negative_title_variants_stay_pending(
    observation_db, member_title, entry_title
):
    conn = observation_db
    conn.execute("""
        CREATE TABLE fixture.int_song_cluster__daily(song_key text,cluster_key text);
        INSERT INTO fixture.int_song_cluster__daily VALUES ('member','group');
        CREATE TABLE fixture.int_song_cluster_inputs__daily(song_key text,folded_title text);
        CREATE TABLE fixture.int_billboard_song__daily(song_key text,chart_week date,
            is_debut boolean,week_complete boolean,track_title text);
    """)
    conn.execute(
        "INSERT INTO fixture.int_song_cluster_inputs__daily VALUES ('member',%s)",
        (member_title,),
    )
    conn.execute(
        "INSERT INTO fixture.int_billboard_song__daily VALUES (NULL,'2026-09-19',NULL,true,%s)",
        (entry_title,),
    )
    extract_billboard(conn, {"name": "fixed", "schema": "fixture"}, {"id": "now"})
    coverage = evaluate.ObservationCoverage(
        conn.execute("SELECT * FROM backtest.targets"),
        conn.execute("SELECT * FROM backtest.observations"),
        conn.execute("SELECT * FROM backtest.billboard_blockers"),
    )
    assert not coverage.mature("group", "billboard_debut", date(2026, 9, 14), 7)
    # A possible title match is never promoted to a positive.
    assert conn.execute("SELECT count(*) AS n FROM backtest.facts").fetchone()["n"] == 0


@pytest.mark.docker
@pytest.mark.parametrize(
    "membership", ["split", "missing_frozen", "missing_latest", "clear", "positive"]
)
def test_billboard_negative_uses_prediction_members(
    observation_db, tmp_path, monkeypatch, membership
):
    conn = observation_db
    methods = {"fixed": {"sha": "fixture", "chosen_on": "2026-09-01"}}
    original = warehouse.command
    monkeypatch.setattr(
        warehouse,
        "command",
        lambda args, **kw: (
            json.dumps(methods) if args[:2] == ["git", "show"] else original(args, **kw)
        ),
    )
    state = {
        "methods": methods,
        "cycles": [
            {"id": "old", "opened_at": "2026-09-14", "close_no": 1},
            {"id": "now", "opened_at": "2026-10-17", "close_no": 2},
        ],
    }
    conn.execute("""
        INSERT INTO backtest.builds VALUES
            ('old','fixed','2026-09-14',1,'fixture',1,1,1),
            ('now','fixed','2026-10-17',2,'fixture',1,1,1);
        INSERT INTO backtest.predictions(cycle_id,method,day,song_key,list,movement_list,rank,families)
            VALUES ('old','fixed','2026-09-14','a','movers','new_entries',1,2);
        INSERT INTO backtest.aliases VALUES ('old','fixed','alias-a','a'),('now','fixed','alias-a','a');
        INSERT INTO backtest.billboard_members VALUES
            ('old','fixed','a','a'),('old','fixed','b','a'),
            ('now','fixed','a','a'),('now','fixed','b','b');
        INSERT INTO backtest.billboard_blockers VALUES ('now','fixed','b','2026-09-19','unkeyed_title');
        INSERT INTO backtest.targets VALUES ('now','fixed','billboard_debut','hot-100',NULL);
        INSERT INTO backtest.observations
            SELECT 'now','fixed','billboard_debut','hot-100',d,true
            FROM generate_series('2026-09-19'::date,'2026-10-17'::date,interval '7 days') d;
    """)
    if membership == "missing_frozen":
        conn.execute("DELETE FROM backtest.billboard_members WHERE cycle_id='old'")
    elif membership == "missing_latest":
        conn.execute(
            "DELETE FROM backtest.billboard_members WHERE cycle_id='now' AND song_key='b'"
        )
    elif membership == "clear":
        conn.execute("DELETE FROM backtest.billboard_blockers")
    elif membership == "positive":
        conn.execute(
            "INSERT INTO backtest.facts VALUES ('now','fixed','a','2026-09-19','billboard_debut','hot-100',true,NULL)"
        )
    evaluate.label(conn, state, tmp_path)
    evaluate.score(conn, state, None, tmp_path)
    result = json.loads((tmp_path / "report.json").read_text())["billboard_maturity"]
    assert result["mature"] == (3 if membership in ("clear", "positive") else 0)
    assert result["pending"] == (0 if membership in ("clear", "positive") else 3)


def test_backdated_state_refuses_score_and_report(monkeypatch, tmp_path):
    committed = {"fixed": {"sha": "fixture", "chosen_on": "2026-09-26"}}
    monkeypatch.setattr(warehouse, "command", lambda *args, **kw: json.dumps(committed))
    state = {"methods": {"fixed": {"sha": "fixture", "chosen_on": "2026-09-20"}}}
    with pytest.raises(ValueError, match="committed"):
        evaluate.selection_cutoff(state, None)
    # Check before opening a possibly stale report.json.
    with pytest.raises(ValueError, match="committed"):
        evaluate.report(state, tmp_path)


def test_choice_reader_uses_committed_record(monkeypatch):
    recorded = {"fixed": {"sha": "fixture", "chosen_on": "2026-09-26"}}

    def command(args, **kwargs):
        assert args == ["git", "show", "HEAD:ops/backtest/choices.json"]
        return json.dumps(recorded)

    monkeypatch.setattr(warehouse, "command", command)
    assert warehouse.committed_choices() == recorded
    assert evaluate.selection_cutoff({"methods": recorded}, None) == date(2026, 9, 26)


def test_report_refuses_backdated_saved_result(monkeypatch, tmp_path):
    recorded = {"fixed": {"sha": "fixture", "chosen_on": "2026-09-26"}}
    monkeypatch.setattr(warehouse, "command", lambda *args, **kw: json.dumps(recorded))
    warehouse.save(
        tmp_path / "report.json", {"methods": recorded, "chosen_through": "2026-09-20"}
    )
    with pytest.raises(ValueError, match="committed"):
        evaluate.report({"methods": recorded}, tmp_path)


@pytest.mark.parametrize("opens_at_deadline", [False, True])
def test_quiet_wait_stops_at_overall_deadline(monkeypatch, opens_at_deadline):
    elapsed = [0]
    monkeypatch.setattr(warehouse.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        warehouse.time,
        "sleep",
        lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    monkeypatch.setattr(warehouse, "runner_starts", lambda conn: [])
    monkeypatch.setattr(
        warehouse,
        "quiet_seconds",
        lambda *args: 100 if opens_at_deadline and elapsed[0] >= 60 else 0,
    )
    with pytest.raises(ValueError, match="deadline"):
        warehouse.await_quiet(None, timeout=60)
    assert elapsed[0] == 60
