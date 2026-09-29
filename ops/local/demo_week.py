"""Seven fixed days of synthetic observations for the local analyst queries. No HTTP."""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import psycopg
from mdp_functions.listenbrainz import LbFreshRelease
from mdp_functions.playlist import PlaylistItem, PlaylistSnapshot
from mdp_functions.shazam import ShazamChartEntry
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

START = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
TRACKS = (
    "Copper Places",
    "Paper Satellite",
    "Glass Orbit",
    "New Horizon",
    "Velvet Dawn",
)
ISRC = "ZZDEM2600001"
MEMBERS = (
    (0, 1, 2),
    (1, 0, 2),
    (1, 2, 3),
    (3, 1, 2),
    (3, 2, 4),
    (2, 3, 0, 4),
    (3, 0, 2, 4),
)


def identity(key):
    return str(uuid5(NAMESPACE_URL, "mdp-local-demo-week:" + key))


def observations():
    """Yield declared raw records with stable ids, times and positions."""
    for day, members in enumerate(MEMBERS):
        observed = START + timedelta(days=day)
        common = {
            "platform": "apple_music",
            "playlist_id": "demo-editorial",
            "variant": "us",
            "stream": "full",
            "snapshot_id": identity(f"snapshot:{day}"),
            "observation_group": identity(f"group:{day}"),
            "observed_at": observed,
            "fetch_surface": "am_playlist",
        }
        snapshot = PlaylistSnapshot(
            **common,
            title="Demo Week",
            owner_class="editorial",
            owner_class_observed="editorial",
            followers=1000 + day * 75,
            track_count_reported=len(members),
            items_observed=len(members),
            coverage="full",
            cadence="daily",
            snapshot_hash=hashlib.sha256(json.dumps(members).encode()).hexdigest(),
            membership_hash=hashlib.sha256(json.dumps(members).encode()).hexdigest(),
        )
        yield "playlist_snapshots", "am_playlist", day, snapshot.model_dump(mode="json")
        for position, track in enumerate(members, 1):
            item = PlaylistItem(
                **common,
                position=position,
                platform_track_id=str(900000001 + track),
                platform_item_id=str(900000001 + track),
                occurrence=1,
                occurrence_key=f"track:{900000001 + track}:1",
                occurrence_inferred=True,
                title=TRACKS[track],
                artist_names=["Demo Ensemble"],
                duration_ms=180000,
                isrc=ISRC if track == 0 else "ZZDEM2600005" if track == 4 else None,
                platform_artist_ids=["demo-artist"],
            )
            yield "playlist_items", "am_playlist", day, item.model_dump(mode="json")
            chart = ShazamChartEntry(
                chart="shazam:top:DEMO",
                chart_date=observed.date(),
                position=position,
                apple_song_id=str(900000001 + track),
                title_text=TRACKS[track],
                artist_text="Demo Ensemble",
                observed_at=observed,
            )
            if track != 4:
                yield (
                    "shazam_chart_entries",
                    "sz_chart",
                    day,
                    chart.model_dump(mode="json"),
                )
        # A release arrives on Thursday and stays in the later observations.
        if day >= 3:
            release = LbFreshRelease(
                release_mbid=identity("release"),
                artist_mbids=json.dumps([identity("artist")]),
                artist_credit_name="Demo Ensemble",
                release_name="New Horizon",
                release_date=(START + timedelta(days=3)).date(),
                listen_count=10 + day * 5,
                observed_at=observed,
            )
            yield (
                "lb_fresh_releases",
                "lb_fresh_releases",
                day,
                release.model_dump(mode="json"),
            )
        # The second city observes the song only after two complete daily charts.
        for city, city_members in (
            ("Demo Harbor", tuple(t for t in members if t != 4)),
            ("Demo Ridge", (2,) if day < 2 else (2, 0)),
        ):
            for position, track in enumerate(city_members, 1):
                chart = ShazamChartEntry(
                    chart=f"shazam:city:US:{city}",
                    chart_date=observed.date(),
                    position=position,
                    apple_song_id=str(900000001 + track),
                    title_text=TRACKS[track],
                    artist_text="Demo Ensemble",
                    observed_at=observed,
                )
                yield (
                    "shazam_chart_entries",
                    "sz_chart",
                    day,
                    chart.model_dump(mode="json"),
                )
        # A Spotify copy shares Copper Places's observed ISRC with the Apple copy.
        common.update(
            platform="spotify",
            playlist_id="demo-spotify",
            fetch_surface="sp_playlist",
            snapshot_id=identity(f"spotify:{day}"),
            observation_group=identity(f"spotify-group:{day}"),
        )
        present = day >= 2
        snapshot = PlaylistSnapshot(
            **common,
            title="Demo Discoveries",
            owner_class="editorial",
            owner_class_observed="editorial",
            followers=2000,
            track_count_reported=int(present),
            items_observed=int(present),
            coverage="full",
            cadence="daily",
            snapshot_hash=str(present),
            membership_hash=str(present),
        )
        yield "playlist_snapshots", "sp_playlist", day, snapshot.model_dump(mode="json")
        if present:
            item = PlaylistItem(
                **common,
                position=1,
                platform_track_id="demo-copper",
                platform_item_id="demo-copper",
                occurrence=1,
                occurrence_key="track:demo-copper:1",
                occurrence_inferred=True,
                title=TRACKS[0],
                artist_names=["Demo Ensemble"],
                platform_artist_ids=["demo-artist"],
                isrc=ISRC,
                duration_ms=180000,
            )
            yield "playlist_items", "sp_playlist", day, item.model_dump(mode="json")
    # One complete, synthetic chart. Some entries match; the rest have no identity evidence.
    for position in range(1, 101):
        yield (
            "chart_entries",
            "billboard_hot100",
            6,
            {
                "chart": "hot-100",
                "week": START.date().isoformat(),
                "position": position,
                "title": TRACKS[position - 1]
                if position <= len(TRACKS)
                else f"Demo Unmatched {position}",
                "artist": "Demo Ensemble",
                "weeks_on_chart": 1,
            },
        )
    # A tiny, receipted reference generation links only synthetic artist IDs.
    generation = "2026-09-14-demo"
    reference = [
        (
            "artist",
            {
                "mb_key": "1",
                "artist_id": 1,
                "artist_gid": identity("artist"),
                "name": "Demo Ensemble",
            },
        )
    ]
    for number, platform in enumerate(("apple_music", "spotify"), 1):
        reference.append(
            (
                "url_link",
                {
                    "mb_key": str(number),
                    "entity_type": "artist",
                    "entity_id": 1,
                    "link_id": number,
                    "url_id": number,
                    "url": f"https://example.invalid/{platform}/demo-artist",
                    "url_platform": platform,
                    "url_kind": "artist",
                    "url_platform_id": "demo-artist",
                },
            )
        )
    counts = Counter("raw.mb_" + table for table, _ in reference)
    for table, row in reference:
        yield (
            "mb_" + table,
            "mb_spine",
            0,
            {**row, "mb_generation": generation, "mb_sequence": 1},
        )
    yield (
        "mb_generation",
        "mb_spine",
        0,
        {
            "mb_generation": generation,
            "mb_sequence": 1,
            "mirror_counts": json.dumps(counts),
        },
    )
    outputs = {
        table: {identity(f"dump:{table.removeprefix('raw.')}:0"): count}
        for table, count in {**counts, "raw.mb_generation": 1}.items()
    }
    yield (
        "_run_completion",
        "mb_spine",
        0,
        {
            "run_id": identity("run"),
            "source_key": "mb_spine",
            "outputs": json.dumps(outputs),
            "recorded": json.dumps({"generation": generation, "mirror_counts": counts}),
        },
    )
    yield (
        "mb_artist_catalog",
        "mb_artist_catalog",
        0,
        {
            "mb_artist_gid": identity("artist"),
            "artist_gid": identity("artist"),
            "status": "found",
            "catalog_week": "2026-09-14",
            "mb_generation": generation,
            "scope": "global",
            "step": "external:mb_artist_catalog",
            "input_ref": identity("artist"),
            "input_version": "demo",
            "config_version": "demo",
            "run_admitted_at": START.isoformat(),
            "learning_eligible": True,
        },
    )


