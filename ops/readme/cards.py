# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.40", "uharfbuzz>=0.39"]
# ///
"""Draws the README hero, banner and outcome cards as SVG under docs/images.

    uv run ops/readme/cards.py [--fonts control/apps/showcase/public/fonts]

GitHub shows SVGs as images and loads no web fonts, so every word is drawn as
glyph outlines from the bundled open font families. Edit CARDS (copy, status)
and rerun; the SVGs are generated files.
"""

from __future__ import annotations

import argparse
import math
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images"

# Neutral palette.
BG, BG_RAISED, BG_SUNKEN = "#142026", "#23343d", "#0e171c"
FG, FG_MUTED, MUTED, SURFACE = "#edf4f6", "#b4c8d0", "#8da7b2", "#1b2a32"
HAIR = "rgba(237,244,246,0.12)"
FAINT = "rgba(237,244,246,0.28)"
STATUS = {  # status -> accent
    "live": "#e8b16d",
    "built, off": "#68b9c2",
    "landing": "#68b9c2",
    "test data": MUTED,
    "next": "#a9c885",
}

CARDS = [
    {"slug": "request-lineage", "title": "request lineage", "line": "Records retain the request, run and input batch that produced them.", "status": "test data", "draw": "chain"},
    {"slug": "usage-metadata", "title": "usage metadata", "line": "Served rows include source keys and learning and resale permission flags.", "status": "test data", "draw": "rows"},
    {"slug": "playlist-observations", "title": "playlist observations", "line": "Dated observations describe list membership, additions and removals.", "status": "test data", "draw": "lanes"},
    {"slug": "recording-identifiers", "title": "recording identifiers", "line": "ISRCs and MusicBrainz relationships connect platform recording identifiers.", "status": "test data", "draw": "converge"},
]
HERO = {
    "label": "[  MUSIC DATA PLATFORM  ]",
    "headline": "public music data,\nwith run history.",
    "line": "Charts, catalogs and platform playlists.\nPython collection, SQL transforms and typed reads.",
    "sources": [("BILLBOARD", "test data"), ("SPOTIFY", "test data"), ("APPLE MUSIC", "test data"),
                ("SOUNDCLOUD", "test data"), ("BANDCAMP", "built, off"), ("MUSICBRAINZ", "built, off"),
                ("WIKIMEDIA", "test data")],
}
# status -> (stroke, opacity, dash) for the hero's source lines
LINES = {
    "live": (STATUS["live"], 1.0, ""),
    "landing": (STATUS["landing"], 0.85, "7 5"),
    "built, off": (STATUS["built, off"], 0.85, "7 5"),
    "test data": (STATUS["test data"], 0.7, "2 4"),
    "next": (STATUS["next"], 0.75, "1 7"),
}
# The status row under the hero: (label, status). Each becomes one pill image.
PILLS = [("Billboard", "test data"), ("request history", "test data"), ("playlists", "test data"), ("ISRC matching", "test data")]


@dataclass
class Face:
    tt: TTFont
    hb_font: hb.Font
    wght: float | None

    @classmethod
    def load(cls, path: Path, wght: float | None = None) -> Face:
        tt = TTFont(path)
        font = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))
        if wght is not None:
            font.set_variations({"wght": wght})
        return cls(tt, font, wght)

    @property
    def upem(self) -> int:
        return self.tt["head"].unitsPerEm

    def shape(self, text: str) -> tuple[list[str], list[tuple[float, float]]]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {"kern": True, "liga": True, "lnum": True})
        order = self.tt.getGlyphOrder()
        names = [order[i.codepoint] for i in buf.glyph_infos]
        return names, [(p.x_advance, p.x_offset) for p in buf.glyph_positions]

    def width(self, text: str, size: float, track: float = 0.0) -> float:
        _, pos = self.shape(text)
        return sum(a for a, _ in pos) * size / self.upem + track * size * max(len(pos) - 1, 0)

    def path(self, text: str, size: float, x: float, y: float, track: float = 0.0) -> str:
        location = {"wght": self.wght} if self.wght is not None else None
        glyphs = self.tt.getGlyphSet(location=location)
        pen = SVGPathPen(glyphs)
        scale = size / self.upem
        cursor = 0.0
        names, pos = self.shape(text)
        for name, (advance, offset) in zip(names, pos):
            gx = x + (cursor + offset) * scale
            glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, gx, y)))
            cursor += advance + track * self.upem
        return pen.getCommands()


