"""raw fixtures for the dbt build: load them after the dbt bootstrap, build the
free-source models, then check. The same rows load into a Postgres `ci` database for the parity gate.

MDP_DEV_DB=/tmp/free-sources.duckdb uv run --project functions python functions/tests/free_sources_dbt_fixture.py load
MDP_DEV_DB=/tmp/free-sources.duckdb uv run --project functions python functions/tests/free_sources_dbt_fixture.py check
uv run --project functions python functions/tests/free_sources_dbt_fixture.py pg-load <postgres url>
uv run --project functions python functions/tests/free_sources_dbt_fixture.py parity <postgres url>
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import duckdb

DAY = date(2026, 9, 24)
TENANT = "7f1b6d8e-0000-4000-8000-00000000f2e0"
GENERATION = "20260923-002121"
RELATIONS = (
    "stg_shazam__chart_entries",
    "mart_shazam_chart_daily",
    "stg_billboard__chart_entries",
    "int_artist_identity",
    "int_wiki__artist_qids",
    "stg_wiki__sitelinks",
    "stg_wiki__lookups",
    "int_wiki__articles",
    "stg_wiki__pageviews",
    "int_label__ownership_closure",
    "int_artist__contract_edges",
    "int_lb__popularity_inputs",
    "stg_lb__popularity",
    "int_lb__sitewide_refreshes",
    "mart_open_listening",
    "stg_lb__fresh_releases",
    "int_lb__similarity_inputs",
    "stg_lb__similar_artists",
    "stg_kexp__plays",
    "int_radio__rotation_events",
    "mart_radio_rotation",
)
def ident(value):
    return str(uuid5(NAMESPACE_URL, "mdp-free-sources:" + value))


def at(day, hour=8, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def lineage(source, key, seq, day=DAY, cycle="cycle:global", target=None):
    return {
        "_run_id": ident(f"run:{source}:{day}"),
        "_dump_id": ident(f"dump:{source}:{key}:{seq}"),
        "_landed_seq": seq,
        "_cycle_id": ident(cycle),
        "_revision_id": None,
        "_target_id": target,
        "_request_id": f"req-{seq}",
        "_source_key": source,
        "_ingested_at": at(day, 9),
        "_extra": "{}",
    }


def shazam_rows():
    """Two charts, one of them landed twice on its chart date (the second landing wins), a
    collaboration row, a page ISRC, and a Billboard week that must stay as it is."""

    def entry(chart, position, song, artist, credit, title, seq, isrc=None, day=DAY):
        return {"chart": chart, "chart_date": day, "position": position, "apple_song_id": song,
                "apple_primary_artist_id": artist, "artist_text": credit, "title_text": title, "isrc": isrc,
                "observed_at": at(day, 6), **lineage("sz_chart", f"{chart}:{day}", seq, day)}

    city = "shazam:top-50:united-states:boston"
    top = "shazam:top-200:united-states"
    rows = [
        entry(top, 1, "6000000101", "9609916580", "Fixture Act 101", "Fixture Song 101", 1),
        entry(top, 2, "6000000102", "1000000102", "Fixture Act 102 & Fixture Guest", "Fixture Song 102", 1),
        entry(top, 3, "6000000103", "1000000103", "Fixture Act 103", "Fixture Song 103", 1, isrc="QZFXA2600103"),
        entry(city, 1, "6000000201", "1000000201", "Fixture Act 201", "Old Title", 2),
        entry(city, 1, "6000000201", "1000000201", "Fixture Act 201", "Fixture Song 201", 3),
        entry(city, 2, "6000000202", "1000000202", "Fixture Act 202, Fixture Guest", "Fixture Song 202", 3),
        entry(city, 1, "6000000202", "1000000202", "Fixture Act 202, Fixture Guest", "Fixture Song 202", 4,
              day=DAY - timedelta(days=1)),
    ]
    week = date(2026, 9, 26)
    billboard = [{"chart": "hot-100", "week": week, "position": n, "title": f"Fixture Track {n}",
                  "artist": f"Fixture Ensemble {n}", "last_week": None, "peak": n, "weeks_on_chart": 1,
                  **lineage("billboard_hot100", f"hot-100:{week}", 10 + n)} for n in (1, 2)]
    # An Apple playlist row whose platform-reported ISRC gives song 6000000101 a track identity.
    item = {"platform": "apple_music", "playlist_id": "pl.fixture0000000000000000000000001", "variant": "us",
            "stream": "full", "observation_group": None, "item_type": "track", "platform_item_id": "6000000101",
            "snapshot_id": "snap-am-1", "observed_at": at(DAY), "position": 1, "platform_track_id": "6000000101",
            "platform_row_id": "row1", "occurrence": 1, "occurrence_key": "track:6000000101#1",
            "occurrence_inferred": False, "title": "Fixture Song 101", "artist_names": json.dumps(["Fixture Act 101"]),
            "platform_artist_ids": json.dumps(["9609916580"]), "isrc": "QZFXA2600101",
            "fetch_surface": "am_playlist", **lineage("am_playlist", "pl.fixture", 20)}
    return {"shazam_chart_entries": rows, "chart_entries": billboard, "playlist_items": [item]}


def artist_gid(n):
    return f"00000000-0000-4000-a000-{n:012d}"


def generation(tables, seq=500):
    """One reconciled mb_spine generation: its rows, its raw.mb_generation row and its run completion."""
    run = ident("run:mb_spine:" + GENERATION)
    stamp = {"mb_channel": "dump", "mb_generation": GENERATION, "mb_sequence": 189207}
    out, outputs, counts = {}, {}, {}
    for name, records in tables.items():
        dump = ident(f"dump:mb:{name}")
        out["mb_" + name] = [{**stamp, **r, "_run_id": run, "_dump_id": dump, "_landed_seq": seq,
                              "_source_key": "mb_spine", "_extra": "{}"} for r in records]
        outputs["raw.mb_" + name], counts["raw.mb_" + name] = {dump: len(records)}, len(records)
    dump = ident("dump:mb:generation")
    out["mb_generation"] = [{**stamp, "mb_key": GENERATION, "export_date": at(DAY, 0), "schema_sequence": 31,
                             "imported_at": at(DAY, 0), "validated_at": at(DAY, 0), "mirror_counts": json.dumps(counts),
                             "read_at": at(DAY, 0), "_run_id": run, "_dump_id": dump, "_landed_seq": seq + 1,
                             "_source_key": "mb_spine", "_extra": "{}"}]
    outputs["raw.mb_generation"] = {dump: 1}
    out["_run_completion"] = [{"run_id": run, "source_key": "mb_spine", "outputs": json.dumps(outputs),
                               "recorded": json.dumps({"generation": GENERATION, "sequence": 189207, "mirror_counts": counts}),
                               "_run_id": run, "_dump_id": ident("completion:mb"), "_landed_seq": seq + 2,
                               "_source_key": "mb_spine", "_extra": "{}"}]
    return out


def label_tables():
    """The 23.11 extension for the wiki acts' MusicBrainz artists: act one's current contract with an
    imprint (whose parent is owned by a group and distributed by a distributor) and an ended one with a
    label since renamed into that parent; act three's contract with a label that has two current parents."""

    def dated(year=None, month=None, day=None, end=None, ended=False):
        return {"begin_year": year, "begin_month": month, "begin_day": day, "end_year": end, "end_month": None,
                "end_day": None, "ended": ended}

    def label(n, name, parent_year=None):
        return {"mb_key": str(n), "label_id": n, "label_gid": f"00000000-0000-4000-7000-{n:012d}", "name": name,
                "type_id": 4, "label_code": None, "begin_year": parent_year, "end_year": None, "ended": False}

    def edge(n, parent, child, kind, **dates):
        return {"mb_key": str(n), "link_id": n, "label_id0": parent, "label_id1": child, "link_type": kind,
                **dated(**dates)}

    return {
        "label": [label(701, "Fixture Imprint"), label(702, "Fixture Records"), label(703, "Fixture Music Group"),
                  label(704, "Fixture Distribution"), label(705, "Fixture Old Records"), label(708, "Fixture Joint Venture"),
                  label(709, "Fixture Partner Group")],
        "l_artist_label": [
            {"mb_key": "1", "link_id": 1, "artist_id": 1, "label_id": 701, "link_type": "recording contract", **dated(2019, 3, 1)},
            {"mb_key": "2", "link_id": 2, "artist_id": 1, "label_id": 705, "link_type": "recording contract", **dated(2012, end=2018, ended=True)},
            {"mb_key": "3", "link_id": 3, "artist_id": 3, "label_id": 708, "link_type": "recording contract", **dated(2021, 6)},
        ],
        "l_label_label": [
            edge(1, 702, 701, "imprint", year=2010), edge(2, 703, 702, "label ownership", year=2004),
            edge(3, 704, 702, "label distribution"), edge(4, 705, 702, "label rename", year=2018),
            edge(5, 703, 708, "label ownership", year=2020), edge(6, 709, 708, "label ownership", year=2020),
            # An ownership that ended no longer makes a parent.
            edge(7, 709, 702, "label ownership", year=1995, end=2003, ended=True),
        ],
        "artist_ipi": [{"mb_key": "1:00000000001", "artist_id": 1, "ipi": "00000000001"}],
        "artist_isni": [{"mb_key": "1:0000000123456789", "artist_id": 1, "isni": "0000000123456789"}],
    }


