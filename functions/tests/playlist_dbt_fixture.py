"""Load/check a bounded history in a disposable playlist-fixture DuckDB after dbt bootstrap.

MDP_DEV_DB=/tmp/playlist-fixture/playlists.duckdb uv run --project functions python functions/tests/playlist_dbt_fixture.py load
Run the brief's dbt build, then invoke this script with `check`.
"""

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import duckdb
from mdp_functions.bandcamp import BandcampTrack
from mdp_functions.playlist import PlaylistItem, PlaylistSnapshot


def identity(value):
    return str(uuid5(NAMESPACE_URL, "mdp-playlist-fixture:" + value))


CLOSED = datetime(2026, 9, 22, tzinfo=timezone.utc)
# Owner cases. `label` is the class the target's frozen label landed; `observed` is what the collector
# saw (None: a row landed before it was recorded, so staging reads the payload evidence); `served` is
# the class the marts serve; `own` says the platform's own account owns it, so its id stays and a name
# is served (`served_name`, else the landed one). `surface` names a non-default fetch surface. Each
# case lands a description, which a mart serves only when a row observed the platform's own account
# (`described`, else `own`).
ALGOTORIAL = [{"key": "isAlgotorial", "value": "true"}]
OWNER_FIELDS = ("playlist", "platform", "label", "observed", "served", "owner_id", "name", "own")
OWNERS = [
    {"attributes": [], "surface": None, "served_name": None, "embed_observed": None, "no_page": False,
     "described": row[7], "description": f"{row[0]} description",
     **dict(zip(OWNER_FIELDS, row[:8], strict=True)), **(row[8] if len(row) > 8 else {})}
    for row in [
        ("owner_apple", "apple_music", "editorial", None, "editorial", "1526756058", "Fixture item 34517", True),
        # An Apple curator page landed as `unknown` before the observed class: its "Apple Music" curator
        # name is the payload evidence, so it is served as the platform's editorial owner.
        ("owner_apple_unknown", "apple_music", "unknown", None, "editorial", "976439548", "Apple Music Pop", True),
        ("owner_spotify", "spotify", "editorial", "editorial", "editorial", "spotify", "Spotify", True),
        ("owner_user", "apple_music", "user", "user", "user", "private-person-id", "Private Person Name", False),
        ("owner_unknown", "apple_music", "unknown", "unknown", "unknown", "unknown-owner-id", "Unknown Owner Name", False),
        # A label never replaces an observed user, fills only an observed unknown, and may refine
        # editorial to chart.
        ("owner_curator_label", "apple_music", "curator", "user", "user", "curator-label-id", "Curator Label Name", False),
        ("owner_unknown_label", "apple_music", "curator", "unknown", "curator", "unknown-label-id", "Unknown Label Name", False),
        ("owner_chart_label", "spotify", "chart", "editorial", "chart", "spotify", "Spotify", True),
        ("owner_editorial_label", "spotify", "editorial", None, "user", "private-editor-id", "Private Editor Name", False),
        # Frozen editorial, but its embed carries Spotify's algotorial attribute: served algorithmic.
        ("owner_algotorial_label", "spotify", "editorial", None, "dsp_algorithmic", "spotify", "Spotify", True,
         {"attributes": ALGOTORIAL}),
        # Precedence: a label never promotes an observed user; an embed (which never observes its owner)
        # takes its paired page's class before its label, and its label only without a page.
        ("owner_user_label_editorial", "apple_music", "editorial", "user", "user", "user-label-id", "User Label Name", False),
        ("owner_embed_page_user", "spotify", "editorial", "user", "user", "embed-page-user-id", "Embed Page User", False,
         {"embed_observed": "unknown"}),
        # An embed alone never observes its owner, so its description is not served.
        ("owner_embed_no_page", "spotify", "chart", "unknown", "chart", None, None, True,
         {"no_page": True, "described": False}),
        # A Bandcamp Daily list landed with its writer's byline: the column is served as its owner. A
        # radio show's name is its programme and stays.
        ("owner_bc_daily", "bandcamp", "editorial", None, "editorial", None, "Byline Writer Name", True,
         {"surface": "bc_daily_list", "served_name": "Bandcamp Daily"}),
        ("owner_bc_radio", "bandcamp", "editorial", None, "editorial", None, "The Hip Hop Show", True,
         {"surface": "bc_radio"}),
    ]
]


