#!/usr/bin/env python3
"""Count distinct source song ids against daily identity; never print song values."""
import argparse
import os
from datetime import UTC, datetime

import psycopg

QUERY = """
WITH source_tracks AS (
 SELECT DISTINCT _source_key AS source, platform, platform_track_id
 FROM staging.stg_playlist__items
 WHERE platform IN ('apple_music','spotify') AND platform_track_id IS NOT NULL
   AND coalesce(item_type,'track')='track'
 UNION
 SELECT DISTINCT 'sz_chart', 'apple_music', apple_song_id
 FROM staging.stg_shazam__chart_entries WHERE apple_song_id IS NOT NULL
 UNION
 SELECT DISTINCT 'spotify_streams', platform, platform_track_id
 FROM marts.mart_track_daily_streams WHERE platform_track_id IS NOT NULL
), counts AS (
 SELECT s.source, count(*) AS songs,
   count(*) FILTER (WHERE i.mb_recording_gid IS NOT NULL) AS resolved,
   count(*) FILTER (WHERE i.mb_recording_gid IS NULL) AS unresolved
 FROM source_tracks s LEFT JOIN intermediate.int_track_identity__daily i
   USING (platform,platform_track_id)
 GROUP BY s.source
), sources(source) AS (
 VALUES ('am_playlist'),('am_playlist_weekly'),('sp_playlist'),('sp_playlist_weekly'),
        ('sz_chart'),('spotify_streams')
)
SELECT s.source,coalesce(c.songs,0),coalesce(c.resolved,0),coalesce(c.unresolved,0)
FROM (SELECT source FROM sources UNION SELECT source FROM counts) s
LEFT JOIN counts c USING(source) ORDER BY s.source
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url-env", default="MDP_WAREHOUSE_URL", help="Environment variable holding the warehouse URL")
    parser.add_argument("--data-kind", choices=["local", "fixture"], required=True)
    args = parser.parse_args()
    with psycopg.connect(os.environ[args.url_env]) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5s'")
        rows = conn.execute(QUERY).fetchall()
        stamps = conn.execute("SELECT relation,cycle_id,close_no::text,built_at FROM marts._build WHERE relation IN ('marts.mart_track_daily_streams','marts.mart_shazam_chart_daily') ORDER BY relation").fetchall()
    print(f"# Identity coverage · {datetime.now(UTC).date()}\n")
    print(f"Data: {args.data_kind}. This is not a production measurement.\n")
    print("Counts are distinct platform track ids per source. Daily identity is left joined, so a Shazam-only song remains unresolved.")
    print("Sources overlap. Do not sum them into unique songs. Spotify streams is a served source family. Zero means no source ids in this dataset.\n")
    print("| Source | Songs | Resolved | Unresolved |\n|---|---:|---:|---:|")
    for row in rows:
        print("| " + " | ".join(map(str, row)) + " |")
    print("\nInput mart stamps (a missing row has unknown provenance):\n")
    for relation, cycle, close, built in stamps:
        print(f"- `{relation}`: cycle `{cycle}`, close `{close}`, built `{built.isoformat()}`.")
    print("\nReproduce: `uv run --project functions python ops/showcase/identity-coverage.py --url-env MDP_WAREHOUSE_URL --data-kind " + args.data_kind + "`.")
    print("Use a warehouse URL in that environment variable. Review unresolved coverage before selecting a story.")


if __name__ == "__main__":
    main()