def wiki_rows():
    """Eight catalog artists: one with articles in two languages (its English article
    renamed a week earlier), one whose Wikidata item has no article, one with a MusicBrainz artist but no
    QID, one with no MusicBrainz artist, one whose sparse article Wikimedia answers on two days only (every
    other fetched day lands as 0), one whose QID has no completed lookup, one read at a flat 100 a day with
    09-16 to 09-20 never fetched, and one fetched once the week before (30 views) and three days of the
    call week (100 a day); the first catalog artist has articles in twelve editions (ten are fetched), and
    the second has only an Apple Music artist page (the chart-entry rule matches it by
    apple_primary_artist_id alone)."""
    cycle = "cycle:global"
    acts = [("ra_one", "spotify", "0FixtureWikiAct000001", 1, "Q9000001"),
            ("ra_two", "apple_music", "1900000002", 2, "Q9000002"),
            ("ra_three", "spotify", "0FixtureWikiAct000003", 3, None),
            ("ra_four", "spotify", "0FixtureWikiAct000004", None, None),
            ("wl_one", "spotify", "0FixtureWikiAct000005", 5, "Q9000005"),
            ("wl_two", "apple_music", "1000000201", None, None),
            ("ra_five", "spotify", "0FixtureWikiAct000006", 6, "Q9000006"),
            ("ra_six", "spotify", "0FixtureWikiAct000007", 7, "Q9000007"),
            ("ra_seven", "spotify", "0FixtureWikiAct000008", 8, "Q9000008"),
            ("ra_eight", "spotify", "0FixtureWikiAct000009", 9, "Q9000009")]
    targets, links, artists = [], [], []
    for key, platform, account, mb, qid in acts:
        if mb is None:
            continue
        artists.append({"mb_key": str(mb), "artist_id": mb, "artist_gid": artist_gid(mb), "name": f"Fixture Act {mb}",
                        "sort_name": f"Act {mb}, Fixture", "type_id": 2})
        kind = "artist"
        url = (f"https://open.spotify.com/artist/{account}" if platform == "spotify"
               else f"https://music.apple.com/us/artist/fixture/{account}")
        links.append({"mb_key": f"artist:{mb}:{platform}", "entity_type": "artist", "link_id": 1, "entity_id": mb,
                      "url_id": 1000 + mb, "url": url, "link_type": "free streaming", "ended": False,
                      "url_platform": platform, "url_kind": kind, "url_platform_id": account})
        if qid:
            links.append({"mb_key": f"artist:{mb}:wikidata", "entity_type": "artist", "link_id": 2, "entity_id": mb,
                          "url_id": 2000 + mb, "url": f"https://www.wikidata.org/wiki/{qid}", "link_type": "wikidata",
                          "ended": False, "url_platform": "wikidata", "url_kind": "entity", "url_platform_id": qid})
    out = {"targets": targets, **generation({"url_link": links, "artist": artists, **label_tables()})}

    def gold(source, qid, seq, **extra):
        return {"input_ref": f"ref-{qid}", "input_version": f"ver-{qid}-{seq}", "scope": "global",
                "step": "external:" + source, "config_version": "cfg-1", "llm_step_id": None, "learning_eligible": True,
                "_source_keys": json.dumps(["mb_spine"]), "_input_cycle": ident(cycle), "run_admitted_at": at(DAY, 7),
                "tenant_id": None, **extra, **lineage(source, f"{qid}:{seq}", seq, cycle=cycle)}

    def link(qid, site, title, week, seq):
        project = site[:-4] + ".wikipedia"
        return {"qid": qid, "entity_qid": qid, "site": site, "project": project, "title": title,
                "observed_at": at(DAY, 7), **gold("wiki_sitelinks", qid, seq, sitelinks_week=week.isoformat())}

    this_week, last_week = date(2026, 9, 21), date(2026, 9, 14)
    # The catalog artist's twelve editions: the ten first in seeds/wiki_projects.csv order are fetched.
    editions = ["en", "ja", "de", "es", "ru", "fr", "it", "zh", "pt", "pl", "cy", "eo"]
    out["wiki_sitelinks"] = [
        link("Q9000001", "enwiki", "Fixture Act", last_week, 60),
        link("Q9000001", "enwiki", "Fixture Act (band)", this_week, 61),
        link("Q9000001", "dewiki", "Fixture Act", this_week, 61),
        *[link("Q9000005", f"{e}wiki", "Fixture Watch Act", this_week, 62) for e in editions],
        link("Q9000006", "enwiki", "Fixture Sparse Act", this_week, 63),
        link("Q9000008", "enwiki", "Fixture Flat Act", this_week, 69),
        link("Q9000009", "enwiki", "Fixture Thin Act", this_week, 70),
    ]

    def lookup(qid, articles, week, seq):
        return {"qid": qid, "entity_qid": qid, "articles": articles, "observed_at": at(DAY, 7),
                **gold("wiki_sitelinks", qid, seq, sitelinks_week=week.isoformat())}

    # One completed lookup per QID and week; Q9000002's item lists no Wikipedia article, and Q9000007 has none.
    out["wiki_lookups"] = [lookup("Q9000001", 1, last_week, 64), lookup("Q9000001", 2, this_week, 65),
                           lookup("Q9000002", 0, this_week, 66), lookup("Q9000005", 12, this_week, 67),
                           lookup("Q9000006", 1, this_week, 68), lookup("Q9000008", 1, this_week, 71),
                           lookup("Q9000009", 1, this_week, 72)]

    def views(qid, project, title, day, count, seq, pageview_day):
        return {"qid": qid, "project": project, "title": title, "date": day, "views": count, "observed_at": at(pageview_day, 7),
                **gold("wiki_pageviews", f"{qid}:{project}:{day}", seq, pageview_day=pageview_day.isoformat())}

    d = [date(2026, 9, n) for n in range(18, 24)]
    out["wiki_pageviews"] = [
        # Under the old title, then the new one: one series by QID and project.
        *[views("Q9000001", "en.wikipedia", "Fixture Act", day, 100 + i, 70 + i, day + timedelta(days=1)) for i, day in enumerate(d[:3])],
        *[views("Q9000001", "en.wikipedia", "Fixture Act (band)", day, 200 + i, 80 + i, DAY) for i, day in enumerate(d[3:])],
        # The 23rd, first fetched with a partial count, refetched the next cycle: the later fetch wins.
        views("Q9000001", "en.wikipedia", "Fixture Act (band)", d[5], 5, 79, d[5]),
        *[views("Q9000001", "de.wikipedia", "Fixture Act", day, 10 + i, 90 + i, DAY) for i, day in enumerate(d[3:])],
        *[views("Q9000005", "en.wikipedia", "Fixture Watch Act", day, 7, 95 + i, DAY) for i, day in enumerate(d[3:])],
        # Fetched every day from 09-13; Wikimedia answered 09-16 (10) and 09-22 (21) only, every other day lands as 0.
        *[views("Q9000006", "en.wikipedia", "Fixture Sparse Act", date(2026, 9, n), {16: 10, 22: 21}.get(n, 0), 120 + n,
                date(2026, 9, n) + timedelta(days=1)) for n in range(13, 24)],
        # A flat 100 a day; no run fetched 09-16 to 09-20, so those days have no row at all.
        *[views("Q9000008", "en.wikipedia", "Fixture Flat Act", date(2026, 9, n), 100, 150 + n,
                date(2026, 9, n) + timedelta(days=1)) for n in (13, 14, 15, 21, 22, 23)],
        # One landed day the week before: too few for the rule, whatever the ratio.
        *[views("Q9000009", "en.wikipedia", "Fixture Thin Act", date(2026, 9, n), 30 if n == 15 else 100, 180 + n,
                date(2026, 9, n) + timedelta(days=1)) for n in (15, 21, 22, 23)],
    ]
    return out


