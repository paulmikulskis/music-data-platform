"""The MusicBrainz mirror: the spine mb_spine lands, the lookups mb_resolve runs, and the platform
URL parsing both share.

The mirror is `mdp-mb-db` on the private network, read as `mb_reader` through `MDP_MB_DB_URL`. Each
generation lives in its own database with one `mdp.generation` row; `ops/fly/mb-db/mdp-schema.sql`
adds that row's table, `mdp.url_tail_id()` and the pg_trgm index on artist_credit.name the lookups read.
"""

import json
import os
import re
import threading
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, create_model

from mdp_functions.errors import ServiceError
from mdp_functions.fetch.guard import transport_network

# Platform URLs the spine lands and the lookups accept. The SQL predicate and the parser read the same
# patterns, so every URL the mirror returns parses.
URL_PATTERNS = {
    "spotify": r"^https?://open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|artist)/([A-Za-z0-9]{22})/?(?:[?#].*)?$",
    "apple_music": r"^https?://(?:music|itunes|geo\.music)\.apple\.com/[^/]*/(album|song|artist)/(?:[^/?#]+/)?(?:id)?([0-9]+)/?(?:[?#].*)?$",
    "bandcamp": r"^https?://([a-z0-9-]+\.bandcamp\.com/(track|album)/[^/?#]+)/?(?:[?#].*)?$",
    "soundcloud": r"^https?://soundcloud\.com/([^/?#]+(?:/[^/?#]+){0,2})/?(?:[?#].*)?$",
    # Reference and social URLs: the Wikidata item that keys the pageview series, and Discogs,
    # Instagram, TikTok and YouTube links as cross-check evidence. Never a seed and never a name match.
    "wikidata": r"^https?://(?:www\.)?wikidata\.org/wiki/(Q[1-9][0-9]*)/?(?:[?#].*)?$",
    "discogs": r"^https?://(?:www\.)?discogs\.com/(artist|label)/([0-9]+)(?:-[^/?#]*)?/?(?:[?#].*)?$",
    "instagram": r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?(?:[?#].*)?$",
    "tiktok": r"^https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._]+)/?(?:[?#].*)?$",
    "youtube": r"^https?://(?:www\.)?youtube\.com/(?:channel/(UC[A-Za-z0-9_-]{22})|@([A-Za-z0-9._-]+))/?(?:[?#].*)?$",
}
KINDS = {"song": "track", "track": "track", "album": "album", "artist": "artist", "sets": "album"}
TRACKED_URL_SQL = "(" + "|".join(f"({p})" for p in URL_PATTERNS.values()) + ")"


def platform_url(url: str) -> tuple[str, str, str] | None:
    """(platform, kind, platform id) for a tracked platform URL, else None. Apple song ids and
    `?i=` album links are tracks; SoundCloud paths are user, user/track or user/sets/name."""
    for platform, pattern in URL_PATTERNS.items():
        match = re.match(pattern, url)
        if not match:
            continue
        if platform == "wikidata":
            return platform, "entity", match.group(1)
        if platform in ("instagram", "tiktok"):
            return platform, "profile", match.group(1)
        if platform == "youtube":
            return (platform, "channel", match.group(1)) if match.group(1) else (platform, "profile", match.group(2))
        if platform == "discogs":
            return platform, match.group(1), match.group(2)
        if platform == "bandcamp":
            return platform, KINDS[match.group(2)], match.group(1)
        if platform == "soundcloud":
            path = match.group(1)
            parts = path.split("/")
            kind = "artist" if len(parts) == 1 else "album" if parts[1] == "sets" else "track"
            return platform, kind, path
        kind, ident = KINDS[match.group(1)], match.group(2)
        song = re.search(r"[?&]i=([0-9]+)", url)
        if platform == "apple_music" and kind == "album" and song:
            return platform, "track", song.group(1)
        return platform, kind, ident
    return None


def normalize(text: Any) -> str:
    """Case, accent, punctuation and whitespace folded, for title and artist comparison."""
    value = unicodedata.normalize("NFKD", str(text or "")).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^\w]+", " ", value)
    return " ".join(value.split())


def mirror_url() -> str:
    url = os.environ.get("MDP_MB_DB_URL", "")
    if not url:
        raise ServiceError("vendor_4xx", "MDP_MB_DB_URL is not configured; the MusicBrainz mirror is unreachable")
    return url


# Waits between three connect attempts. A busy mirror or a promote's rename refuses or stalls a
# connect for moments; only an input whose connect fails every attempt counts against the run's circuit.
CONNECT_BACKOFF_S = (2.0, 5.0)


def connect(url: str | None = None) -> psycopg.Connection:
    url, attempt = url or mirror_url(), 0
    while True:
        try:
            # The mirror is the platform's own private database, read through a declared credential, not
            # source egress: its connect (psycopg resolves the host name in Python) runs on the transport.
            with transport_network():
                return psycopg.connect(url, row_factory=dict_row, connect_timeout=10,
                                       options="-c default_transaction_read_only=on -c statement_timeout=60000")
        except psycopg.OperationalError as exc:
            if attempt == len(CONNECT_BACKOFF_S):
                raise ServiceError("vendor_retryable", "The MusicBrainz mirror is unreachable") from exc
            time.sleep(CONNECT_BACKOFF_S[attempt])
            attempt += 1


GENERATION_SQL = (
    "SELECT generation, export_date, replication_sequence, schema_sequence, imported_at, validated_at, counts "
    "FROM mdp.generation ORDER BY generation DESC LIMIT 1"
)


# The spine is scoped to the tracked catalog: the closure of the ISRCs, platform track, album and
# artist URLs and resolved recordings the identity chain holds. Seeds resolve to MusicBrainz ids, the
# ids close upward (tracks, mediums, releases, release groups, artist credits, artists), and every
# table reads its rows by those ids. mdp.tracked_url holds the URLs matching TRACKED_URL_SQL, computed
# once per generation at import (mdp-schema.sql); mdp.url_tail_id() narrows URLs by platform id.
@dataclass
class Seeds:
    isrcs: set[str] = field(default_factory=set)
    urls: set[tuple[str, str, str]] = field(default_factory=set)
    recording_gids: set[str] = field(default_factory=set)

    def __len__(self) -> int:
        return len(self.isrcs) + len(self.urls) + len(self.recording_gids)


ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")
GID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    return list(value or []) if isinstance(value, list) else []


def tracked_seeds(inputs: list[dict[str, Any]]) -> Seeds:
    """The seeds of the tracked catalog: every track input's platform ISRC and its platform track, album
    and artist ids."""
    seeds = Seeds()
    for row in inputs:
        platform = row["platform"]
        if row.get("platform_isrc"):
            seeds.isrcs.add(str(row["platform_isrc"]).strip().upper())
        seeds.urls.add((platform, "track", str(row["platform_track_id"])))
        if row.get("platform_album_id"):
            seeds.urls.add((platform, "album", str(row["platform_album_id"])))
        for artist in json_list(row.get("platform_artist_ids")):
            seeds.urls.add((platform, "artist", str(artist)))
    seeds.isrcs = {i for i in seeds.isrcs if ISRC_RE.match(i)}
    return seeds


def url_tail(platform_id: str) -> str:
    """What mdp.url_tail_id() returns for a platform URL carrying this id: its last path segment."""
    return platform_id.rstrip("/").rsplit("/", 1)[-1]


SEED_URLS = """
SELECT u.id, u.url FROM musicbrainz.url u JOIN mdp.tracked_url t ON t.id = u.id
WHERE mdp.url_tail_id(u.url) IN (SELECT unnest(%s::text[]))"""
SEED_LINKS = """
SELECT 'recording' AS entity_type, entity0 AS entity_id FROM musicbrainz.l_recording_url WHERE entity1 IN (SELECT unnest(%(urls)s::int[]))
UNION ALL SELECT 'release', entity0 FROM musicbrainz.l_release_url WHERE entity1 IN (SELECT unnest(%(urls)s::int[]))
UNION ALL SELECT 'artist', entity0 FROM musicbrainz.l_artist_url WHERE entity1 IN (SELECT unnest(%(urls)s::int[]))"""
SEED_ISRCS = "SELECT DISTINCT recording FROM musicbrainz.isrc WHERE isrc IN (SELECT unnest(%s::text[])::char(12))"
SEED_GIDS = """
SELECT id FROM musicbrainz.recording WHERE gid IN (SELECT unnest(%(gids)s::uuid[]))
UNION SELECT new_id FROM musicbrainz.recording_gid_redirect WHERE gid IN (SELECT unnest(%(gids)s::uuid[]))"""
CLOSE_TRACKS = "SELECT id, medium, artist_credit FROM musicbrainz.track WHERE recording IN (SELECT unnest(%s::int[]))"
CLOSE_MEDIUMS = "SELECT DISTINCT release FROM musicbrainz.medium WHERE id IN (SELECT unnest(%s::int[]))"
CLOSE_RECORDINGS = "SELECT artist_credit FROM musicbrainz.recording WHERE id IN (SELECT unnest(%s::int[]))"
CLOSE_RELEASES = "SELECT release_group, artist_credit FROM musicbrainz.release WHERE id IN (SELECT unnest(%s::int[]))"
CLOSE_GROUPS = "SELECT artist_credit FROM musicbrainz.release_group WHERE id IN (SELECT unnest(%s::int[]))"
CLOSE_ARTISTS = "SELECT DISTINCT artist FROM musicbrainz.artist_credit_name WHERE artist_credit IN (SELECT unnest(%s::int[]))"


def seed_ids(conn: psycopg.Connection, seeds: Seeds) -> dict[str, set[int]]:
    """The recordings, releases and artists the seeds name: ISRC rows, tracked URLs whose platform, kind
    and id equal a seed's (narrowed by URL tail, checked by the parser), and recordings by gid or merge
    redirect."""
    ids: dict[str, set[int]] = {"recordings": set(), "releases": set(), "artists": set()}
    if seeds.urls:
        tails = sorted({url_tail(pid) for _, _, pid in seeds.urls})
        urls = [r["id"] for r in conn.execute(SEED_URLS, (tails,)).fetchall() if platform_url(r["url"]) in seeds.urls]
        if urls:
            for r in conn.execute(SEED_LINKS, {"urls": urls}).fetchall():
                ids[r["entity_type"] + "s"].add(r["entity_id"])
    if seeds.isrcs:
        ids["recordings"] |= {r["recording"] for r in conn.execute(SEED_ISRCS, (sorted(seeds.isrcs),)).fetchall()}
    if seeds.recording_gids:
        ids["recordings"] |= {r["id"] for r in conn.execute(SEED_GIDS, {"gids": sorted(seeds.recording_gids)}).fetchall()}
    return ids


def closure(conn: psycopg.Connection, recordings: Iterable[int], releases: Iterable[int] = (),
            artists: Iterable[int] = (), tracks: bool = True) -> dict[str, list[int]]:
    """The upward closure of recordings, releases and artists: with `tracks`, every track of a recording
    and the medium and release it sits on; then release groups, the artist credits of every recording,
    track, release and group, and their artists."""
    recordings, releases, artists = set(recordings), set(releases), set(artists)
    track_ids, mediums, credits = set(), set(), set()

    def rows(query: str, values: set[int]) -> list[dict[str, Any]]:
        return conn.execute(query, (sorted(values),)).fetchall() if values else []

    if tracks:
        for r in rows(CLOSE_TRACKS, recordings):
            track_ids.add(r["id"])
            mediums.add(r["medium"])
            credits.add(r["artist_credit"])
        releases |= {r["release"] for r in rows(CLOSE_MEDIUMS, mediums)}
    credits |= {r["artist_credit"] for r in rows(CLOSE_RECORDINGS, recordings)}
    groups = set()
    for r in rows(CLOSE_RELEASES, releases):
        groups.add(r["release_group"])
        credits.add(r["artist_credit"])
    credits |= {r["artist_credit"] for r in rows(CLOSE_GROUPS, groups)}
    artists |= {r["artist"] for r in rows(CLOSE_ARTISTS, credits)}
    return {"recordings": sorted(recordings), "tracks": sorted(track_ids), "mediums": sorted(mediums),
            "releases": sorted(releases), "release_groups": sorted(groups), "credits": sorted(credits),
            "artists": sorted(artists)}


# The label closure: the labels a closure artist has an affiliation with, then each label's
# parents up to the root owner. Relationship directions, from MusicBrainz's relationship-type docs: in
# `label ownership` ("is/was the parent label of") and `imprint` ("has imprint") the parent is entity0;
# in `label distribution` the distributor is entity0; in `label rename` the successor is entity1.
# Employment types (positions at a label) are about people's jobs and never land.
ARTIST_LABEL_TYPES = ("recording contract", "personal label", "personal publisher", "owner", "label founder")
PARENT_LABEL_TYPES = ("label ownership", "imprint", "label distribution")
SUCCESSOR_LABEL_TYPES = ("label rename",)
MAX_LABEL_DEPTH = 10


def sql_names(names: Iterable[str]) -> str:
    """A constant list of link type names as an SQL array literal."""
    return "ARRAY[" + ",".join("'" + n.replace("'", "''") + "'" for n in names) + "]::text[]"


CLOSE_ARTIST_LABELS = f"""
SELECT DISTINCT l.entity1 AS label FROM musicbrainz.l_artist_label l
JOIN musicbrainz.link k ON k.id = l.link JOIN musicbrainz.link_type t ON t.id = k.link_type
WHERE t.name = ANY({sql_names(ARTIST_LABEL_TYPES)}) AND l.entity0 IN (SELECT unnest(%s::int[]))"""
CLOSE_LABEL_PARENTS = f"""
SELECT l.entity0 AS label FROM musicbrainz.l_label_label l
JOIN musicbrainz.link k ON k.id = l.link JOIN musicbrainz.link_type t ON t.id = k.link_type
WHERE t.name = ANY({sql_names(PARENT_LABEL_TYPES)}) AND l.entity1 IN (SELECT unnest(%(labels)s::int[]))
UNION
SELECT l.entity1 FROM musicbrainz.l_label_label l
JOIN musicbrainz.link k ON k.id = l.link JOIN musicbrainz.link_type t ON t.id = k.link_type
WHERE t.name = ANY({sql_names(SUCCESSOR_LABEL_TYPES)}) AND l.entity0 IN (SELECT unnest(%(labels)s::int[]))"""


def label_closure(conn: psycopg.Connection, artists: Iterable[int]) -> list[int]:
    """The labels the closure's artists reach, and every parent, distributor and successor up to the root
    owner, at most MAX_LABEL_DEPTH steps."""
    artists = sorted(set(artists))
    labels = {r["label"] for r in conn.execute(CLOSE_ARTIST_LABELS, (artists,)).fetchall()} if artists else set()
    frontier = set(labels)
    for _ in range(MAX_LABEL_DEPTH):
        if not frontier:
            break
        frontier = {r["label"] for r in conn.execute(CLOSE_LABEL_PARENTS, {"labels": sorted(frontier)}).fetchall()} - labels
        labels |= frontier
    return sorted(labels)


def ids_in(name: str) -> str:
    return f"IN (SELECT unnest(%({name})s::int[]))"


LINK_DATES = """k.begin_date_year AS begin_year, k.begin_date_month AS begin_month, k.begin_date_day AS begin_day,
       k.end_date_year AS end_year, k.end_date_month AS end_month, k.end_date_day AS end_day, k.ended"""


# table -> (the ordered key columns the cursor resumes after, SELECT over the closure's id arrays).
# mb_key is the key as text.
SPINE = {
    "raw.mb_url_link": (["entity_type", "link_id"], f"""
SELECT l.entity_type, l.link_id, l.entity_id, l.url_id, u.url, lt.name AS link_type, k.ended,
       l.entity_type || ':' || l.link_id AS mb_key
FROM (SELECT 'recording'::text AS entity_type, id AS link_id, entity0 AS entity_id, entity1 AS url_id, link
      FROM musicbrainz.l_recording_url WHERE entity0 {ids_in("recordings")}
      UNION ALL SELECT 'release', id, entity0, entity1, link FROM musicbrainz.l_release_url WHERE entity0 {ids_in("releases")}
      UNION ALL SELECT 'artist', id, entity0, entity1, link FROM musicbrainz.l_artist_url WHERE entity0 {ids_in("artists")}) l
JOIN mdp.tracked_url t ON t.id = l.url_id JOIN musicbrainz.url u ON u.id = l.url_id
JOIN musicbrainz.link k ON k.id = l.link JOIN musicbrainz.link_type lt ON lt.id = k.link_type"""),
    "raw.mb_isrc": (["isrc_id"], f"""
SELECT i.id AS isrc_id, trim(i.isrc) AS isrc, i.recording AS recording_id, i.id::text AS mb_key
FROM musicbrainz.isrc i WHERE i.recording {ids_in("recordings")}"""),
    "raw.mb_recording": (["recording_id"], f"""
SELECT r.id AS recording_id, r.gid::text AS recording_gid, r.name, r.artist_credit AS artist_credit_id,
       r.length AS length_ms, r.video, r.id::text AS mb_key
FROM musicbrainz.recording r WHERE r.id {ids_in("recordings")}"""),
    "raw.mb_track": (["track_id"], f"""
SELECT t.id AS track_id, t.gid::text AS track_gid, t.recording AS recording_id, t.medium AS medium_id,
       t.position, t.number, t.name, t.artist_credit AS artist_credit_id, t.length AS length_ms,
       t.id::text AS mb_key
FROM musicbrainz.track t WHERE t.id {ids_in("tracks")}"""),
    "raw.mb_medium": (["medium_id"], f"""
SELECT m.id AS medium_id, m.release AS release_id, m.position, m.format AS format_id, m.track_count,
       m.id::text AS mb_key
FROM musicbrainz.medium m WHERE m.id {ids_in("mediums")}"""),
    "raw.mb_release": (["release_id"], f"""
SELECT r.id AS release_id, r.gid::text AS release_gid, r.name, r.artist_credit AS artist_credit_id,
       r.release_group AS release_group_id, r.barcode, r.status AS status_id, r.id::text AS mb_key
FROM musicbrainz.release r WHERE r.id {ids_in("releases")}"""),
    "raw.mb_release_group": (["release_group_id"], f"""
SELECT g.id AS release_group_id, g.gid::text AS release_group_gid, g.name, g.artist_credit AS artist_credit_id,
       g.type AS type_id, g.id::text AS mb_key
FROM musicbrainz.release_group g WHERE g.id {ids_in("release_groups")}"""),
    "raw.mb_artist_credit": (["artist_credit_id"], f"""
SELECT c.id AS artist_credit_id, c.name, c.artist_count, c.id::text AS mb_key
FROM musicbrainz.artist_credit c WHERE c.id {ids_in("credits")}"""),
    "raw.mb_artist_credit_name": (["artist_credit_id", "position"], f"""
SELECT n.artist_credit AS artist_credit_id, n.position, n.artist AS artist_id, n.name, n.join_phrase,
       n.artist_credit || ':' || n.position AS mb_key
FROM musicbrainz.artist_credit_name n WHERE n.artist_credit {ids_in("credits")}"""),
    "raw.mb_artist": (["artist_id"], f"""
SELECT a.id AS artist_id, a.gid::text AS artist_gid, a.name, a.sort_name, a.type AS type_id,
       a.id::text AS mb_key
FROM musicbrainz.artist a WHERE a.id {ids_in("artists")}"""),
    "raw.mb_l_artist_label": (["link_id"], f"""
SELECT l.id AS link_id, l.entity0 AS artist_id, l.entity1 AS label_id, lt.name AS link_type, {LINK_DATES},
       l.id::text AS mb_key
FROM musicbrainz.l_artist_label l JOIN musicbrainz.link k ON k.id = l.link JOIN musicbrainz.link_type lt ON lt.id = k.link_type
WHERE l.entity0 {ids_in("artists")} AND lt.name = ANY({sql_names(ARTIST_LABEL_TYPES)})"""),
    "raw.mb_label": (["label_id"], f"""
SELECT b.id AS label_id, b.gid::text AS label_gid, b.name, b.type AS type_id, b.label_code,
       b.begin_date_year AS begin_year, b.end_date_year AS end_year, b.ended, b.id::text AS mb_key
FROM musicbrainz.label b WHERE b.id {ids_in("labels")}"""),
    "raw.mb_l_label_label": (["link_id"], f"""
SELECT l.id AS link_id, l.entity0 AS label_id0, l.entity1 AS label_id1, lt.name AS link_type, {LINK_DATES},
       l.id::text AS mb_key
FROM musicbrainz.l_label_label l JOIN musicbrainz.link k ON k.id = l.link JOIN musicbrainz.link_type lt ON lt.id = k.link_type
WHERE l.entity0 {ids_in("labels")} AND l.entity1 {ids_in("labels")}"""),
    "raw.mb_artist_ipi": (["artist_id", "ipi"], f"""
SELECT i.artist AS artist_id, trim(i.ipi) AS ipi, i.artist || ':' || trim(i.ipi) AS mb_key
FROM musicbrainz.artist_ipi i WHERE i.artist {ids_in("artists")}"""),
    "raw.mb_artist_isni": (["artist_id", "isni"], f"""
SELECT i.artist AS artist_id, trim(i.isni) AS isni, i.artist || ':' || trim(i.isni) AS mb_key
FROM musicbrainz.artist_isni i WHERE i.artist {ids_in("artists")}"""),
    "raw.mb_redirect": (["entity_type", "gid"], f"""
SELECT 'recording'::text AS entity_type, x.gid::text AS gid, x.new_id, 'recording:' || x.gid AS mb_key
FROM musicbrainz.recording_gid_redirect x WHERE x.new_id {ids_in("recordings")}
UNION ALL
SELECT 'release', x.gid::text, x.new_id, 'release:' || x.gid
FROM musicbrainz.release_gid_redirect x WHERE x.new_id {ids_in("releases")}
UNION ALL
SELECT 'artist', x.gid::text, x.new_id, 'artist:' || x.gid
FROM musicbrainz.artist_gid_redirect x WHERE x.new_id {ids_in("artists")}"""),
}


def spine_query(table: str, ids: dict[str, list[int]], after: list[Any] | None) -> tuple[str, dict[str, Any]]:
    """The table's rows for the closure `ids` in key order, after the cursor's last key when resuming."""
    keys, select = SPINE[table]
    params: dict[str, Any] = dict(ids)
    where = ""
    if after is not None:
        where = f" WHERE ({', '.join(keys)}) > ({', '.join(f'%(after{i})s' for i in range(len(keys)))})"
        params.update({f"after{i}": v for i, v in enumerate(after)})
    return f"SELECT * FROM ({select}) s{where} ORDER BY {', '.join(keys)}", params


def spine_row(table: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """A landed row: URL relationships carry the parsed platform, kind and id; a URL the parser does not
    recognise is None."""
    if table != "raw.mb_url_link":
        return row
    parsed = platform_url(row["url"])
    if parsed is None:
        return None
    return {**row, "url_platform": parsed[0], "url_kind": parsed[1], "url_platform_id": parsed[2]}


def closure_rows(conn: psycopg.Connection, ids: dict[str, list[int]], skip: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Every spine row of a small closure, each tagged with its table name (mb_table) as landed."""
    out = []
    for table in (t for t in SPINE if t not in skip):
        query, params = spine_query(table, ids, None)
        for row in conn.execute(query, params).fetchall():
            if (row := spine_row(table, row)) is not None:
                out.append({"mb_table": table.removeprefix("raw.mb_"), **row})
    return out


class MbRow(BaseModel):
    mb_channel: str
    mb_generation: str
    mb_sequence: int
    mb_key: str


class MbUrlLink(MbRow):
    entity_type: str
    link_id: int
    entity_id: int
    url_id: int
    url: str
    link_type: str
    ended: bool | None = None
    url_platform: str
    url_kind: str
    url_platform_id: str


class MbIsrc(MbRow):
    isrc_id: int
    isrc: str
    recording_id: int


class MbRecording(MbRow):
    recording_id: int
    recording_gid: str
    name: str
    artist_credit_id: int
    length_ms: int | None = None
    video: bool | None = None


class MbTrack(MbRow):
    track_id: int
    track_gid: str
    recording_id: int
    medium_id: int
    position: int
    number: str | None = None
    name: str
    artist_credit_id: int
    length_ms: int | None = None


class MbMedium(MbRow):
    medium_id: int
    release_id: int
    position: int
    format_id: int | None = None
    track_count: int | None = None


class MbRelease(MbRow):
    release_id: int
    release_gid: str
    name: str
    artist_credit_id: int
    release_group_id: int
    barcode: str | None = None
    status_id: int | None = None


class MbReleaseGroup(MbRow):
    release_group_id: int
    release_group_gid: str
    name: str
    artist_credit_id: int
    type_id: int | None = None


class MbArtistCredit(MbRow):
    artist_credit_id: int
    name: str
    artist_count: int


class MbArtistCreditName(MbRow):
    artist_credit_id: int
    position: int
    artist_id: int
    name: str
    join_phrase: str | None = None


class MbArtist(MbRow):
    artist_id: int
    artist_gid: str
    name: str
    sort_name: str | None = None
    type_id: int | None = None


class MbRedirect(MbRow):
    entity_type: str
    gid: str
    new_id: int


class MbArtistLabel(MbRow):
    """A dated artist-label affiliation: a recording contract, a personal label or publisher, ownership or
    founding of the label. Open data, not contract truth."""

    link_id: int
    artist_id: int
    label_id: int
    link_type: str
    begin_year: int | None = None
    begin_month: int | None = None
    begin_day: int | None = None
    end_year: int | None = None
    end_month: int | None = None
    end_day: int | None = None
    ended: bool | None = None


class MbLabel(MbRow):
    label_id: int
    label_gid: str
    name: str
    type_id: int | None = None
    label_code: int | None = None
    begin_year: int | None = None
    end_year: int | None = None
    ended: bool | None = None


class MbLabelLink(MbRow):
    """A dated relationship between two closure labels (ownership, imprint, distribution, rename)."""

    link_id: int
    label_id0: int
    label_id1: int
    link_type: str
    begin_year: int | None = None
    begin_month: int | None = None
    begin_day: int | None = None
    end_year: int | None = None
    end_month: int | None = None
    end_day: int | None = None
    ended: bool | None = None


class MbArtistIpi(MbRow):
    artist_id: int
    ipi: str


class MbArtistIsni(MbRow):
    artist_id: int
    isni: str


class MbGeneration(MbRow):
    export_date: datetime
    schema_sequence: int
    imported_at: datetime
    validated_at: datetime
    # Spine rows read from the mirror per table in the landing snapshot, as JSON text.
    mirror_counts: str
    read_at: datetime


SCHEMAS: dict[str, type[BaseModel]] = {
    "raw.mb_url_link": MbUrlLink, "raw.mb_isrc": MbIsrc, "raw.mb_recording": MbRecording,
    "raw.mb_track": MbTrack, "raw.mb_medium": MbMedium, "raw.mb_release": MbRelease,
    "raw.mb_release_group": MbReleaseGroup, "raw.mb_artist_credit": MbArtistCredit,
    "raw.mb_artist_credit_name": MbArtistCreditName, "raw.mb_artist": MbArtist,
    "raw.mb_l_artist_label": MbArtistLabel, "raw.mb_label": MbLabel, "raw.mb_l_label_label": MbLabelLink,
    "raw.mb_artist_ipi": MbArtistIpi, "raw.mb_artist_isni": MbArtistIsni,
    "raw.mb_redirect": MbRedirect, "raw.mb_generation": MbGeneration,
}
# Catalog extension: landed with a generation, never by an mb_resolve answer, which touches only the
# recording identity tables.
EXTENSION = ("raw.mb_l_artist_label", "raw.mb_label", "raw.mb_l_label_label", "raw.mb_artist_ipi", "raw.mb_artist_isni")


def closure_schema() -> type[BaseModel]:
    """raw.mb_resolve_closure: one spine row an mb_resolve answer touched, in the raw.mb_* row shape (its
    table's columns, the rest null), with the generation the lookup read."""
    fields: dict[str, Any] = {"mb_table": (str, ...), "mb_key": (str, ...), "mb_generation": (str | None, None),
                              "mb_sequence": (int | None, None)}
    for table, model in SCHEMAS.items():
        if table != "raw.mb_generation" and table not in EXTENSION:
            for name, info in model.model_fields.items():
                if name not in MbRow.model_fields:
                    fields.setdefault(name, (info.annotation | None, None))
    return create_model("MbResolveClosure", **fields)


MbResolveClosure = closure_schema()


# Lookups (mb_resolve). One connection per process, reopened after a failure or a promote.
_LOCK = threading.Lock()
_CONN: dict[tuple[str, str], psycopg.Connection] = {}


def lookup_connection(namespace: str = "default") -> psycopg.Connection:
    url = mirror_url()
    with _LOCK:
        key = (url, namespace)
        conn = _CONN.get(key)
        if conn is None or conn.closed or conn.broken:
            conn = _CONN[key] = connect(url)
            conn.autocommit = True
            lookup_session(conn)
        return conn


def lookup_session(conn: psycopg.Connection) -> None:
    """pg_trgm's operators run as the caller (the first import placed them in musicbrainz), and `%` and
    show_limit() read the session threshold: at TRIGRAM_FLOOR the index returns only candidates the
    lookup can keep, so a LIMIT never drops one for a candidate below the floor."""
    conn.execute(f"SET search_path = musicbrainz, public; SET pg_trgm.similarity_threshold = {TRIGRAM_FLOOR}")


def reset_lookup_connection(namespace: str | None = None) -> None:
    with _LOCK:
        for key in list(_CONN):
            if namespace is None or key[1] == namespace:
                _CONN.pop(key).close()


# The URL ids come first from the tail index, then their release links through l_release_url's entity1
# index. As a plain join the planner sizes the tail by the expression index's statistics, and with none
# (an index newer than the table's ANALYZE) it may overestimate matches and hash-join a full scan of
# l_release_url: 850 ms a lookup instead of 11.
RELEASES_BY_ALBUM = """
SELECT u.url, l.entity0 AS release_id
FROM musicbrainz.url u JOIN musicbrainz.l_release_url l ON l.entity1 = u.id
WHERE u.id = ANY(ARRAY(SELECT id FROM musicbrainz.url WHERE mdp.url_tail_id(url) = %s))"""
RELEASE_CANDIDATES = """
SELECT t.recording AS recording_id, r.gid::text AS recording_gid, t.name AS track_name, r.name AS recording_name,
       coalesce(t.length, r.length) AS length_ms, tc.name AS track_credit, rc.name AS recording_credit,
       m.release AS release_id, rel.gid::text AS release_gid
FROM musicbrainz.medium m JOIN musicbrainz.track t ON t.medium = m.id
JOIN musicbrainz.recording r ON r.id = t.recording
JOIN musicbrainz.artist_credit tc ON tc.id = t.artist_credit
JOIN musicbrainz.artist_credit rc ON rc.id = r.artist_credit
JOIN musicbrainz.release rel ON rel.id = m.release
WHERE m.release = ANY(%s)"""
# Title and credit by trigram similarity, length within two seconds: the lower-precedence method. The
# credits come first, from the GIN index on artist_credit.name, and their recordings through
# recording_idx_artist_credit. A short common title ("Night", "Free") shares trigrams with hundreds of
# thousands of the 40M recording names, so a lookup that starts from the title spends 5 to 60 s
# rechecking them; the title is compared through similarity() so the planner cannot start there.
# Known limit (unchanged by the credit-first shape): the floor applies to the whole credit name and to
# the first platform artist only, so "A feat. B" and "A & B" credits ("Fixture Artist Gamma & Fixture Artist Delta" for "Fixture Artist Gamma", 0.55)
# and spelling variants the C ctype splits ("Fíxture" for "Fixture", 0.33) stay unmatched.
TRIGRAM_CANDIDATES = """
SELECT r.id AS recording_id, r.gid::text AS recording_gid, r.name AS recording_name, r.length AS length_ms,
       c.name AS recording_credit, similarity(r.name, %(title)s) AS title_sim, similarity(c.name, %(artist)s) AS artist_sim
FROM musicbrainz.recording r JOIN musicbrainz.artist_credit c ON c.id = r.artist_credit
WHERE r.artist_credit = ANY(ARRAY(SELECT id FROM musicbrainz.artist_credit WHERE name %% %(artist)s))
  AND similarity(r.name, %(title)s) >= show_limit() AND r.length BETWEEN %(low)s AND %(high)s
ORDER BY similarity(r.name, %(title)s) + similarity(c.name, %(artist)s) DESC, r.id
LIMIT 50"""
ISRCS = "SELECT trim(isrc) AS isrc FROM musicbrainz.isrc WHERE recording = %s ORDER BY isrc"
TRIGRAM_FLOOR = 0.6
# The mirror's ctype is C, where pg_trgm counts only ASCII letters and digits as word characters: a
# title or artist without one ("米津玄師", "!!!") has no trigrams and can never reach the floor, and
# searching it reads the whole artist_credit index.
TRIGRAM_WORD = re.compile(r"[A-Za-z0-9]")
DURATION_MS = 2000


def artist_names(value: Any) -> list[str]:
    return [str(v) for v in json_list(value) if v]


def artist_agrees(names: list[str], credits: list[str]) -> bool:
    """A platform artist name and an artist credit agree when either contains the other, folded."""
    folded = [normalize(c) for c in credits if c]
    return any(n and any(n in c or c in n for c in folded) for n in map(normalize, names))


def within(duration_ms: Any, length_ms: Any) -> bool:
    return duration_ms is None or (length_ms is not None and abs(int(length_ms) - int(duration_ms)) <= DURATION_MS)


def resolve(conn: psycopg.Connection, row: dict[str, Any]) -> dict[str, Any]:
    """One priority track against the mirror: its platform album URL to the release, then candidates by
    title, artist credit and duration within two seconds (never playlist position); when no release
    candidate matches, title and credit by trigram when both have trigrams. More than one candidate
    recording stays unresolved with its count. Negative results carry the generation read too."""
    started = time.perf_counter()
    generation = conn.execute(GENERATION_SQL).fetchone() or {}
    title, names = row.get("title"), artist_names(row.get("artist_names"))
    duration = row.get("duration_ms")
    result: dict[str, Any] = {
        "platform": row["platform"], "platform_track_id": row["platform_track_id"],
        "status": "unmatched", "method": None, "recording_id": None, "recording_gid": None,
        "isrc": None, "isrc_count": 0, "release_id": None, "release_gid": None, "candidate_count": 0,
        "confidence": None, "evidence": None, "closure": None,
        "mb_generation": generation.get("generation"), "mb_sequence": generation.get("replication_sequence"),
    }
    found: dict[int, dict[str, Any]] = {}
    method = None
    album = row.get("platform_album_id")
    if album:
        releases = sorted({
            r["release_id"] for r in conn.execute(RELEASES_BY_ALBUM, (str(album),)).fetchall()
            if platform_url(r["url"]) == (row["platform"], "album", str(album))
        })
        if releases:
            result["evidence"] = f"album:{album}->releases:{','.join(map(str, releases))}"
            for c in conn.execute(RELEASE_CANDIDATES, (releases,)).fetchall():
                titled = normalize(title) in {normalize(c["track_name"]), normalize(c["recording_name"])}
                if titled and artist_agrees(names, [c["track_credit"], c["recording_credit"]]) and within(duration, c["length_ms"]):
                    found.setdefault(c["recording_id"], c)
            method = "mb_release" if found else None
    if not found and title and names and duration is not None and TRIGRAM_WORD.search(str(title)) \
            and TRIGRAM_WORD.search(names[0]):
        for c in conn.execute(TRIGRAM_CANDIDATES, {"title": title, "artist": names[0],
                                                     "low": int(duration) - DURATION_MS, "high": int(duration) + DURATION_MS}).fetchall():
            if c["title_sim"] >= TRIGRAM_FLOOR and c["artist_sim"] >= TRIGRAM_FLOOR:
                found.setdefault(c["recording_id"], c)
        method = "mb_trigram" if found else None
    result["candidate_count"] = len(found)
    if len(found) == 1:
        (c,) = found.values()
        isrcs = [r["isrc"] for r in conn.execute(ISRCS, (c["recording_id"],)).fetchall()]
        exact = normalize(title) == normalize(c.get("recording_name")) and artist_agrees(names, [c["recording_credit"]])
        # The spine rows this answer touched (its recording and ISRCs, release and release group, artist
        # credits and artists, and their tracked URLs), landed with it so SQL joins them before the next
        # generation carries them.
        touched = closure_rows(conn, closure(conn, [c["recording_id"]], [c["release_id"]] if c.get("release_id") else [],
                                             tracks=False), skip=("raw.mb_redirect", *EXTENSION))
        result.update(
            status="resolved", method=method, recording_id=c["recording_id"], recording_gid=c["recording_gid"],
            isrc=isrcs[0] if isrcs else None, isrc_count=len(isrcs),
            release_id=c.get("release_id"), release_gid=c.get("release_gid"),
            confidence=(1.0 if exact else 0.9) if method == "mb_release"
            else round((float(c["title_sim"]) + float(c["artist_sim"])) / 2, 4),
            closure=touched,
        )
    elif found:
        result.update(status="ambiguous", method=method)
    result["lookup_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result



# Catalog depth (mb_artist_catalog): the release groups that credit an artist, anywhere in the group's artist
# credit, counted by primary and secondary type, with the year of the earliest dated release of each kind. SQL
# decides which kinds count (dbt/seeds/artist_stage_thresholds.csv); the lookup lands facts only. Dates come
# from the CC0 core release events (release_country, release_unknown_country); release_group_meta sits in the
# supplementary dump and is never read. Every step reads a stock MusicBrainz index: artist_credit_name by
# artist, release_group by artist credit, the secondary type join by group, release by release group, and each
# release event table by release.
ARTIST_BY_GID = """
SELECT a.id, a.gid::text AS gid, a.name FROM musicbrainz.artist a WHERE a.gid = %(gid)s::uuid
UNION ALL
SELECT a.id, a.gid::text, a.name FROM musicbrainz.artist_gid_redirect x JOIN musicbrainz.artist a ON a.id = x.new_id
WHERE x.gid = %(gid)s::uuid
LIMIT 1"""
ARTIST_RELEASE_GROUPS = """
WITH credits AS MATERIALIZED (
    SELECT DISTINCT artist_credit FROM musicbrainz.artist_credit_name WHERE artist = %s
), groups AS MATERIALIZED (
    SELECT id, type FROM musicbrainz.release_group
    WHERE artist_credit = ANY(ARRAY(SELECT artist_credit FROM credits))
), releases AS MATERIALIZED (
    SELECT id, release_group FROM musicbrainz.release
    WHERE release_group = ANY(ARRAY(SELECT id FROM groups))
), dates AS (
    SELECT release, date_year FROM musicbrainz.release_country
    WHERE release = ANY(ARRAY(SELECT id FROM releases))
    UNION ALL
    SELECT release, date_year FROM musicbrainz.release_unknown_country
    WHERE release = ANY(ARRAY(SELECT id FROM releases))
), years AS (
    SELECT r.release_group, min(d.date_year) AS first_year
    FROM releases r JOIN dates d ON d.release = r.id GROUP BY r.release_group
), secondary AS (
    SELECT j.release_group, string_agg(s.name, ';' ORDER BY s.name) AS names
    FROM musicbrainz.release_group_secondary_type_join j
    JOIN musicbrainz.release_group_secondary_type s ON s.id = j.secondary_type
    WHERE j.release_group = ANY(ARRAY(SELECT id FROM groups))
    GROUP BY j.release_group
)
SELECT coalesce(p.name, '') AS primary_type, coalesce(s.names, '') AS secondary_types,
    count(*) AS release_groups, min(y.first_year) AS first_release_year
FROM groups g
LEFT JOIN musicbrainz.release_group_primary_type p ON p.id = g.type
LEFT JOIN secondary s ON s.release_group = g.id
LEFT JOIN years y ON y.release_group = g.id
GROUP BY 1, 2 ORDER BY 1, 2"""
# MusicBrainz's special purpose artists (Style/Unknown and untitled/Special purpose artist) credit thousands of
# groups and say nothing about one act's size. A bracketed name ("[crowd noise]") is the backstop for one the
# list lacks.
SPECIAL_PURPOSE_ARTISTS = frozenset({
    "89ad4ac3-39f7-470e-963a-56509c546377",  # Various Artists
    "125ec42a-7229-4250-afc5-e057484327fe",  # [unknown]
    "f731ccc4-e22a-43af-a747-64213329e088",  # [anonymous]
    "33cf029c-63b0-41a0-9855-be2a3665fb3b",  # [data]
    "314e1c25-dde7-4e4d-b2f4-0a7b9f7c56dc",  # [dialogue]
    "eec63d3c-3b81-4ad4-b1e4-7c147d4d2b61",  # [no artist]
    "9be7f096-97ec-4615-8957-8d40b5dcbc41",  # [traditional]
    "66ea0139-149f-4a0c-8fbf-5ea9ec4a6e49",  # [Disney]
    "a0ef7e1d-44ff-4039-9435-7d5fefdeecc9",  # [theatre]
    "90068d37-bae7-4292-be4a-704c145bd616",  # [church chimes]
    "80a8851f-444c-4539-892b-ad2a49292aa9",  # [language instruction]
    "7e84f845-ac16-41fe-9ff8-df12eb32af55",  # MusicBrainz Test Artist
})
BRACKETED = re.compile(r"^\[.+\]$")


def special_purpose(gid: str, name: str) -> bool:
    return gid in SPECIAL_PURPOSE_ARTISTS or bool(BRACKETED.match(name.strip()))


# One statement gives the result and its generation one snapshot and saves two network round trips.
ARTIST_CATALOG = f"""
WITH artist AS MATERIALIZED ({ARTIST_BY_GID}), generation AS ({GENERATION_SQL})
SELECT CASE WHEN a.id IS NULL THEN 'not_found'
            WHEN a.gid = ANY(%(special)s::text[]) OR trim(a.name) ~ '^\\[.+\\]$'
            THEN 'special_purpose' ELSE 'found' END AS status,
    a.id AS artist_id, a.gid AS artist_gid,
    generation.generation AS mb_generation, generation.replication_sequence AS mb_sequence,
    CASE WHEN a.id IS NULL OR a.gid = ANY(%(special)s::text[]) OR trim(a.name) ~ '^\\[.+\\]$'
         THEN '[]'::jsonb
         ELSE (SELECT coalesce(jsonb_agg(k), '[]'::jsonb)
               FROM ({ARTIST_RELEASE_GROUPS.replace('%s', 'a.id')}) k) END AS groups
FROM (SELECT 1) one LEFT JOIN artist a ON true LEFT JOIN generation ON true
"""


def artist_catalog(
    conn: psycopg.Connection, gid: str, timeout_s: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read one artist and its generation together. Redirects and negative answers keep their evidence.

    The catalog's private connection accepts a per-lookup timeout from the run's remaining budget.
    Pipeline mode sends the timeout and lookup together, without another network round trip.
    """
    with conn.pipeline():
        if timeout_s is not None:
            conn.execute("SELECT set_config('statement_timeout', %s, false)",
                         (str(max(1, int(timeout_s * 1000))),))
        result = conn.execute(ARTIST_CATALOG, {"gid": gid, "special": sorted(SPECIAL_PURPOSE_ARTISTS)})
    row = result.fetchone()
    return {key: value for key, value in row.items() if key != "groups"}, row["groups"]
