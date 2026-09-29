"""Recorded live-page regression coverage without network or database access."""

import gzip
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mdp_functions.layers import Ctx
from mdp_functions.sources.billboard.function import ChartEntry, hot100

FIXTURES = (
    Path(__file__).resolve().parents[1] / "src/mdp_functions/sources/billboard/fixtures"
)


async def parse(html: str) -> tuple[list[dict], Ctx]:
    ctx = Ctx(hot100.__mdp_manifest__, {"id": uuid4(), "cycle_id": uuid4()})
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ctx.http = client
        rows = [row async for row in hot100(ctx)]
    assert len(requests) == 1
    assert str(requests[0].url) == "https://www.billboard.com/charts/hot-100/"
    assert ctx.observed_count == len(rows) + len(ctx.rejected)
    return rows, ctx


async def test_recorded_live_hot100() -> None:
    html = gzip.decompress(
        (FIXTURES / "synthetic-hot100-2026-09-18.html.gz").read_bytes()
    ).decode()
    assert 'datetime="00:00-YY-DD-MM"' in html
    rows, ctx = await parse(html)
    assert len(rows) == ctx.observed_count == 100
    assert ctx.rejected == []
    assert [row["position"] for row in rows] == list(range(1, 101))
    expected = json.loads(
        (FIXTURES / "synthetic-hot100-2026-09-18-history.json").read_text()
    )
    assert [
        [row["last_week"], row["peak"], row["weeks_on_chart"]] for row in rows
    ] == expected
    assert expected[:3] == [[1, 1, 47], [2, 2, 23], [3, 1, 14]]
    for row in rows:
        assert set(row) == set(ChartEntry.model_fields)
        entry = ChartEntry.model_validate(row)
        assert entry.chart == "hot-100"
        assert entry.week == date(2026, 9, 19)
        assert entry.title and entry.artist
        assert entry.last_week is None or 1 <= entry.last_week <= 100
        assert entry.peak is not None and 1 <= entry.peak <= entry.position
        assert entry.weeks_on_chart is not None and entry.weeks_on_chart >= 1


@pytest.mark.parametrize(
    "date_markup,expected",
    [
        ('<time datetime="2026-09-12">Chart week</time>', "2026-09-12"),
        (
            (
                '<time datetime="2026-09-18">Article</time>'
                '<div id="chart-date-picker" data-date="2026-09-19"></div>'
            ),
            "2026-09-19",
        ),
        (
            (
                '<time datetime="00:00-YY-DD-MM">Article</time>'
                "<p>Week of September 19, 2026</p>"
            ),
            "2026-09-19",
        ),
        (
            (
                '<time datetime="2026-09-18">Article</time>'
                "<p>Week of September 19, 2026</p>"
            ),
            "2026-09-19",
        ),
        ('<time datetime="00:00-YY-DD-MM">Article</time>', None),
        ('<div id="chart-date-picker" data-date="2026-02-30"></div>', None),
        ("<p>Week of February 30, 2026</p>", None),
        ("", None),
    ],
)
async def test_chart_week_validation(date_markup: str, expected: str | None) -> None:
    html = json.loads((FIXTURES / "normal.jsonl").read_text())["response"]["body"]
    html = html.replace('<time datetime="2026-09-12">Chart week</time>', date_markup)
    rows, ctx = await parse(html)
    assert ctx.observed_count == 3
    if expected is None:
        assert rows == []
        assert len(ctx.rejected) == 3
        assert {reject["reason"] for reject in ctx.rejected} == {
            "Chart week missing or invalid; expected a calendar date"
        }
    else:
        assert len(rows) == 3
        assert ctx.rejected == []
        assert {ChartEntry.model_validate(row).week for row in rows} == {
            date.fromisoformat(expected)
        }


def history_page(values: dict[str, str], labeled: bool) -> str:
    labels = {"last_week": "LW", "peak": "PEAK", "weeks_on_chart": "WEEKS"}
    # Reorder history and add distracting expanded statistics deliberately.
    cells = "".join(
        (
            f'<span class="c-span">{labels[key]}</span>'
            f'<li><span class="c-label">{value}</span></li>'
            if labeled
            else f'<span data-stat="{key}">{value}</span>'
        )
        for key, value in reversed(list(values.items()))
    )
    return (
        '<time datetime="2026-09-19"></time>'
        '<div class="o-chart-results-list-row-container">'
        '<h3 id="title-of-a-story">Track</h3>'
        '<span class="c-label a-no-trucate">Artist</span>'
        f'{cells}<span class="c-label">39</span><span class="c-label">1</span>'
        "</div>"
    )