def lb_rows():
    """Act one's ListenBrainz totals through September: a baseline, a change, eight unchanged days while
    the upstream statistics job skips a week (one of them unfetched), then a jump whose raw gain doubles
    the first but whose per-day growth does not, then a steep change in a week the sitewide run did not
    advance; act two has no data; the catalog artist's per-day growth quintuples in the recovery week; act
    six's listeners fall on a recount, then grow. The weekly sitewide fetches see a run, the same run again
    (the skipped week), a newer one, then that one again."""
    cycle = "cycle:global"
    one, two, watch, recount = artist_gid(1), artist_gid(2), artist_gid(5), artist_gid(7)

    def total(mbid, day, listeners, listens, seq):
        return {"entity_type": "artist", "mbid": mbid, "total_listen_count": listens, "total_user_count": listeners,
                "observed_at": at(day, 10), "input_ref": f"ref-{mbid}", "input_version": f"ver-{mbid}-{day}",
                "scope": "global", "step": "external:lb_popularity", "config_version": "cfg-1",
                "llm_step_id": None, "learning_eligible": True, "_source_keys": json.dumps(["mb_spine"]),
                "_input_cycle": ident(cycle), "run_admitted_at": at(day, 9), "mb_artist_gid": mbid,
                "popularity_day": day.isoformat(), "tenant_id": None,
                **lineage("lb_popularity", f"{mbid}:{day}", seq, day=day, cycle=cycle)}

    rows, seq = [], 200
    for n in range(1, 24):
        day = date(2026, 9, n)
        if day == date(2026, 9, 11):
            continue
        value = (1000, 50000) if n < 6 else (1070, 53000) if n < 15 else (1230, 61000) if n < 22 else (1930, 70000)
        rows.append(total(one, day, *value, seq))
        rows.append(total(two, day, None, None, seq + 1))
        rows.append(total(watch, day, *((100, 4000) if n < 6 else (107, 4300) if n < 15 else (170, 6000)), seq + 2))
        # An upstream recount lowers the distinct listeners, then they grow: never a base for growth.
        rows.append(total(recount, day, *((100, 4000) if n < 6 else (93, 4100) if n < 15 else (112, 4600)), seq + 3))
        seq += 4

    def sitewide(day, updated, seq):
        return {"entity_type": "artist", "stats_range": "week", "window_start": at(day - timedelta(days=7), 0),
                "window_end": at(day, 0), "last_updated": updated, "rank": 1, "mbid": one, "name": "Fixture Act 1",
                "artist_mbids": None, "listen_count": 900, "observed_at": at(day, 6),
                **lineage("lb_sitewide", f"sitewide:{day}", seq, day=day)}

    first, second = at(date(2026, 9, 5), 4), at(date(2026, 9, 19), 4)
    sitewide_rows = [sitewide(date(2026, 9, 6), first, 300), sitewide(date(2026, 9, 13), first, 301),
                     sitewide(date(2026, 9, 20), second, 302), sitewide(date(2026, 9, 23), second, 303)]
    fresh = [{"release_mbid": "00000000-0000-4000-d000-000000000202", "release_group_mbid": None,
              "artist_mbids": json.dumps([one]), "artist_credit_name": "Fixture Act 1", "release_name": "Fixture Single",
              "release_group_primary_type": "Single", "release_date": date(2026, 9, 23), "listen_count": 12,
              "observed_at": at(DAY, 7), **lineage("lb_fresh_releases", "fresh", 310)}]
    algorithm = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"

    def neighbour(week, mbid, score, rank, seq, algo=algorithm):
        return {"reference_mbid": one, "neighbour_mbid": mbid, "score": score, "rank": rank, "algorithm": algo,
                "observed_at": at(week, 10), "input_ref": f"sim-{one}", "input_version": f"sim-{one}-{week}-{algo[:10]}",
                "scope": "global", "step": "external:lb_similar_artists", "config_version": "cfg-1",
                "llm_step_id": None, "learning_eligible": False, "_source_keys": json.dumps(["mb_spine"]),
                "_input_cycle": ident(cycle), "run_admitted_at": at(week, 9), "mb_artist_gid": one,
                "similarity_week": week.isoformat(), "tenant_id": None,
                **lineage("lb_similar_artists", f"{week}:{mbid}:{algo[:10]}", seq, day=week, cycle=cycle)}

    older, newer = date(2026, 9, 14), date(2026, 9, 21)
    similar = [
        neighbour(older, artist_gid(90), 950, 1, 400),
        # The newest week names act two (already in the catalog) and one untracked act.
        neighbour(newer, two, 900, 1, 401), neighbour(newer, artist_gid(91), 800, 2, 402),
        # Another algorithm's series is its own; the seed names only one.
        neighbour(newer, artist_gid(92), 999, 1, 403, algo="session_based_days_75_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"),
    ]
    return {"lb_popularity": rows, "lb_sitewide": sitewide_rows, "lb_fresh_releases": fresh, "lb_similar_artists": similar}


