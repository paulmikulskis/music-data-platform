"""Hot 100 page shaping; every chart row is observed or rejected."""

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag
from mdp_functions.layers import Ctx, bronze
from pydantic import BaseModel, Field


class ChartEntry(BaseModel):
    chart: str
    week: date
    position: int = Field(ge=1, le=100)
    title: str
    artist: str
    last_week: int | None = None
    peak: int | None = None
    weeks_on_chart: int | None = None


def number(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def ranking_history(row: Tag, position: int) -> dict[str, int | None]:
    # The page renamed WEEKS to WEEKS ON CHART; WEEKS AT NO. 1 is a different stat.
    labels = {
        "LW": "last_week",
        "PEAK": "peak",
        "WEEKS": "weeks_on_chart",
        "WEEKS ON CHART": "weeks_on_chart",
    }
    cells: dict[str, list[str]] = {key: [] for key in labels.values()}
    for cell in row.select("[data-stat]"):
        if cell["data-stat"] in cells:
            cells[cell["data-stat"]].append(cell.get_text(strip=True))
    # Desktop and mobile repeat the labeled cells. Expanded chart details are
    # unrelated statistics and must never supply ranking history.
    for label in row.select("span.c-span"):
        key = labels.get(label.get_text(strip=True))
        if key:
            sibling = label.find_next_sibling()
            cell = sibling.select_one("span.c-label") if sibling else None
            if cell is None:
                raise ValueError(f"Chart row missing ranking history: {key}")
            cells[key].append(cell.get_text(strip=True))
    values: dict[str, int | None] = {}
    for key, texts in cells.items():
        if not texts:
            raise ValueError(f"Chart row missing ranking history: {key}")
        parsed = []
        for text in texts:
            value = number(text)
            if value is None and not (
                key == "last_week" and text in {"-", "–", "NEW", "RE-ENTRY"}
            ):
                raise ValueError(f"Chart row invalid ranking history: {key}")
            parsed.append(value)
        if len(set(parsed)) != 1:
            raise ValueError(f"Chart row conflicting ranking history: {key}")
        values[key] = parsed[0]
    if values["peak"] is None or not 1 <= values["peak"] <= position:
        raise ValueError("Chart row peak must be between 1 and current position")
    if values["weeks_on_chart"] is None or values["weeks_on_chart"] < 1:
        raise ValueError("Chart row weeks_on_chart must be at least 1")
    if values["last_week"] is not None and not 1 <= values["last_week"] <= 100:
        raise ValueError("Chart row last_week must be null or between 1 and 100")
    return values


def chart_week(soup: BeautifulSoup) -> str | None:
    # The page also has article timestamps, including invalid format placeholders.
    # Prefer the chart's own date over any generic <time> element.
    picker = soup.select_one("#chart-date-picker[data-date]")
    if picker is not None:
        try:
            return date.fromisoformat(picker["data-date"]).isoformat()
        except ValueError:
            pass
    match = re.search(
        r"Week of ([A-Za-z]+ \d{1,2}, \d{4})", soup.get_text(" ", strip=True)
    )
    if match:
        try:
            return (
                datetime.strptime(match[1], "%B %d, %Y")
                .replace(tzinfo=timezone.utc)
                .date()
                .isoformat()
            )
        except ValueError:
            pass
    time = soup.select_one("time[datetime]")
    if time is not None:
        try:
            return date.fromisoformat(time["datetime"][:10]).isoformat()
        except ValueError:
            pass
    return None


@bronze(
    # Public chart HTML GET uses no key, quota account or paid transport.
    canary=True,
    source_key="billboard_hot100",
    # Require the only chart; tolerate a few malformed positions, but not a missing share.
    min_target_coverage=1.0,
    min_row_coverage=0.9,
    knobs={"allow_partial": True},
    writes=["raw.chart_entries"],
    cadence="weekly",
    key=["chart", "week", "position"],
    schema=ChartEntry,
)
async def hot100(ctx: Ctx) -> AsyncIterator[dict[str, Any]]:
    base = "https://www.billboard.com/charts/hot-100/"
    if ctx.window:
        start = datetime.fromisoformat(ctx.window["from"]).astimezone(timezone.utc)
        end = datetime.fromisoformat(ctx.window["to"]).astimezone(timezone.utc)
        day = start.date()
        day += timedelta(days=(5 - day.weekday()) % 7)
        while datetime.combine(day, datetime.min.time(), timezone.utc) < end:
            if datetime.combine(day, datetime.min.time(), timezone.utc) >= start:
                async for row in chart_rows(ctx, base + day.isoformat() + "/"):
                    if row["week"] != day.isoformat():
                        ctx.reject(
                            row, reason="Archive returned a different chart week"
                        )
                    else:
                        yield row
            day += timedelta(days=7)
    else:
        async for row in chart_rows(ctx, base):
            yield row


def shape_chart(text: str) -> list[tuple[str, Any, str | None]]:
    """Parse one chart page into ("row", entry) or ("reject", record, reason) outcomes.

    CPU-bound on a large page, so chart_rows runs it in a worker thread.
    """
    soup = BeautifulSoup(text, "html.parser")
    week = chart_week(soup)
    rows = soup.select(".o-chart-results-list-row-container")
    if not rows:
        return [("reject", text, "No chart rows found")]
    outcomes: list[tuple[str, Any, str | None]] = []
    for index, row in enumerate(rows, 1):
        title = row.select_one("h3#title-of-a-story")
        artist = row.select_one(".c-label.a-no-trucate")
        if not week:
            outcomes.append(
                (
                    "reject",
                    str(row),
                    "Chart week missing or invalid; expected a calendar date",
                )
            )
            continue
        if title is None or artist is None:
            outcomes.append(("reject", str(row), "Chart row missing title or artist"))
            continue
        try:
            values = ranking_history(row, index)
        except ValueError as exc:
            outcomes.append(("reject", str(row), str(exc)))
            continue
        entry = {
            "chart": "hot-100",
            "week": week,
            "position": index,
            "title": title.get_text(strip=True),
            "artist": artist.get_text(strip=True),
            "last_week": values.get("last_week"),
            "peak": values.get("peak"),
            "weeks_on_chart": values.get("weeks_on_chart"),
        }
        outcomes.append(("row", entry, None))
    return outcomes


async def chart_rows(ctx: Ctx, url: str) -> AsyncIterator[dict[str, Any]]:
    response = await ctx.http.get(url)
    for kind, value, reason in await asyncio.to_thread(shape_chart, response.text):
        ctx.observed(1)
        if kind == "reject":
            ctx.reject(value, reason=reason or "rejected")
        else:
            yield value