@pytest.mark.parametrize("labeled", [False, True])
@pytest.mark.parametrize("last_week", ["-", "–", "NEW", "RE-ENTRY", "1", "100"])
async def test_history_uses_labels(labeled: bool, last_week: str) -> None:
    rows, ctx = await parse(
        history_page(
            {"last_week": last_week, "peak": "1", "weeks_on_chart": "47"}, labeled
        )
    )
    assert not ctx.rejected
    assert rows[0]["last_week"] == (int(last_week) if last_week.isdigit() else None)
    assert rows[0]["peak"] == 1
    assert rows[0]["weeks_on_chart"] == 47


@pytest.mark.parametrize("labeled", [False, True])
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("peak", "2", "peak must be between"),
        ("peak", "0", "peak must be between"),
        ("peak", "-", "invalid ranking history: peak"),
        ("weeks_on_chart", "0", "weeks_on_chart must be at least 1"),
        ("weeks_on_chart", "-1", "invalid ranking history: weeks_on_chart"),
        ("last_week", "0", "last_week must be null or between"),
        ("last_week", "101", "last_week must be null or between"),
        ("last_week", "unknown", "invalid ranking history: last_week"),
        ("last_week", "", "invalid ranking history: last_week"),
        ("last_week", None, "missing ranking history: last_week"),
        ("peak", None, "missing ranking history: peak"),
        ("weeks_on_chart", None, "missing ranking history: weeks_on_chart"),
    ],
)
async def test_invalid_history_is_rejected(
    labeled: bool, field: str, value: str | None, reason: str
) -> None:
    values = {"last_week": "1", "peak": "1", "weeks_on_chart": "47"}
    if value is None:
        del values[field]
    else:
        values[field] = value
    rows, ctx = await parse(history_page(values, labeled))
    assert rows == []
    assert ctx.observed_count == len(ctx.rejected) == 1
    assert reason in ctx.rejected[0]["reason"]


async def test_conflicting_mobile_history_is_rejected() -> None:
    html = history_page({"last_week": "1", "peak": "1", "weeks_on_chart": "47"}, True)
    html = html.replace(
        "</div>",
        '<span class="c-span">WEEKS</span><li><span class="c-label">48</span></li></div>',
    )
    rows, ctx = await parse(html)
    assert rows == []
    assert (
        ctx.rejected[0]["reason"]
        == "Chart row conflicting ranking history: weeks_on_chart"
    )


async def test_backfill_fetches_only_archive_weeks_in_window() -> None:
    ctx = Ctx(hot100.__mdp_manifest__, {"id": uuid4(), "cycle_id": uuid4()})
    ctx.window = {
        "from": "2026-09-12T00:00:00+00:00",
        "to": "2026-09-19T00:00:00+00:00",
    }
    urls = []
    html = gzip.decompress(
        (FIXTURES / "synthetic-hot100-2026-09-18.html.gz").read_bytes()
    ).decode()

    def respond(request):
        urls.append(str(request.url))
        return httpx.Response(
            200,
            text=html.replace("2026-09-19", "2026-09-12").replace(
                "September 19, 2026", "September 12, 2026"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ctx.http = client
        rows = [row async for row in hot100(ctx)]
    assert urls == ["https://www.billboard.com/charts/hot-100/2026-09-12/"]
    assert len(rows) == 100
    assert {r["week"] for r in rows} == {"2026-09-12"}


def test_parses_weeks_on_chart_label_rename() -> None:
    import gzip
    from pathlib import Path

    from bs4 import BeautifulSoup
    from mdp_functions.sources.billboard.function import ranking_history

    html = gzip.decompress(
        (
            Path(__file__).parents[1]
            / "src/mdp_functions/sources/billboard/fixtures/synthetic-hot100-2026-09-23.html.gz"
        ).read_bytes()
    ).decode()
    rows = BeautifulSoup(html, "html.parser").select(
        "div.o-chart-results-list-row-container"
    )
    assert len(rows) == 100
    first = ranking_history(rows[0], 1)
    assert first["weeks_on_chart"] == 47 and first["peak"] == 1