def kexp_rows():
    """Recording one enters rotation after a library play and is upgraded to heavy; recording two's first
    play is already in heavy rotation (an add on act one's track in the call window); a play with no
    recording MBID; one play landed twice. A library track plays every day from 06-01, so the landed
    history covers the 90 days before each September event; recording eight enters heavy rotation on 06-05,
    too soon after the history starts for its add to count."""
    def play(n, day, hour, rotation, recording, seq):
        return {"station": "kexp", "play_id": 3700000 + n, "airdate": at(day, hour), "play_type": "trackplay",
                "recording_mbid": recording, "artist_mbids": json.dumps([artist_gid(1)]), "release_group_mbid": None,
                "label_mbids": "[]", "rotation_status": rotation, "is_local": n == 5, "is_request": n == 2,
                "is_live": False, "artist_text": "Fixture Act 1", "song_text": f"Fixture Song {n}",
                "observed_at": at(day, 23), **lineage("kexp_plays", f"kexp:{n}:{seq}", seq, day=day)}

    r1, r2 = "00000000-0000-4000-b000-000000000901", "00000000-0000-4000-b000-000000000902"
    return {"radio_plays": [
        play(1, date(2026, 9, 1), 10, "Library", r1, 500),
        play(2, date(2026, 9, 8), 11, "Light", r1, 501),
        play(3, date(2026, 9, 15), 12, "Heavy", r1, 502),
        play(3, date(2026, 9, 15), 12, "Heavy", r1, 503),
        play(4, date(2026, 9, 16), 9, "Heavy", r1, 504),
        play(5, date(2026, 9, 20), 8, "Heavy", r2, 505),
        play(6, date(2026, 9, 20), 9, None, None, 506),
        {**play(7, date(2026, 6, 5), 14, "Heavy", "00000000-0000-4000-b000-000000000908", 507),
         "artist_mbids": json.dumps([artist_gid(98)])},
        *[{**play(100 + n, date(2026, 6, 1) + timedelta(days=n), 3, "Library", "00000000-0000-4000-b000-000000000909", 600 + n),
           "artist_mbids": json.dumps([artist_gid(99)])} for n in range(112)],
    ]}