class Type:
    def __init__(self, fonts: Path):
        self.serif = Face.load(fonts / "IBMPlexSans.ttf", 500)
        self.serif_light = Face.load(fonts / "IBMPlexSans.ttf", 400)
        self.sans = Face.load(fonts / "IBMPlexSans.ttf", 380)
        self.mono = Face.load(fonts / "GeistMono-Variable.ttf", 420)


def text(face: Face, s: str, size: float, x: float, y: float, fill: str,
         anchor: str = "start", track: float = 0.0, opacity: float = 1.0) -> str:
    if anchor != "start":
        w = face.width(s, size, track)
        x -= w if anchor == "end" else w / 2
    op = f' fill-opacity="{opacity}"' if opacity < 1 else ""
    return f'<path fill="{fill}"{op} d="{rnd(face.path(s, size, x, y, track))}"/>'


def wrap(face: Face, s: str, size: float, width: float) -> list[str]:
    lines = []
    for para in s.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if line and face.width(trial, size) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    # No one-word last line: pull a word down from the line above.
    if len(lines) > 1 and " " not in lines[-1] and " " in lines[-2]:
        head, moved = lines[-2].rsplit(" ", 1)
        lines[-2:] = [head, f"{moved} {lines[-1]}"]
    return lines


def one_line(s: str) -> str:
    return " ".join(s.split())


def rnd(d: str) -> str:
    return re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip("."), d)


def svg(w: int, h: int, body: str, label: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{label}"><title>{label}</title>{body}</svg>\n'
    )


def ground(w: int, h: int, gid: str, cx: str = "22%", cy: str = "18%") -> str:
    return (
        f'<defs><radialGradient id="{gid}" cx="{cx}" cy="{cy}" r="95%">'
        f'<stop offset="0" stop-color="{BG_RAISED}"/><stop offset="0.55" stop-color="{BG}"/>'
        f'<stop offset="1" stop-color="{BG_SUNKEN}"/></radialGradient></defs>'
        f'<rect width="{w}" height="{h}" rx="4" fill="url(#{gid})"/>'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="4" fill="none" stroke="{HAIR}"/>'
    )


def monogram(x: float, y: float, size: float, center: str = BG) -> str:
    return f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="4" fill="{STATUS["live"]}"/>'


def wordmark(x: float, y: float, height: float) -> str:
    return f'<text x="{x}" y="{y + height}" fill="{FG}" font-family="monospace" font-size="{height}">MDP</text>'


# ---- card drawings: hairline pictures in the card's accent, inside box (x, y, w, h) ----

def draw_lanes(t: Type, a: str, x: float, y: float, w: float, h: float) -> str:
    out, lanes = [], 5
    snaps = [x + 20 + i * (w - 150) / 7 for i in range(8)]
    for s in snaps:
        out.append(f'<line x1="{s:.1f}" y1="{y - 6}" x2="{s:.1f}" y2="{y + h - 18}" stroke="{FAINT}" stroke-dasharray="2 5"/>')
    gap = (h - 30) / (lanes - 1)
    spans = [(0, 7, None), (3, 7, "add"), (0, 4, "remove"), (0, 7, "move"), (5, 7, None)]
    for i, (s0, s1, tag) in enumerate(spans):
        ly = y + 6 + i * gap
        x0, x1 = snaps[s0], snaps[s1]
        if tag == "move":
            mid = snaps[4]
            out.append(f'<path d="M{x0:.1f} {ly + gap * 0.45:.1f}H{mid - 8:.1f}L{mid + 8:.1f} {ly:.1f}H{x1:.1f}" '
                       f'fill="none" stroke="{a}" stroke-width="2"/>')
        else:
            stroke = a if tag else FAINT
            out.append(f'<line x1="{x0:.1f}" y1="{ly:.1f}" x2="{x1:.1f}" y2="{ly:.1f}" stroke="{stroke}" stroke-width="2"/>')
        if tag == "add":
            out.append(f'<circle cx="{x0:.1f}" cy="{ly:.1f}" r="5" fill="{a}"/>')
        if tag == "remove":
            out.append(f'<circle cx="{x1:.1f}" cy="{ly:.1f}" r="5" fill="{BG}" stroke="{a}" stroke-width="2"/>')
        if tag:
            out.append(text(t.mono, tag.upper(), 13, x + w, ly + 4.5, a, "end", 0.18))
    out.append(text(t.mono, "SNAPSHOTS", 11, x + 20, y + h + 2, MUTED, track=0.25))
    return "".join(out)


