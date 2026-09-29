"""Shazam charts: per chart, its CSV for the chart date, rank and printed credit, then its
page for each row's Apple song id and its one artist link. Song pages and the internal `/shazam/v1/` and
`/shazam/v3/` APIs back nothing (the first answers with a challenge, the others are disallowed).

The page carries every row as rendered HTML (`data-test-id="songItem"`). The first rows stream in later:
their artist link sits in a `<template id="P:n">` whose content is the hidden `div#S:n` further down.
ISRCs appear only on the video-highlight songs, in the page's flight data (`videoList`). The CSV prints
the credit as the page shows it; it is landed as text and never matched on.
"""

import csv
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

from mdp_functions.layers import Ctx, Target
from mdp_functions.playlist import EnvelopeError, envelope_miss, off_loop

HOSTS = ["www.shazam.com"]
SURFACES = ("sz_chart_csv", "sz_chart_page")
SONG = re.compile(r"/song/(\d+)(?:/|$)")
ARTIST = re.compile(r"/artist/[^/]+/(\d+)/?$")
ISRC = re.compile(r"[A-Z]{2}[A-Z0-9]{3}\d{7}")
FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


class ShazamChartEntry(BaseModel):
    """One chart row on its chart date. `artist_text` and `title_text` are the CSV's; the ids are the page's."""

    chart: str
    chart_date: date
    position: int = Field(ge=1, le=200)
    apple_song_id: str
    apple_primary_artist_id: str | None = None
    artist_text: str | None = None
    title_text: str | None = None
    isrc: str | None = None
    observed_at: datetime


def chart_key(target: Target) -> str:
    """`shazam:<type>:<country>[:<city>]` from the frozen spec."""
    params = target.get("params_json") or {}
    parts = [params.get("chart_type"), params.get("country"), params.get("city")]
    return ":".join(["shazam", *[str(p) for p in parts if p]])


def folded(value: str | None) -> str:
    """Titles compared across the CSV and the page: the CSV leaves inner quotes unescaped."""
    text = unicodedata.normalize("NFKC", value or "").replace('"', "")
    return " ".join(text.casefold().split())


def parse_csv(text: str) -> tuple[date, dict[int, dict[str, Any]], list[str]]:
    """The chart date (the CSV's first line), rows by rank, and the lines that do not parse."""
    lines = text.lstrip("\ufeff").splitlines()
    try:
        header = next(i for i, line in enumerate(lines) if line.strip() == "Rank,Artist,Title")
    except StopIteration:
        raise EnvelopeError("header") from None
    stamp = next((line for line in reversed(lines[:header]) if line.strip()), "")
    stamp = stamp.strip().strip('"').split("[", 1)[0].strip()
    try:
        day = datetime.strptime(stamp, "%A, %d %B %Y").replace(tzinfo=timezone.utc).date()
    except ValueError:
        raise EnvelopeError("chart_date") from None
    rows: dict[int, dict[str, Any]] = {}
    unparsed = []
    for line in lines[header + 1 :]:
        if not line.strip():
            continue
        fields = next(csv.reader([line]))
        rank = fields[0].strip()
        if len(fields) != 3 or not rank.isdigit() or int(rank) in rows:
            unparsed.append(line)
            continue
        rows[int(rank)] = {"artist_text": fields[1].strip(), "title_text": fields[2].strip()}
    return day, rows, unparsed


def flight_isrcs(html: str) -> dict[str, str]:
    """Apple song id to ISRC for the songs the page's video highlights carry."""
    flight = "".join(json.loads(chunk) for chunk in FLIGHT.findall(html))
    decoder = json.JSONDecoder()
    found = {}
    for match in re.finditer(r'"videoList":', flight):
        try:
            songs, _ = decoder.raw_decode(flight, match.end())
        except ValueError:
            continue
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict) or song.get("type") != "songs":
                continue
            isrc = (song.get("attributes") or {}).get("isrc")
            if isinstance(isrc, str) and ISRC.fullmatch(isrc):
                found[str(song.get("id"))] = isrc
    return found