def rows():
    out = {}
    for builder in (shazam_rows, wiki_rows, lb_rows, kexp_rows):
        for table, records in builder().items():
            out.setdefault(table, []).extend(records)
    return out


def columns(conn, table, duck):
    if duck:
        return [r[0] for r in conn.execute(f"describe raw.{table}").fetchall()]
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name=%s ORDER BY ordinal_position",
        (table,)).fetchall()]


def load(conn, duck=True):
    for table, records in rows().items():
        names = columns(conn, table, duck)
        missing = {k for r in records for k in r} - set(names)
        assert not missing, (table, missing)
        conn.execute(f"DELETE FROM raw.{table} WHERE _cycle_id = '{records[0]['_cycle_id']}'" if table == "targets" else
                     f"DELETE FROM raw.{table} WHERE _dump_id IN ({','.join(repr(r['_dump_id']) for r in records)})")
        mark = "?" if duck else "%s"
        statement = f"INSERT INTO raw.{table} ({','.join(names)}) VALUES ({','.join([mark] * len(names))})"
        for record in records:
            conn.execute(statement, [record.get(n) for n in names])


def fetch(conn, sql):
    cursor = conn.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def check_shazam(conn):
    staged = fetch(conn, "select * from main_staging.stg_shazam__chart_entries order by chart, chart_date, position")
    assert len(staged) == 6, staged
    city = [r for r in staged if r["city"] == "boston" and r["chart_date"] == DAY]
    # The later landing of the same chart date wins; the day before is its own row.
    assert [r["title_text"] for r in city] == ["Fixture Song 201", "Fixture Song 202"]
    assert {(r["chart_type"], r["country"]) for r in staged} == {("top-200", "united-states"), ("top-50", "united-states")}
    mart = fetch(conn, "select * from main_marts.mart_shazam_chart_daily order by chart, chart_date, position")
    assert len(mart) == len(staged)
    assert len({(r["chart"], r["chart_date"], r["position"]) for r in mart}) == len(mart)
    by = {(r["chart"], r["chart_date"], r["position"]): r for r in mart}
    top = "shazam:top-200:united-states"
    one, two, three = by[(top, DAY, 1)], by[(top, DAY, 2)], by[(top, DAY, 3)]
    # Identity joins by the Apple song id: the tracked Apple row's ISRC reaches song one only.
    assert one["isrc"] == "QZFXA2600101" and one["isrc_source"] not in (None, "page"), one
    assert three["isrc"] == "QZFXA2600103" and three["isrc_source"] == "page"
    assert two["isrc"] is None and two["multi_artist_credit"] is True and one["multi_artist_credit"] is False
    assert by[("shazam:top-50:united-states:boston", DAY, 2)]["multi_artist_credit"] is True
    for row in mart:
        assert set(json.loads(row["source_keys"])) >= {"sz_chart"}
        assert row["learning_eligible"] is False and row["resale_permitted"] is False
    # Billboard stays in its own table and weekly mart, untouched by the Shazam rows.
    billboard = fetch(conn, "select * from main_staging.stg_billboard__chart_entries")
    assert len(billboard) == 2 and {r["source_key"] for r in billboard} == {"billboard_hot100"}
    history = fetch(conn, "select * from main_marts.mart_chart_history")
    assert len(history) == 2 and {r["chart_name"] for r in history} == {"hot-100"}
    print(f"PASS shazam fixture: {len(mart)} chart rows, billboard {len(history)} rows unchanged")