def draw_converge(t: Type, a: str, x: float, y: float, w: float, h: float) -> str:
    out, names = [], ["SPOTIFY", "APPLE MUSIC", "SOUNDCLOUD", "BANDCAMP"]
    nx, ny = x + w * 0.66, y + h / 2
    for i, n in enumerate(names):
        ly = y + 8 + i * (h - 16) / 3
        lx = x + 150
        out.append(text(t.mono, n, 12, x, ly + 4, MUTED, track=0.18))
        out.append(f'<circle cx="{lx}" cy="{ly:.1f}" r="3.5" fill="{FG}" fill-opacity="0.7"/>')
        out.append(f'<path d="M{lx + 4} {ly:.1f}C{lx + 90} {ly:.1f} {nx - 90:.1f} {ny:.1f} {nx - 22:.1f} {ny:.1f}" '
                   f'fill="none" stroke="{FAINT}" stroke-width="1.5"/>')
    out.append(f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="20" fill="none" stroke="{a}" stroke-width="2"/>')
    out.append(f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="7" fill="{a}"/>')
    out.append(f'<line x1="{nx + 22:.1f}" y1="{ny:.1f}" x2="{nx + 52:.1f}" y2="{ny:.1f}" stroke="{a}" stroke-width="2"/>')
    out.append(text(t.mono, "ISRC", 13, nx + 60, ny - 3, a, track=0.2))
    out.append(text(t.mono, "RECORDING", 11, nx + 60, ny + 15, MUTED, track=0.2))
    return "".join(out)


def draw_waves(t: Type, a: str, x: float, y: float, w: float, h: float) -> str:
    rng = random.Random(7)
    out, bars, bw = [], 56, (w - 8) / 56
    hit = range(30, 40)
    rows = [(y + h * 0.22, "REFERENCE", range(10, 20)), (y + h * 0.80, "MATCH", hit)]
    for cy, label, win in rows:
        for i in range(bars):
            amp = (0.25 + 0.75 * abs(math.sin(i * 0.37 + rng.random()))) * h * 0.17
            col, op = (a, 1.0) if i in win else (FG, 0.3)
            out.append(f'<rect x="{x + i * bw:.1f}" y="{cy - amp:.1f}" width="{bw * 0.55:.1f}" '
                       f'height="{2 * amp:.1f}" rx="1" fill="{col}" fill-opacity="{op}"/>')
        out.append(text(t.mono, label, 11, x + w, cy - h * 0.2 - 4, MUTED, "end", 0.25))
    (ty, _, tw), (by, _, bwin) = rows
    for j in (0, 9):
        x0, x1 = x + (tw[j] + 0.3) * bw, x + (bwin[j] + 0.3) * bw
        out.append(f'<path d="M{x0:.1f} {ty + h * 0.18:.1f}C{x0:.1f} {ty + h * 0.45:.1f} {x1:.1f} {by - h * 0.45:.1f} '
                   f'{x1:.1f} {by - h * 0.18:.1f}" fill="none" stroke="{a}" stroke-width="1.5" stroke-dasharray="3 4"/>')
    return "".join(out)


def draw_chain(t: Type, a: str, x: float, y: float, w: float, h: float) -> str:
    out, cy = [], y + h * 0.45
    steps = ["REQUEST", "RAW ROW", "MODEL", "MART"]
    gap = (w - 200) / (len(steps) - 1)
    end = x + w - 120
    out.append(f'<line x1="{x + 8}" y1="{cy:.1f}" x2="{end:.1f}" y2="{cy:.1f}" stroke="{a}" stroke-width="2"/>')
    for i, s in enumerate(steps):
        sx = x + 8 + i * gap
        out.append(f'<rect x="{sx - 7:.1f}" y="{cy - 7:.1f}" width="14" height="14" fill="{BG}" stroke="{a}" stroke-width="2"/>')
        out.append(text(t.mono, s, 12, sx - 7, cy + 36, MUTED, track=0.18))
    out.append(text(t.serif, "100", 64, x + w, cy + 22, a, "end"))
    out.append(text(t.mono, "ROWS LANDED", 12, x + w, cy + 36, a, "end", 0.18))
    out.append(f'<path d="M{end - 10:.1f} {cy - 22:.1f}C{end - 120:.1f} {y - 6:.1f} {x + 130:.1f} {y - 6:.1f} '
               f'{x + 14:.1f} {cy - 16:.1f}" fill="none" stroke="{FAINT}" stroke-width="1.5" stroke-dasharray="3 5"/>')
    out.append(f'<path d="M{x + 8:.1f} {cy - 22:.1f}L{x + 14:.1f} {cy - 15:.1f}L{x + 22:.1f} {cy - 20:.1f}" '
               f'fill="none" stroke="{FAINT}" stroke-width="1.5"/>')
    return "".join(out)


def draw_rows(t: Type, a: str, x: float, y: float, w: float, h: float) -> str:
    out, tags = [], ["SERVE", "SERVE · LEARN", "SERVE", "SERVE · LEARN"]
    rh = h / len(tags)
    for i, tag in enumerate(tags):
        ry, bh = y + i * rh, rh - 6
        out.append(f'<rect x="{x}" y="{ry:.1f}" width="{w}" height="{bh:.1f}" fill="{FG}" fill-opacity="0.035" '
                   f'stroke="{HAIR}"/>')
        cx = x + 16
        for cw in (90, 150, 60):
            out.append(f'<rect x="{cx}" y="{ry + bh / 2 - 3:.1f}" width="{cw}" height="6" rx="3" '
                       f'fill="{FG}" fill-opacity="0.18"/>')
            cx += cw + 18
        learn = "LEARN" in tag
        col = a if learn else MUTED
        tw = t.mono.width(tag, 11, 0.16)
        tx = x + w - 16 - tw
        out.append(f'<rect x="{tx - 9:.1f}" y="{ry + 3.5:.1f}" width="{tw + 18:.1f}" height="{bh - 7:.1f}" rx="2" '
                   f'fill="none" stroke="{col}" stroke-opacity="{1 if learn else 0.6}"/>')
        out.append(text(t.mono, tag, 11, tx, ry + bh / 2 + 4, col, track=0.16))
    return "".join(out)


def draw_drift(t: Type, a: str, x: float, y: float, w: float, h: float) -> str:
    rng = random.Random(11)
    out = []
    left = [(x + 70 + rng.gauss(0, 34), y + h / 2 + rng.gauss(0, 24)) for _ in range(26)]
    right = [(x + w - 110 + rng.gauss(0, 26), y + h / 2 - 8 + rng.gauss(0, 18)) for _ in range(12)]
    for px, py in left:
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{FG}" fill-opacity="0.4"/>')
    for px, py in right:
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{a}"/>')
    for (lx, ly), (rx, ry) in zip(sorted(left)[-5:], right[:5]):
        mx = (lx + rx) / 2
        out.append(f'<path d="M{lx + 5:.1f} {ly:.1f}C{mx:.1f} {y - 10:.1f} {mx:.1f} {y - 10:.1f} {rx - 6:.1f} {ry:.1f}" '
                   f'fill="none" stroke="{a}" stroke-opacity="0.7" stroke-width="1.5" stroke-dasharray="3 5"/>')
    out.append(text(t.mono, "NOW", 11, x + 70, y + h + 2, MUTED, "middle", 0.25))
    out.append(text(t.mono, "MOVING TO", 11, x + w - 110, y + h + 2, a, "middle", 0.25))
    return "".join(out)


DRAW = {
    "lanes": draw_lanes, "converge": draw_converge, "waves": draw_waves,
    "chain": draw_chain, "rows": draw_rows, "drift": draw_drift,
}


def card(t: Type, i: int, c: dict) -> str:
    w, h, pad = 640, 400, 40
    a = STATUS[c["status"]]
    body = [ground(w, h, f"g{i}")]
    body.append(text(t.mono, f"[{i:02d}]", 17, pad, 58, MUTED, track=0.3))
    label = c["status"].upper()
    lw = t.mono.width(label, 17, 0.3)
    body.append(f'<circle cx="{w - pad - lw - 16:.1f}" cy="52" r="6" fill="{a}"/>')
    body.append(text(t.mono, label, 17, w - pad, 58, a, "end", 0.3))
    body.append(DRAW[c["draw"]](t, a, pad, 100, w - 2 * pad, 118))
    size = min(54.0, 46 * (w - 2 * pad) / t.serif.width(c["title"], 46))
    body.append(text(t.serif, c["title"], size, pad, 292, FG))
    lines = wrap(t.sans, c["line"], 22, w - 2 * pad)
    if len(lines) > 2:
        raise SystemExit(f'card {c["slug"]}: line needs {len(lines)} lines; shorten it to fit two')
    for j, line in enumerate(lines):
        body.append(text(t.sans, line, 22, pad, 333 + j * 30, FG_MUTED))
    return svg(w, h, "".join(body), f'{c["title"]} ({c["status"]}). {c["line"]}')


def hero(t: Type, line: str = HERO["line"], out: tuple[str, str, str] = ("ONE RECORD", "SOURCE · RIGHTS · RUN", "test data"),
         gid: str = "hero") -> str:
    """The fan of sources into one record; `out` names where the record goes and that link's status."""
    w, h, x = 1280, 520, 88
    body = [ground(w, h, gid, "28%", "30%")]
    body.append(monogram(x - 6, 78, 58))
    body.append(wordmark(x + 70, 92, 30))
    body.append(text(t.mono, HERO["label"], 15, x, 186, MUTED, track=0.35))
    y = 262
    for head in wrap(t.serif_light, HERO["headline"], 58, 600):
        body.append(text(t.serif_light, head, 58, x, y, FG))
        y += 64
    y += 18
    for row in wrap(t.sans, line, 22, 560):
        body.append(text(t.sans, row, 22, x, y, FG_MUTED))
        y += 32
    # Right: every source converges through the aperture into one record.
    teal, sx, cx, cy, end = STATUS["live"], 775, 995, 262, w - 56
    srcs = HERO["sources"]
    for i, (s, status) in enumerate(srcs):
        sy = 118 + i * (288 / (len(srcs) - 1))
        stroke, op, dash = LINES[status]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        body.append(text(t.mono, s, 13, sx, sy + 4.5, teal if status == "live" else FG_MUTED, "end", 0.22))
        body.append(f'<circle cx="{sx + 14}" cy="{sy:.1f}" r="3.5" fill="{stroke}"/>')
        body.append(f'<path d="M{sx + 18} {sy:.1f}C{sx + 120} {sy:.1f} {cx - 130} {cy:.1f} {cx - 44} {cy:.1f}" '
                    f'fill="none" stroke="{stroke}" stroke-opacity="{op}" stroke-width="1.5"{dash_attr}/>')
    lx = sx - 150
    used = {st for _, st in srcs} | {out[2]}
    for status in [st for st in LINES if st in used]:
        stroke, op, dash = LINES[status]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        body.append(f'<line x1="{lx}" y1="{h - 52}" x2="{lx + 28}" y2="{h - 52}" stroke="{stroke}" '
                    f'stroke-opacity="{op}" stroke-width="2"{dash_attr}/>')
        label = status.upper()
        body.append(text(t.mono, label, 11, lx + 38, h - 48, stroke, track=0.2))
        lx += 38 + t.mono.width(label, 11, 0.2) + 34
    body.append(f'<g transform="rotate(45 {cx} {cy})"><rect x="{cx - 40}" y="{cy - 40}" width="80" height="80" rx="18" '
                f'fill="none" stroke="{FG}" stroke-opacity="0.85" stroke-width="1.5"/></g>')
    body.append(f'<ellipse cx="{cx}" cy="{cy}" rx="19" ry="11" fill="{teal}"/>')
    label, sub, status = out
    stroke, op, dash = LINES[status]
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    body.append(f'<line x1="{cx + 58}" y1="{cy}" x2="{end - 4}" y2="{cy}" stroke="{stroke}" stroke-opacity="{op}" '
                f'stroke-width="2"{dash_attr}/>')
    body.append(f'<circle cx="{end}" cy="{cy}" r="4" fill="{stroke}"/>')
    body.append(text(t.mono, label, 13, end, cy - 16, stroke, "end", 0.22))
    body.append(text(t.mono, sub, 11, end, cy + 30, MUTED, "end", 0.2))
    return svg(w, h, "".join(body), f'Music Data Platform. {one_line(HERO["headline"])} {one_line(line)}')


def banner(t: Type) -> str:
    w, h = 1280, 240
    body = [ground(w, h, "banner", "30%", "20%")]
    body.append(monogram(84, 70, 52))
    body.append(wordmark(150, 83, 27))
    body.append(text(t.mono, HERO["label"], 14, 90, 168, MUTED, track=0.35))
    body.append(text(t.serif_light, one_line(HERO["headline"]), 44, w - 88, 140, FG, "end"))
    return svg(w, h, "".join(body), f'Music Data Platform. {one_line(HERO["headline"])}')


def pill(t: Type, status: str, label: str | None = None) -> str:
    size, pad, gap, h = 11, 10, 8, 26
    a = STATUS[status]
    name = status.upper()
    lw = t.mono.width(label.upper(), size, 0.14) + gap if label else 0
    sw = t.mono.width(name, size, 0.14)
    w = round(pad + lw + 15 + sw + pad)
    body = [f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="2" fill="{SURFACE}" stroke="{HAIR}"/>']
    if label:
        body.append(text(t.mono, label.upper(), size, pad, 17, FG_MUTED, track=0.14))
    body.append(f'<circle cx="{pad + lw + 4}" cy="{h / 2}" r="3.5" fill="{a}"/>')
    body.append(text(t.mono, name, size, pad + lw + 15, 17, a, track=0.14))
    title = f"{label}: {status}" if label else status
    return svg(w, h, "".join(body), title)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts", type=Path, default=ROOT / "control/apps/showcase/public/fonts")
    args = parser.parse_args()
    t = Type(args.fonts)
    (OUT / "cards").mkdir(parents=True, exist_ok=True)
    # The app marks use the same palette and outlined type as the documentation.
    public = ROOT / "control/apps/showcase/public"
    mark = svg(360, 100, f'<rect width="360" height="100" rx="8" fill="{BG}"/>'
               + text(t.sans, "MDP", 58, 180, 70, FG, "middle"), "Music Data Platform")
    for name in ("brand/mdp-monogram.svg", "brand/mdp-wordmark.svg", "brand/third-party/mdp.svg", "mdp-wordmark.svg"):
        (public / name).write_text(mark)
    for name in ("mdp-monogram-rect5283.png", "mdp-wordmark-custom.png"):
        subprocess.run(["rsvg-convert", str(public / "brand/mdp-wordmark.svg"),
                        "-o", str(public / "brand" / name)], check=True)
    (OUT / "hero.svg").write_text(hero(t))
    (OUT / "banner.svg").write_text(banner(t))
    for stale in (OUT / "cards").glob("*.svg"):
        stale.unlink()
    for stale in (OUT / "status").glob("*.svg"):
        stale.unlink()
    for i, c in enumerate(CARDS, 1):
        (OUT / "cards" / f'{c["slug"]}.svg').write_text(card(t, i, c))
    (OUT / "status").mkdir(exist_ok=True)
    for status in STATUS:
        (OUT / "status" / f"{slug(status)}.svg").write_text(pill(t, status))
    for label, status in PILLS:
        (OUT / "status" / f"{slug(label)}.svg").write_text(pill(t, status, label))
    for f in sorted(OUT.glob("**/*.svg")):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
