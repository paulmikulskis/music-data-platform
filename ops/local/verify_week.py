"""Check the local build inventory and the synthetic questions as an analyst."""

import json
import os
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]


def verify(conn):
    manifest = json.loads((ROOT / "dbt/target/manifest.json").read_text())
    required = (ROOT / "ops/local/models.txt").read_text().splitlines()
    for model in required:
        node = manifest["nodes"]["model.music_data_platform." + model]
        schema = node["schema"] if model.startswith("mart_") else "explore_" + node["schema"]
        relation = sql.Identifier(schema, node["alias"])
        conn.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(relation))
    inventory = conn.execute("""SELECT table_name FROM information_schema.tables
        WHERE table_schema='marts' AND table_name LIKE 'mart_%' ORDER BY table_name""").fetchall()
    print(
        "Built analyst marts (database checked): " + ", ".join(r[0] for r in inventory)
    )
    total, keyed = conn.execute(
        "SELECT count(*), count(song_key) FROM marts.mart_chart_history WHERE chart_name='hot-100'"
    ).fetchone()
    assert total == 100 and 0 < keyed < total, (total, keyed)
    copies = conn.execute("""SELECT count(DISTINCT k.platform) FROM intermediate.int_song_key__daily k
        JOIN marts.mart_song_cluster_members m USING (song_key)
        WHERE k.title_text='Copper Places' GROUP BY m.cluster_key""").fetchall()
    assert copies == [(2,)], copies
    cities = conn.execute("""SELECT count(DISTINCT city) FROM marts.mart_shazam_chart_daily
        WHERE title_text='Copper Places' AND city IS NOT NULL""").fetchone()[0]
    assert cities == 2, cities
    for table in (
        "mart_song_day",
        "mart_arrivals_current",
        "mart_early_signals_current",
        "mart_readiness",
        "mart_top_movers_current",
    ):
        count = conn.execute(
            sql.SQL("SELECT count(*) FROM marts.{}").format(sql.Identifier(table))
        ).fetchone()[0]
        assert count > 0, table
    print(
        "Synthetic week verified. Run the starter queries in docs/analyst-access.md#ten-starter-queries."
    )


if __name__ == "__main__":
    with psycopg.connect(os.environ["MDP_ANALYST_URL"]) as connection:
        verify(connection)