def check_labels(conn):
    owners = {r["label_name"]: r for r in fetch(conn, "select * from main_intermediate.int_label__ownership_closure")}
    # The imprint and the renamed label reach the group through the current chain; an ended ownership and
    # distribution are not ownership; two current parents leave the root unresolved.
    assert owners["Fixture Imprint"]["root_label_name"] == "Fixture Music Group" and owners["Fixture Imprint"]["ownership_depth"] == 2
    assert owners["Fixture Old Records"]["root_label_name"] == "Fixture Music Group"
    assert owners["Fixture Music Group"]["root_label_name"] == "Fixture Music Group" and owners["Fixture Music Group"]["ownership_depth"] == 0
    assert owners["Fixture Distribution"]["root_label_name"] == "Fixture Distribution"
    joint = owners["Fixture Joint Venture"]
    assert joint["ambiguous_parent"] is True and joint["root_label_gid"] is None
    edges = {r["link_id"]: r for r in fetch(conn, "select * from main_intermediate.int_artist__contract_edges")}
    current = edges[1]
    assert (current["artist_gid"], current["label_name"], current["begin_on"], current["end_on"], current["ended"]) == (
        artist_gid(1), "Fixture Imprint", "2019-03-01", None, False)
    assert current["root_label_name"] == "Fixture Music Group" and current["evidence"] == "open data, not contract truth"
    assert current["distributor_label_name"] is None
    old = edges[2]
    assert (old["label_name"], old["begin_on"], old["end_on"], old["ended"], old["root_label_name"]) == (
        "Fixture Old Records", "2012", "2018", True, "Fixture Music Group")
    assert (edges[3]["begin_on"], edges[3]["root_label_gid"], edges[3]["ambiguous_parent"]) == ("2021-06", None, True)
    records = next(r for r in fetch(conn, "select * from main_intermediate.int_label__ownership_closure") if r["label_name"] == "Fixture Records")
    assert records["root_label_name"] == "Fixture Music Group"
    print(f"PASS label fixture: {len(owners)} labels, {len(edges)} contract edges")