def main():
    dsn = os.environ["MDP_WAREHOUSE_URL"]
    if conninfo_to_dict(dsn).get("host") != "127.0.0.1":
        raise SystemExit(
            "Demo week requires the loopback database from ops/local/env.sh"
        )
    run_id = identity("run")
    rows = list(observations())
    with psycopg.connect(dsn) as conn:
        if (
            conn.execute("select current_setting('mdp.local_stack', true)").fetchone()[
                0
            ]
            != "on"
        ):
            raise SystemExit("Demo week requires a disposable local stack")
        # Replace only this generator's rows; retrying startup never duplicates the week.
        for table in sorted({row[0] for row in rows}):
            conn.execute(
                sql.SQL("delete from raw.{} where _run_id = %s").format(
                    sql.Identifier(table)
                ),
                (run_id,),
            )
        for table, source, day, record in rows:
            record.update(
                _run_id=run_id,
                _dump_id=identity(f"dump:{table}:{day}"),
                _cycle_id=identity("cycle"),
                _revision_id=identity("revision"),
                _target_id=identity(source),
                _request_id="demo-week",
                _source_key=source,
                _landed_seq=day + 1,
                _ingested_at=(START + timedelta(days=day)).isoformat(),
                _extra={},
            )
            conn.execute(
                sql.SQL("insert into raw.{} ({}) values ({})").format(
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, record)),
                    sql.SQL(",").join(sql.Placeholder() for _ in record),
                ),
                [
                    json.dumps(value) if isinstance(value, (dict, list)) else value
                    for value in record.values()
                ],
            )
    print(f"Demo week: {len(rows)} synthetic rows, 2026-09-14 through 2026-09-20")


if __name__ == "__main__":
    main()