def load(conn):
    fixtures = json.loads(
        (Path(__file__).parent / "fixtures/playlist_history.json").read_text()
    )
    original = list(fixtures)
    # Supply explicit paired page evidence for the historical event cases.
    for fixture in original:
        if (
            fixture.get("platform", "spotify") != "spotify"
            or fixture.get("source", "sp_playlist_embed") != "sp_playlist_embed"
        ):
            continue
        if any(
            f.get("source") == "sp_playlist_page"
            and f["playlist"] == fixture["playlist"]
            and f["day"] == fixture["day"]
            for f in original
        ):
            continue
        content = next(
            (
                f
                for f in original
                if f["playlist"] == fixture["playlist"]
                and f["day"] == fixture.get("unchanged_day", fixture["day"])
                and f.get("source", "sp_playlist_embed") == "sp_playlist_embed"
            ),
            fixture,
        )
        fixtures.append(
            {
                **fixture,
                "source": "sp_playlist_page",
                "rows": content["rows"][:30],
                "reported": fixture.get("reported", len(content["rows"])),
                "coverage": "full",
                "unchanged_day": None,
            }
        )
        fixtures[-1].pop("unchanged_day", None)
        fixtures[-1].pop("items_observed", None)
    head = [f"t{n}" for n in range(100)]

    def paired(playlist, day, rows, total, page_rows=None, **kw):
        fixtures.append(
            dict(playlist=playlist, day=day, rows=rows, reported=total, **kw)
        )
        fixtures.append(
            dict(
                playlist=playlist,
                day=day,
                rows=(page_rows if page_rows is not None else rows)[:30],
                reported=total,
                source="sp_playlist_page",
                **kw,
            )
        )

    for day, rows, total in [
        (10, head, 100),
        (11, ["inserted"] + head[:99], 101),
        (12, head, 100),
        (13, head[:99] + ["bounded"], 100),
    ]:
        paired("threshold", day, rows, total)
    # Page reports 100, then an insertion before the embed pushes t99 to 101.
    paired("fetch_race", 10, head, 100)
    paired("fetch_race", 11, ["inserted"] + head[:99], 100, page_rows=head)
    # An insertion at 60 lands before the embed and is undone before the page: the
    # first 30 rows still agree, but 81 embed rows against a total of 80 do not.
    short = head[:80]
    paired("insert_race", 10, short, 80)
    paired(
        "insert_race", 11, short[:59] + ["inserted"] + short[59:], 80, page_rows=short
    )
    paired("insert_race", 12, short, 80)
    # Synthetic sequence: 100 tracks, an insertion at 60 lands before the embed
    # and is undone before the page. Counts and first 30 rows agree; 100 is not below 100.
    paired("insert_race_100", 10, head, 100)
    paired(
        "insert_race_100",
        11,
        head[:59] + ["inserted"] + head[59:99],
        100,
        page_rows=head,
    )
    paired("insert_race_100", 12, head, 100)
    for playlist, kw in [
        ("missing_total", {"reported": None}),
        ("missing_page", {}),
        ("old_page", {"page_delay": 301}),
        ("wrong_group", {"different_group": True}),
        ("wrong_batch", {"different_run": True}),
    ]:
        for day, rows in [(10, ["a"]), (11, ["b"])]:
            if playlist == "missing_page":
                fixtures.append({"playlist": playlist, "day": day, "rows": rows})
            else:
                total = (
                    kw.pop("reported", 1)
                    if day == 10
                    else (None if playlist == "missing_total" else 1)
                )
                paired(playlist, day, rows, total, **kw)
    for playlist, full_day, head_total, cadence in [
        ("current_full", 18, 101, None),
        ("current_stale", 1, 101, None),
        ("current_shrink", 18, 1, None),
        # The frozen daily window plus one daily consuming cycle (48 h): a full
        # stream three days old is stale.
        ("current_daily_stale", 17, 101, "daily"),
        ("current_weekly_fresh", 18, 101, "weekly"),
        # One day before the latest observation: fresh against the local clock, stale
        # against the bound cycle's close three days after it.
        ("current_clock", 19, 101, "daily"),
    ]:
        fixtures.append(
            {
                "playlist": playlist,
                "day": full_day,
                "rows": ["full-member"],
                "source": "sp_playlist_browser",
                "stream": "full",
                "cadence": cadence,
            }
        )
        paired(playlist, 20, ["head-member"] if head_total == 1 else head, head_total)
    fixtures += [
        {
            "playlist": "apple_count",
            "platform": "apple_music",
            "variant": "us",
            "day": 19,
            "rows": ["a", "a"],
            "reported": 999,
        },
        {
            "playlist": "apple_count",
            "platform": "apple_music",
            "variant": "us",
            "day": 20,
            "rows": ["a"],
            "reported": 999,
        },
    ]
    for day, rows in [(10, ["a"]), (11, ["b"])]:
        fixtures.append(
            {
                "playlist": "stream_isolation",
                "day": day,
                "rows": rows,
                "source": "sp_playlist_browser",
                "stream": "full",
            }
        )
        paired("stream_isolation", day, ["a"], 1)
    for day, rows in [(19, ["a", "b"]), (20, ["a"])]:
        fixtures.append(
            {
                "playlist": "legacy",
                "day": day,
                "rows": rows,
                "platform": "apple_music",
                "variant": "us",
            }
        )
    # Rows landed by the single sp_playlist collector carry its source key on both surfaces.
    for day, rows in [(10, ["a", "b"]), (11, ["a", "c"])]:
        paired("collector", day, rows, 2, source_key="sp_playlist")
    # None marks a row whose identity was unrecoverable: rejected, ordinals not advanced.
    for day, rows, coverage in [
        (1, ["x", "y", "x"], "full"),
        (2, [None, "y", "x"], "partial"),
        (3, ["x", "y", "x"], "full"),
    ]:
        fixtures.append(
            {
                "playlist": "shifted",
                "platform": "apple_music",
                "variant": "us",
                "day": day,
                "rows": rows,
                "coverage": coverage,
            }
        )
    # Two observations of one stream at the same instant, one holding "a" and one
    # without it: a tie proves presence only, so the later full absence removes "a".
    for day, rows, tie in [
        (10, ["b", "a"], ""),
        (11, ["b", "a"], ""),
        (11, ["b"], ":tie"),
        (12, ["b"], ""),
    ]:
        fixtures.append(
            {
                "playlist": "tie",
                "platform": "apple_music",
                "variant": "us",
                "day": day,
                "rows": rows,
                "tie": tie,
            }
        )
    # A same-instant tie whose lists order the items differently: the tie cannot be ordered,
    # so it proves no move; the later full observation moves both against day 10.
    for day, rows, tie in [
        (10, ["a", "b"], ""),
        (11, ["a", "b"], ""),
        (11, ["b", "a"], ":tie"),
        (12, ["b", "a"], ""),
    ]:
        fixtures.append(
            {
                "playlist": "tie_move",
                "platform": "apple_music",
                "variant": "us",
                "day": day,
                "rows": rows,
                "tie": tie,
            }
        )
    # Owners: a list the platform's own account owns keeps its owner and description; any other
    # serves no owner id, name or description in any mart, whatever class the target's label gave it.
    for case in OWNERS:
        owner = {
            "owner_class": case["label"],
            "owner_class_observed": case["observed"],
            "owner_id": case["owner_id"],
            "owner_name": case["name"],
            "description": case["description"],
            "attributes": case["attributes"],
        }
        if case["embed_observed"]:
            owner["owner_class_observed_embed"] = case["embed_observed"]
        if case["platform"] == "spotify" and case["no_page"]:
            fixtures.append({"playlist": case["playlist"], "day": 10, "rows": ["a", "b"], **owner})
        elif case["platform"] == "spotify":
            paired(case["playlist"], 10, ["a", "b"], 2, **owner)
        else:
            # Bandcamp lists hold releases, never track placements.
            surface = (
                {"source": case["surface"], "variant": "", "stream": "full", "item_type": "album"}
                if case["surface"]
                else {"variant": "us"}
            )
            fixtures.append(
                {
                    "playlist": case["playlist"],
                    "platform": case["platform"],
                    "day": 10,
                    "rows": ["a", "b"],
                    **surface,
                    **owner,
                }
            )
    for day, rows in [(19, ["album-a"]), (20, ["album-b"])]:
        fixtures.append(
            {
                "playlist": "releases",
                "day": day,
                "rows": rows,
                "platform": "bandcamp",
                "variant": "",
                "stream": "full",
                "source": "bc_discover",
                "item_type": "album",
                "featured_track_id": "featured",
            }
        )
    # SoundCloud full streams through the shared model.
    for day, rows in [(10, ["a", "b", "c"]), (11, ["a", "c", "d"])]:
        fixtures.append(
            {
                "playlist": "sc_editorial",
                "platform": "soundcloud",
                "variant": "",
                "stream": "full",
                "source": "sc_playlist",
                "source_key": "sc_playlist",
                "day": day,
                "rows": rows,
                "isrc": True,
                "cadence": "daily",
            }
        )
    for table in ("playlist_items", "playlist_snapshots", "bc_tracks", "cycles"):
        conn.execute("delete from raw." + table)
    contents = {}
    for index, fixture in enumerate(fixtures):
        playlist = fixture["playlist"]
        day = fixture["day"]
        platform = fixture.get("platform", "spotify")
        variant = fixture.get("variant", "US")
        source = fixture.get(
            "source",
            "am_playlist" if platform == "apple_music" else "sp_playlist_embed",
        )
        sid = identity(f"{playlist}:{variant}:{day}:{source}" + fixture.get("tie", ""))
        common = {
            "platform": platform,
            "playlist_id": playlist,
            "variant": variant,
            "stream": fixture.get(
                "stream", "full" if platform == "apple_music" else "head"
            ),
            "observation_group": identity(
                f"group:{playlist}:{variant}:{day}"
                + (
                    ":other"
                    if fixture.get("different_group") and source == "sp_playlist_page"
                    else ""
                )
            ),
            "snapshot_id": sid,
            "observed_at": datetime(2026, 9, day, tzinfo=timezone.utc),
            "fetch_surface": source,
        }
        if source == "sp_playlist_page":
            common["observed_at"] += timedelta(seconds=fixture.get("page_delay", 0))
        rows = fixture["rows"]
        snapshot = PlaylistSnapshot(
            **common,
            coverage=fixture.get("coverage", "full"),
            items_observed=fixture.get(
                "items_observed", sum(r is not None for r in rows)
            ),
            title=playlist,
            owner_class=fixture.get("owner_class", "editorial"),
            # What the collector observed; the default fixtures observed their own class.
            owner_class_observed=fixture["owner_class_observed_embed"]
            if "owner_class_observed_embed" in fixture and source == "sp_playlist_embed"
            else fixture["owner_class_observed"]
            if "owner_class_observed" in fixture
            else fixture.get("owner_class", "editorial"),
            description=fixture.get("description"),
            owner_id=fixture.get("owner_id"),
            owner_name=fixture.get("owner_name"),
            attributes=fixture.get("attributes", []),
            track_count_reported=len(rows),
            cadence=fixture.get("cadence"),
            observation="unchanged" if "unchanged_day" in fixture else "content",
            content_ref=identity(
                f"{playlist}:{variant}:{fixture['unchanged_day']}:{fixture.get('ref_source', source)}"
            )
            if "unchanged_day" in fixture
            else None,
        )
        counts = Counter()
        records = []
        for position, track in enumerate(rows, 1):
            if track is None:
                continue
            counts[track] += 1
            uid = (
                fixture.get("uids", [f"{t}#{n}" for n, t in enumerate(rows)])[
                    position - 1
                ]
                if "uids" in fixture
                else f"track:{track}#{counts[track]}"
            )
            if (
                fixture.get("uidless")
                or platform != "spotify"
                or source == "sp_playlist_browser"
            ) and not fixture.get("row_ids"):
                uid = None
            elif fixture.get("row_ids"):
                uid = f"ti_{track}"
            records.append(
                (
                    "playlist_items",
                    PlaylistItem(
                        **common,
                        position=position,
                        item_type=fixture.get("item_type", "track"),
                        platform_item_id=track,
                        featured_track_id=fixture.get("featured_track_id"),
                        platform_track_id=track
                        if fixture.get("item_type", "track") == "track"
                        else None,
                        platform_row_id=uid,
                        occurrence=counts[track],
                        occurrence_key=uid
                        or f"{fixture.get('item_type', 'track')}:{track}#{counts[track]}",
                        occurrence_inferred=not bool(uid),
                        title=fixture.get("titles", rows)[position - 1],
                        duration_ms=fixture.get("durations", [200000] * len(rows))[
                            position - 1
                        ],
                        platform_album_id="album-" + track
                        if source == "sp_playlist_page"
                        else None,
                        isrc=f"QZ{track.upper():0>10}"[:12]
                        if fixture.get("isrc")
                        else None,
                    ),
                )
            )
        ordered = [
            (
                item.position,
                item.occurrence_key,
            )
            for _, item in records
        ]
        snapshot.snapshot_hash = hashlib.md5(
            json.dumps(ordered, separators=(",", ":")).encode()
        ).hexdigest()
        snapshot.membership_hash = snapshot.snapshot_hash
        snapshot.content_hash = hashlib.md5(
            json.dumps(
                [
                    [
                        item.position,
                        item.occurrence_key,
                        item.platform_track_id,
                        item.title,
                        item.duration_ms,
                    ]
                    for _, item in records
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if snapshot.content_ref in contents:
            referenced = contents[snapshot.content_ref]
            snapshot.snapshot_hash = referenced.snapshot_hash
            snapshot.membership_hash = referenced.membership_hash
            snapshot.content_hash = referenced.content_hash
            snapshot.track_count_reported = referenced.track_count_reported
            snapshot.continuation = referenced.continuation
        if "reported" in fixture:
            snapshot.track_count_reported = fixture["reported"]
        if "content_hash" in fixture:
            snapshot.content_hash = fixture["content_hash"]
        if snapshot.observation == "content":
            contents[sid] = snapshot
        records.append(("playlist_snapshots", snapshot))
        for table, record in records:
            row = record.model_dump(mode="json")
            row.update(
                _dump_id=identity(f"dump:{index}:{table}"),
                _run_id=identity(
                    "other-run"
                    if fixture.get("different_run") and source == "sp_playlist_page"
                    else "run"
                ),
                _cycle_id=identity("cycle"),
                _revision_id=identity("revision"),
                _target_id=identity(playlist),
                _request_id="fixture",
                _source_key=fixture.get("source_key", source),
                _ingested_at=common["observed_at"],
                _landed_seq=index + 1,
                _extra={},
            )
            keys = list(row)
            values = [
                json.dumps(v) if isinstance(v, (list, dict)) else v
                for v in row.values()
            ]
            conn.execute(
                f"insert into raw.{table} ({','.join(keys)}) values ({','.join('?' for _ in keys)})",
                values,
            )
    conn.execute(
        "update raw.playlist_snapshots set stream=null,observation_group=null where playlist_id='legacy' and observed_at<'2026-09-20'"
    )
    conn.execute(
        "update raw.playlist_items set stream=null,item_type=null,platform_item_id=null,occurrence_key=replace(occurrence_key,'track:','') where playlist_id='legacy' and observed_at<'2026-09-20'"
    )
    conn.execute(
        "update main_reference.rights_registry set learning_eligible=true,resale_permitted=true where source_key in ('am_playlist','sp_playlist_embed')"
    )
    # Release pages for the ranked Bandcamp album: its tracks, and a track page ISRC.
    for position, (page, track, isrc) in enumerate(
        [
            ("https://a.bandcamp.com/album/b", "t1", None),
            ("https://a.bandcamp.com/album/b", "featured", None),
            ("https://a.bandcamp.com/track/featured", "featured", "QZFEATURED01"),
        ],
        1,
    ):
        row = BandcampTrack(
            page_url=page,
            position=position,
            observed_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            track_id=track,
            album_id="album-b",
            page_item_type="track" if "/track/" in page else "album",
            page_item_id="featured" if "/track/" in page else "album-b",
            title=track,
            isrc=isrc,
        ).model_dump(mode="json")
        row.update(
            _dump_id=identity(f"bc:{position}"),
            _run_id=identity("run"),
            _cycle_id=identity("cycle"),
            _revision_id=identity("revision"),
            _target_id=identity("band"),
            _request_id="fixture",
            _source_key="bc_tralbum",
            _ingested_at=row["observed_at"],
            _landed_seq=10_000 + position,
            _extra="{}",
        )
        conn.execute(
            f"insert into raw.bc_tracks ({','.join(row)}) values ({','.join('?' for _ in row)})",
            list(row.values()),
        )
    # The observing cycle's close: coverage age and the bound-cycle clock read it.
    conn.execute(
        "insert into raw.cycles (id,cadence,scope,opened_at,closed_at,status) values (?,'daily','global',?,?,'closed')",
        [identity("cycle"), datetime(2026, 9, 1, tzinfo=timezone.utc), CLOSED],
    )
    print(
        f"Loaded {len(fixtures)} playlist observations; fixture rights: Apple/embed allowed, page denied"
    )


def mart_time(day):
    # The mart contract exposes UTC timestamps without timezone metadata.
    return datetime(2026, 9, day, tzinfo=timezone.utc).replace(tzinfo=None)


def pseudonym(value):
    # mdp_pseudonym with the dev key (dbt/macros/mdp_pseudonym.sql).
    return hashlib.sha256(f"mdp-local-pseudonym:{value}".encode()).hexdigest()


def check_owners(conn):
    profiles = {
        row[0]: row[1:]
        for row in conn.execute(
            "select distinct playlist_id,owner_class,owner_id,owner_name,description from main_marts.mart_playlist_profile where playlist_id like 'owner_%'"
        ).fetchall()
    }
    assert profiles == {
        c["playlist"]: (
            c["served"],
            c["owner_id"] if c["own"] else None,
            (c["served_name"] or c["name"]) if c["own"] else None,
            c["description"] if c["described"] else None,
        )
        for c in OWNERS
    }, profiles
    # The pseudonym stays in staging and intermediate for joins.
    keys = dict(
        conn.execute(
            "select distinct playlist_id,owner_key from main_intermediate.int_playlist__snapshots where playlist_id in ('owner_user','owner_spotify')"
        ).fetchall()
    )
    assert keys == {"owner_user": pseudonym("private-person-id"), "owner_spotify": "spotify"}, keys
    # No private owner's name, raw id or description, and no replaced byline, reaches any staging,
    # intermediate or mart relation, and no mart serves a private owner's pseudonym.
    private = [v for c in OWNERS if not c["own"] for v in (c["owner_id"], c["name"])]
    private += [c["name"] for c in OWNERS if c["own"] and c["served_name"]]
    private += [c["description"] for c in OWNERS if not c["described"]]
    keyed = [pseudonym(c["owner_id"]) for c in OWNERS if not c["own"]]
    tables = conn.execute(
        "select table_schema,table_name from information_schema.tables where table_schema in ('main_staging','main_intermediate','main_marts')"
    ).fetchall()
    for schema, table in tables:
        values = private + (keyed if schema == "main_marts" else [])
        columns = conn.execute(
            "select column_name from information_schema.columns where table_schema=? and table_name=?",
            [schema, table],
        ).fetchall()
        for (column,) in columns:
            leaked = conn.execute(
                f'select count(*) from {schema}."{table}" where cast("{column}" as varchar) in ({",".join("?" for _ in values)})',
                values,
            ).fetchone()[0]
            assert leaked == 0, (schema, table, column)


def check(conn):
    check_owners(conn)
    events = "main_marts.mart_playlist_events"
    pairs = conn.execute(
        f"select event_type,count(*) from {events} where playlist_id='pair' group by 1"
    ).fetchall()
    assert dict(pairs) == {"baseline": 3, "add": 1, "remove": 1, "move": 1}, pairs
    for playlist in ("partial", "interrupted", "dangling", "variant", "cross_surface"):
        rows = conn.execute(
            f"select * from {events} where playlist_id=? and event_type='remove'",
            [playlist],
        ).fetchall()
        assert not rows, (playlist, rows)
    rows = conn.execute(
        f"select event_type,interval_id from {events} where playlist_id='readd' order by observed_at"
    ).fetchall()
    # Intervals are keyed by the snapshot that opened them, never by a count.
    opened, reopened = (identity(f"readd:US:{day}:sp_playlist_embed") for day in (1, 3))
    assert rows == [("baseline", opened), ("remove", opened), ("add", reopened)], rows
    rows = conn.execute(
        f"select occurrence_key from {events} where playlist_id='duplicates' and event_type='remove'"
    ).fetchall()
    assert rows == [("u1",)], rows
    rows = conn.execute(
        "select position,page_match,platform_album_id from main_intermediate.int_playlist__observations where playlist_id='enrich' order by position"
    ).fetchall()
    assert rows == [
        (1, False, None),
        (2, True, "album-different-release"),
        (3, True, "album-c"),
    ], rows
    for playlist in ("partial", "interrupted"):
        variant, source = (
            ("us", "am_playlist")
            if playlist == "partial"
            else ("US", "sp_playlist_embed")
        )
        rows = conn.execute(
            "select effective_coverage,effective_content_id from main_staging.stg_playlist__snapshots where playlist_id=? and observation='unchanged'",
            [playlist],
        ).fetchall()
        assert rows == [("partial", identity(f"{playlist}:{variant}:2:{source}"))], rows
        rows = conn.execute(
            "select platform_track_id from main_intermediate.int_playlist__observations where playlist_id=? and snapshot_id=?",
            [playlist, identity(f"{playlist}:{variant}:3:{source}")],
        ).fetchall()
        assert rows == [("a",)], rows  # The older full set's b must not reappear.
    rows = conn.execute(
        "select effective_coverage,effective_content_id from main_staging.stg_playlist__snapshots where playlist_id='full_unchanged' and observation='unchanged'"
    ).fetchall()
    assert rows == [("full", identity("full_unchanged:US:1:sp_playlist_embed"))], rows
    rows = conn.execute(
        f"select event_type,entered_after,removed_after from {events} where playlist_id='full_unchanged' and event_type in ('add','remove') order by event_type"
    ).fetchall()
    assert rows == [
        ("add", mart_time(2), None),
        ("remove", None, mart_time(2)),
    ], rows
    rows = conn.execute(
        f"select platform_track_id,event_type,is_baseline,entered_after from {events} where playlist_id='partial_first' order by platform_track_id"
    ).fetchall()
    assert rows == [
        ("a", "entry_unknown", False, None),
        ("b", "baseline", True, None),
        ("c", "add", False, mart_time(2)),
    ], rows
    rows = conn.execute(
        f"select event_type,interval_id,observed_at from {events} where playlist_id='partial_only' order by observed_at"
    ).fetchall()
    day = {n: identity(f"partial_only:US:{n}:sp_playlist_embed") for n in (1, 3, 5)}
    assert rows == [
        ("entry_unknown", day[1], mart_time(1)),
        ("entered_head", day[3], mart_time(3)),
        ("add", day[5], mart_time(5)),
        ("remove", day[5], mart_time(6)),
    ], rows
    rows = conn.execute(
        "select effective_coverage,effective_content_id from main_staging.stg_playlist__snapshots where playlist_id='cross_surface' and observation='unchanged'"
    ).fetchall()
    assert rows == [("partial", None)], rows
    rows = conn.execute(
        f"select event_type,entered_after from {events} where playlist_id='variant' and variant='uk'"
    ).fetchall()
    assert rows == [("entry_unknown", None)], rows
    rows = conn.execute(
        "select observation,snapshot_hash from main_staging.stg_playlist__snapshots where playlist_id='page_reorder' order by observed_at"
    ).fetchall()
    assert [row[0] for row in rows] == ["content", "content"] and rows[0][1] != rows[1][
        1
    ], rows
    rows = conn.execute(
        "select occurrence_key,occurrence_inferred,platform_row_id from main_staging.stg_playlist__items where playlist_id='page_reorder' order by observed_at,position"
    ).fetchall()
    assert rows == [
        (key, True, None)
        for key in (
            "track:a#1",
            "track:b#1",
            "track:a#2",
            "track:b#1",
            "track:a#1",
            "track:a#2",
        )
    ], rows
    assert not conn.execute(
        f"select * from {events} where playlist_id='page_reorder'"
    ).fetchall()
    dup = conn.execute(
        f"select platform,playlist_id,variant,stream,occurrence_key,interval_id,event_type,observed_at,count(*) from {events} group by 1,2,3,4,5,6,7,8 having count(*)>1"
    ).fetchall()
    assert not dup, dup
    assert conn.execute(
        "select observed_share from main_marts.mart_playlist_coverage where playlist_id='partial_share' order by observed_at"
    ).fetchall() == [(0.5,), (0.5,)]
    assert conn.execute(
        "select effective_coverage from main_staging.stg_playlist__snapshots where playlist_id='explicit_partial' and observation='unchanged'"
    ).fetchall() == [("partial",)]
    assert conn.execute(
        "select effective_content_id from main_staging.stg_playlist__snapshots where playlist_id in ('changed_coverage','changed_content') and observation='unchanged'"
    ).fetchall() == [(None,), (None,)]
    assert conn.execute(
        f"select distinct occurrence_inferred from {events} where platform='apple_music'"
    ).fetchall() == [(True,)]
    assert conn.execute(
        f"select event_type,count(*) from {events} where playlist_id='threshold' and event_type<>'move' group by 1"
    ).fetchall()
    actual = dict(
        conn.execute(
            f"select event_type,count(*) from {events} where playlist_id='insert_race_100' and event_type<>'move' group by 1"
        ).fetchall()
    )
    # The inserted row enters and leaves; t99 is pushed past 100 and returns. At a
    # total of 100 no transition is a true add or remove.
    assert actual == {"baseline": 100, "entered_head": 2, "left_head": 2}, actual
    for playlist in (
        "fetch_race",
        "insert_race",
        "missing_total",
        "missing_page",
        "old_page",
        "wrong_group",
        "wrong_batch",
    ):
        actual = dict(
            conn.execute(
                f"select event_type,count(*) from {events} where playlist_id=? and event_type<>'move' group by 1",
                [playlist],
            ).fetchall()
        )
        assert (
            actual.get("entered_head") == 1
            and actual.get("left_head") == 1
            and "add" not in actual
            and "remove" not in actual
        ), (playlist, actual)
    actual = dict(
        conn.execute(
            f"select event_type,count(*) from {events} where playlist_id='threshold' and event_type<>'move' group by 1"
        ).fetchall()
    )
    # Every threshold observation totals 100 or 101, so none corroborates its extent.
    assert actual == {"baseline": 100, "entered_head": 3, "left_head": 3}, actual
    selected = conn.execute(
        "select distinct playlist_id,stream,extent from main_marts.mart_playlist_membership_current where playlist_id like 'current_%' order by 1"
    ).fetchall()
    assert selected == [
        ("current_clock", "full", "full"),
        ("current_daily_stale", "head", "head_only"),
        ("current_full", "full", "full"),
        ("current_shrink", "head", "full"),
        ("current_stale", "head", "head_only"),
        ("current_weekly_fresh", "full", "full"),
    ], selected
    assert (
        conn.execute(
            f"select count(*) from {events} where playlist_id='apple_count' and event_type='remove'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "select count(*) from main_marts.mart_editorial_entries where event_type<>'add' or entered_after is null"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "select count(*) from main_marts.mart_editorial_presence where event_type not in ('baseline','entry_unknown','entered_head')"
        ).fetchone()[0]
        == 0
    )
    for mart in (
        "playlist_events",
        "playlist_profile",
        "playlist_coverage",
        "playlist_membership_current",
        "editorial_entries",
        "editorial_presence",
    ):
        assert (
            conn.execute(
                f"select count(*) from main_marts.mart_{mart} where learning_eligible is null or resale_permitted is null or source_keys is null"
            ).fetchone()[0]
            == 0
        )
    assert conn.execute(
        "select distinct learning_eligible,resale_permitted from main_marts.mart_playlist_events where playlist_id='pair'"
    ).fetchall() == [(False, False)]
    assert conn.execute(
        "select distinct learning_eligible,resale_permitted from main_marts.mart_playlist_events where playlist_id='missing_page'"
    ).fetchall() == [(True, True)]
    assert conn.execute(
        "select distinct learning_eligible,resale_permitted from main_marts.mart_playlist_events where playlist_id='apple_count'"
    ).fetchall() == [(True, True)]
    assert json.loads(
        conn.execute(
            "select source_keys from main_marts.mart_playlist_events where playlist_id='pair' limit 1"
        ).fetchone()[0]
    ) == ["sp_playlist_embed", "sp_playlist_page"]
    isolation = conn.execute(
        f"select stream,event_type,count(*) from {events} where playlist_id='stream_isolation' group by 1,2 order by 1,2"
    ).fetchall()
    assert isolation == [
        ("full", "add", 1),
        ("full", "baseline", 1),
        ("full", "remove", 1),
        ("head", "baseline", 1),
    ], isolation
    legacy = conn.execute(
        f"select event_type,count(*) from {events} where playlist_id='legacy' group by 1 order by 1"
    ).fetchall()
    assert legacy == [("baseline", 2), ("remove", 1)], legacy
    releases = conn.execute(
        f"select item_type,platform_item_id,featured_track_id,platform_track_id from {events} where playlist_id='releases' and event_type='add'"
    ).fetchall()
    assert releases == [("album", "album-b", "featured", None)], releases
    # A same-instant tie never proves an absence; the removal's proof is the later one.
    tie = conn.execute(
        f"select event_type,occurrence_key,observed_at,removed_after,snapshot_id from {events} where playlist_id='tie' and event_type<>'baseline'"
    ).fetchall()
    assert tie == [
        (
            "remove",
            "track:a#1",
            mart_time(12),
            mart_time(10),
            identity("tie:us:12:am_playlist"),
        )
    ], tie
    tie_move = conn.execute(
        f"select event_type,occurrence_key,observed_at,previous_position,position,uncertainty_hours from {events} where playlist_id='tie_move' and event_type<>'baseline' order by occurrence_key"
    ).fetchall()
    assert tie_move == [
        ("move", "track:a#1", mart_time(12), 1, 2, 48.0),
        ("move", "track:b#1", mart_time(12), 2, 1, 48.0),
    ], tie_move
    # A partial list with a shifted inferred ordinal proves presence and never moves.
    shifted = conn.execute(
        f"select event_type,count(*) from {events} where playlist_id='shifted' group by 1"
    ).fetchall()
    assert shifted == [("baseline", 3)], shifted
    collector = conn.execute(
        f"select event_type,occurrence_key,source_keys from {events} where playlist_id='collector' and event_type in ('add','remove') order by 1"
    ).fetchall()
    assert collector == [
        ("add", "track:c#1", '["sp_playlist"]'),
        ("remove", "track:b#1", '["sp_playlist"]'),
    ], collector
    entries = conn.execute(
        "select occurrence_key,first_ever_entry from main_marts.mart_editorial_entries where playlist_id='collector'"
    ).fetchall()
    assert entries == [("track:c#1", True)], entries
    # Every other platform's full stream: typed events, native ISRC, native row identity.
    soundcloud = conn.execute(
        f"select event_type,platform_track_id from {events} where playlist_id='sc_editorial' and event_type in ('add','remove') order by 1"
    ).fetchall()
    assert soundcloud == [("add", "d"), ("remove", "b")], soundcloud
    isrcs = conn.execute(
        "select count(*),count(isrc) from main_staging.stg_playlist__items where playlist_id='sc_editorial'"
    ).fetchone()
    assert isrcs[0] == isrcs[1] == 6, isrcs
    entries = conn.execute(
        "select platform_track_id from main_marts.mart_editorial_entries where playlist_id='sc_editorial'"
    ).fetchall()
    assert entries == [("d",)], entries
    # Release-to-recording relations stay relations: no track placement appears.
    relations = conn.execute(
        "select release_type,release_id,track_id,is_featured,on_release_page,isrc from main_intermediate.int_bandcamp__release_tracks order by release_id,track_id"
    ).fetchall()
    assert relations == [
        ("album", "album-a", "featured", True, False, "QZFEATURED01"),
        ("album", "album-b", "featured", True, True, "QZFEATURED01"),
        ("album", "album-b", "t1", False, True, None),
    ], relations
    assert not conn.execute(
        f"select * from {events} where platform='bandcamp' and (item_type='track' or platform_track_id is not null)"
    ).fetchall()
    # Coverage age runs to the observing cycle's close, never to execution time.
    ages = conn.execute(
        "select observed_at,as_of,days_since_full from main_marts.mart_playlist_coverage where playlist_id='sc_editorial' order by observed_at"
    ).fetchall()
    closed = CLOSED.replace(tzinfo=None)
    assert ages == [
        (mart_time(10), closed, 12.0),
        (mart_time(11), closed, 11.0),
    ], ages
    print(
        "PASS owner privacy (only the platform's observed own account named and served an owner id and description; labels never count), opening-snapshot interval keys, same-instant ties (no absence, no move), frozen cadence windows, cycle-close clocks, SoundCloud/Untitled/Bandcamp streams and release relations, single-collector source key, shifted partial ordinals, head extent strictly below 100, page-100 and insert-at-60 fetch races, unknown/stale/cross-batch extent, current selection, editorial, rights; pair: 3 baselines, 1 add, 1 remove, 1 move; exact partial/interrupted references, full unchanged bounds, partial-first unknown entries, established absence, two-full removals, cross-surface rejection, dangling, variants, re-add, duplicate uid, position enrichment, event grain"
    )


def bound(conn):
    """After a build bound to the fixture cycle (--vars cycle_id), "now" is its close."""
    selected = conn.execute(
        "select distinct playlist_id,stream,extent from main_marts.mart_playlist_membership_current where playlist_id in ('current_clock','current_full') order by 1"
    ).fetchall()
    assert selected == [
        ("current_clock", "head", "head_only"),
        ("current_full", "full", "full"),
    ], selected
    print(
        "PASS bound cycle close is the clock: a daily full stream three days old is stale"
    )


def fingerprint(conn):
    results = {}
    for name in (
        "playlist_events",
        "playlist_profile",
        "playlist_coverage",
        "playlist_membership_current",
        "editorial_entries",
        "editorial_presence",
    ):
        rows = conn.execute(
            f"select * from main_marts.mart_{name} order by all"
        ).fetchall()
        assert rows, name
        results[name] = hashlib.sha256(
            json.dumps(rows, default=str).encode()
        ).hexdigest()
    path = Path(os.environ["MDP_DEV_DB"]).with_suffix(".replay.json")
    if path.exists():
        assert results == json.loads(path.read_text()), "Replay changed mart rows"
        print("PASS replay: every row and field in all six marts reproduced")
    else:
        path.write_text(json.dumps(results, indent=2))
        print("Saved six-mart replay fingerprints")


if __name__ == "__main__":
    path = os.environ["MDP_DEV_DB"]
    if not Path(path).is_absolute() or "/playlist-fixture/" not in path:
        raise SystemExit("Use a disposable /.../playlist-fixture/ database")
    with duckdb.connect(path) as conn:
        {"load": load, "check": check, "bound": bound, "fingerprint": fingerprint}[
            sys.argv[1]
        ](conn)