def check_kexp(conn):
    plays = fetch(conn, "select * from main_staging.stg_kexp__plays order by play_id")
    assert [p["play_id"] for p in plays][:7] == [3700001, 3700002, 3700003, 3700004, 3700005, 3700006, 3700007]
    assert len(plays) == 7 + 112
    events = [(e["recording_mbid"][-3:], e["event_type"], str(e["airdate"])[:10]) for e in
              fetch(conn, "select * from main_intermediate.int_radio__rotation_events order by recording_mbid, airdate, event_type")]
    # Only events with the 90 days before them in the landed history: the library track's first play (06-01)
    # and recording eight's heavy add (06-05) come too soon after the history starts.
    assert events == [("901", "first_play", "2026-09-01"), ("901", "rotation_add", "2026-09-08"),
                      ("901", "rotation_upgrade", "2026-09-15"), ("902", "first_play", "2026-09-20"),
                      ("902", "rotation_add", "2026-09-20")], events
    weeks = {(r["recording_mbid"][-3:], str(r["week_start"])): r for r in fetch(conn, "select * from main_marts.mart_radio_rotation")}
    assert weeks[("901", "2026-09-14")]["plays"] == 2 and weeks[("901", "2026-09-14")]["top_rotation"] == "Heavy"
    assert weeks[("901", "2026-09-14")]["rotation_upgraded"] is True and weeks[("901", "2026-09-07")]["rotation_added"] is True
    assert weeks[("901", "2026-08-31")]["top_rotation"] == "Library" and weeks[("901", "2026-08-31")]["first_played"] is True
    assert weeks[("902", "2026-09-14")]["rotation_added"] is True and weeks[("902", "2026-09-14")]["local"] is True
    for row in weeks.values():
        assert set(json.loads(row["source_keys"])) >= {"kexp_plays"} and row["learning_eligible"] is False
    print(f"PASS kexp fixture: {len(plays)} plays, {len(events)} rotation events, {len(weeks)} recording weeks")


