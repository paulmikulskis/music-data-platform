"""The production follower-day branch consumes the profile's already-naive UTC timestamps."""

import os
import subprocess
from datetime import date
from pathlib import Path

import duckdb
import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]


def follower_sql(engine):
    """Render the actual model branch and its macros, without the unrelated identity joins."""
    model = (ROOT / "dbt/models/intermediate/int_playlist__track_days.sql").read_text()
    branch = model.split("), followers as (", 1)[1].split("\nselect a.day,", 1)[0]
    macros = "\n".join(
        (ROOT / "dbt/macros" / name).read_text()
        for name in ("playlist_utc.sql", "mdp_identity.sql")
    )
    query = (
        "with clock as (select timestamp '2026-03-10 00:00:00' as as_of), followers as ("
        + branch
        + "\nselect platform, playlist_id, day, followers from followers order by 1, 2, 3"
    )
    # Jinja belongs to the dbt environment, not the functions runtime.
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "dbt",
            "python",
            "-c",
            (
                "import sys; from jinja2 import Environment, StrictUndefined; "
                "print(Environment(undefined=StrictUndefined).from_string(sys.stdin.read()).render("
                "target={'type': sys.argv[1]}, ref=lambda name: name))"
            ),
            engine,
        ],
        input=macros + query,
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout


@pytest.mark.parametrize(
    "engine", ["duckdb", pytest.param("postgres", marks=pytest.mark.docker)]
)
def test_follower_days_ignore_session_timezone(engine):
    if engine == "postgres":
        url = os.environ.get("MDP_CONTROL_ADMIN_URL")
        if not url:
            pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
        conn = psycopg.connect(url)
    else:
        conn = duckdb.connect()
    try:
        # mart_playlist_profile's contract is timestamp without time zone, normalized to UTC.
        # Midnight observations straddle the New York daylight-saving transition.
        conn.execute(
            "CREATE TEMP TABLE mart_playlist_profile "
            "(platform text, playlist_id text, snapshot_id text, observed_at timestamp, followers bigint)"
        )
        conn.execute("""INSERT INTO mart_playlist_profile VALUES
            ('spotify', 'one', 'a', timestamp '2026-03-07 00:00:00', 100),
            ('spotify', 'one', 'b', timestamp '2026-03-08 00:00:00', NULL),
            ('spotify', 'one', 'c', timestamp '2026-03-09 00:00:00', 200),
            ('spotify', 'two', 'd', timestamp '2026-03-08 00:00:00', 300)""")
        query = follower_sql(engine)
        expected = [
            ("spotify", "one", date(2026, 3, d), 100 if d < 9 else 200)
            for d in range(7, 11)
        ]
        expected += [("spotify", "two", date(2026, 3, d), 300) for d in range(8, 11)]
        conn.execute("SET TIME ZONE 'UTC'")
        utc = conn.execute(query).fetchall()
        assert utc == expected
        conn.execute("SET TIME ZONE 'America/New_York'")
        new_york = conn.execute(query).fetchall()
        assert new_york == utc
    finally:
        conn.close()