def artist_link(soup: BeautifulSoup, card: Tag) -> Tag | None:
    selector = 'a[data-test-id="charts_userevent_list_artistName"]'
    link = card.select_one(selector)
    for template in [] if link else card.find_all("template"):
        streamed = soup.find(id="S:" + str(template.get("id", "")).removeprefix("P:"))
        link = streamed.select_one(selector) if isinstance(streamed, Tag) else None
        if link:
            break
    return link


def parse_page(html: str) -> tuple[list[dict[str, Any]], list[int], dict[str, str]]:
    """Rows in page order (printed rank, song id, primary artist id, title), the ranks whose row lacks
    its song link, and the ISRCs the page carries."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-test-id="songItem"]')
    if not cards:
        raise EnvelopeError("songItem")
    rows, missing = [], []
    for index, card in enumerate(cards, 1):
        printed = next(
            (s.get_text(strip=True) for s in card.find_all("span")
             if any(c.startswith("SongItem_rankingNumber") for c in s.get("class", []))),
            "",
        )
        rank = int(printed) if printed.isdigit() else index
        song = card.select_one('a[data-test-id="charts_userevent_list_songTitle"][href]')
        found = SONG.match(str(song["href"])) if song else None
        if not found:
            missing.append(rank)
            continue
        artist = artist_link(soup, card)
        artist_id = ARTIST.search(str(artist.get("href", ""))) if artist else None
        rows.append({
            "position": rank,
            "apple_song_id": found[1],
            "apple_primary_artist_id": artist_id[1] if artist_id else None,
            "title": song.get("aria-label") or song.get_text(strip=True),
        })
    return rows, missing, flight_isrcs(html)


async def collect_chart(ctx: Ctx, target: Target):
    """One chart: the CSV, then the page. A page row lands only when the CSV row of its rank names the
    same title, so CSV rank equals page rank on every landed row."""
    params = target.get("params_json") or {}
    chart = chart_key(target)
    observed_at = datetime.now(timezone.utc)
    if not params.get("csv_url") or not params.get("page_url"):
        ctx.observed(1)
        ctx.reject({"chart": chart}, reason="chart_spec_incomplete")
        return
    sheet = await ctx.http.get(params["csv_url"])
    try:
        chart_date, ranked, unparsed = await off_loop(parse_csv, sheet.text)
    except EnvelopeError as exc:
        envelope_miss(ctx, SURFACES[0], str(exc))
        ctx.observed(1)
        ctx.reject({"chart": chart}, reason=f"envelope_mismatch:{SURFACES[0]}:{exc}")
        return
    page = await ctx.http.get(params["page_url"])
    try:
        rows, missing, isrcs = await off_loop(parse_page, page.text)
    except EnvelopeError as exc:
        envelope_miss(ctx, SURFACES[1], str(exc))
        ctx.observed(1)
        ctx.reject({"chart": chart}, reason=f"envelope_mismatch:{SURFACES[1]}:{exc}")
        return
    if len(missing) > len(rows):
        envelope_miss(ctx, SURFACES[1], "songTitle")
    seen = {row["position"] for row in rows} | set(missing)
    ctx.observed(len(rows) + len(missing) + len(unparsed) + len(set(ranked) - seen))
    for rank in missing:
        ctx.reject({"chart": chart, "position": rank}, reason=f"envelope_mismatch:{SURFACES[1]}:songTitle")
    for _ in unparsed:
        ctx.reject({"chart": chart}, reason="csv_row_unparsed")
    for rank in sorted(set(ranked) - seen):
        ctx.reject({"chart": chart, "position": rank}, reason="page_rank_missing")
    for row in rows:
        printed = ranked.get(row["position"])
        identity = {"chart": chart, "position": row["position"], "apple_song_id": row["apple_song_id"]}
        if printed is None:
            ctx.reject(identity, reason="csv_rank_missing")
        elif folded(printed["title_text"]) != folded(row["title"]):
            ctx.reject(identity, reason="csv_page_mismatch")
        else:
            yield {
                **identity,
                "chart_date": chart_date,
                "apple_primary_artist_id": row["apple_primary_artist_id"],
                "artist_text": printed["artist_text"],
                "title_text": printed["title_text"],
                "isrc": isrcs.get(row["apple_song_id"]),
                "observed_at": observed_at,
            }


async def collect(ctx: Ctx, batch: list[Target]):
    for target in batch:
        async for row in collect_chart(ctx, target):
            yield row