def check(conn):
    check_shazam(conn)
    check_labels(conn)
    check_kexp(conn)


def normal(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).replace(tzinfo=None).isoformat()
    if isinstance(value, str) and value[:1] in "[{":
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return value if value is None or isinstance(value, (int, bool, str)) else str(value)


def unkeyed(value):
    """Evidence JSON as both engines mean it: hashed evidence keys dropped (mdp_identity_hash differs by
    engine), integral floats as integers, and timestamps with a space separator."""
    if isinstance(value, dict):
        return {k: unkeyed(v) for k, v in value.items() if k != "key"}
    if isinstance(value, list):
        return [unkeyed(v) for v in value]
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 6)
    if isinstance(value, str) and len(value) >= 19 and value[4] == "-" and value[10] == "T":
        return value.replace("T", " ", 1)
    return value


def comparable(names, values):
    """One row as JSON text both engines agree on; evidence arrays go through unkeyed()."""
    return json.dumps([normal(json.dumps(unkeyed(json.loads(v)))) if n == "evidence" and isinstance(v, str) and v[:1] == "["
                       else normal(v) for n, v in zip(names, values, strict=True)], default=str)


def parity(pg):
    import psycopg

    duck = duckdb.connect(os.environ["MDP_DEV_DB"], read_only=True)
    with psycopg.connect(pg) as conn:
        for relation in RELATIONS:
            layer = "marts" if relation.startswith("mart_") else "staging" if relation.startswith("stg_") else "intermediate"
            names = [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                (f"dbt_{layer}", relation)).fetchall()]
            assert names, relation
            # stg_billboard__chart_entries casts its ingestion timestamptz in the session zone.
            keep = [n for n in names if n not in ("_ingested_at", "ingested_at", "input_ref", "input_version", "evidence_keys")]
            cols = ",".join(f'"{n}"' for n in keep)
            left = sorted(comparable(keep, r) for r in conn.execute(f"SELECT {cols} FROM dbt_{layer}.{relation}").fetchall())
            right = sorted(comparable(keep, r) for r in duck.execute(f"SELECT {cols} FROM main_{layer}.{relation}").fetchall())
            status = "identical" if left == right else "DIFFERENT"
            print(f"{relation}: postgres {len(left)} rows, duckdb {len(right)} rows, {status}")
            if left != right:
                print("  only postgres:", sorted(set(left) - set(right))[:2])
                print("  only duckdb:", sorted(set(right) - set(left))[:2])
                raise SystemExit(1)


if __name__ == "__main__":
    command = sys.argv[1]
    if command in ("load", "check"):
        with duckdb.connect(os.environ["MDP_DEV_DB"]) as db:
            load(db) if command == "load" else check(db)
    elif command == "pg-load":
        import psycopg

        with psycopg.connect(sys.argv[2]) as pg:
            load(pg, duck=False)
    elif command == "parity":
        parity(sys.argv[2])
