"""The source catalog, docs/sources.md: one row per source.

Facts come from code: the registered functions' declarations (class, cadence, scope, egress,
transport, hosts, time budget, knobs), `streamline_defaults.RETIRED`, and the rights registry.
Judgment comes from docs/sources/annotations.csv, keyed on source_key: role, method, speed and
breadth, market edge, API, and notes. A planned source exists only there, and its planned-only
columns carry the facts the plan gives it until its function registers.

    uv run --project functions mdp sources doc [--check]
"""

import csv
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mdp_functions.registry import discover
from mdp_functions.settings import REPO
from mdp_functions.streamline_defaults import RETIRED

ANNOTATIONS = "docs/sources/annotations.csv"
RIGHTS = "dbt/seeds/rights_registry.csv"
OUTPUT = "docs/sources.md"
COMMAND = "uv run --project functions mdp sources doc"

ROLES = ("core product", "enrichment", "identity/reference", "tenant", "ops")
# Each method, and whether it scrapes a public surface.
METHODS = {
    "official API": False,
    "vendor API": False,
    "vendor export": False,
    "public page": True,
    "public embed/JSON": True,
    "dump/mirror": False,
    "tenant file": False,
    "platform seed": False,
    "warehouse": False,
}
EDGES = ("commodity", "uncommon", "own-derived")
GATED = "planned, owner-gated"
STATUSES = (
    "built",
    "fixtures only",
    "built, off",
    "planned",
    GATED,
    "registry only",
    "retired",
)
CLASSES = ("bronze", "silver", "gold", "universal", "seed")
# Filled only on a planned row: code and the rights registry own them everywhere else.
PLANNED_FACTS = (
    "class",
    "scope",
    "refresh",
    "category",
    "license_ref",
    "learn",
    "resale",
)
COLUMNS = (
    "source_key",
    "status",
    "role",
    "method",
    "speed_breadth",
    "market_edge",
    "api",
    "notes",
    *PLANNED_FACTS,
)
NO_EGRESS = {"warehouse", "platform seed"}


@dataclass
class Source:
    key: str
    status: str
    layer: str = ""
    scope: str = "global"
    refresh: str = ""
    category: str = ""
    license_ref: str = ""
    learn: str = ""
    resale: str = ""
    proxy: str = "—"
    hosts: list[str] = field(default_factory=list)
    # A <key>_weekly twin with the same declaration shares this row.
    twin: bool = False
    signature: str = ""
    note: dict[str, str] = field(default_factory=dict)

    @property
    def rank(self) -> tuple[int, int, str]:
        status = GATED if self.status.startswith(GATED) else self.status
        return ROLES.index(self.note["role"]), STATUSES.index(status), self.key


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def facts(root: Path) -> dict[str, Source]:
    """Every source that code or the registry knows: registered invoke functions, retired keys,
    and rights rows that no function declares."""
    if os.environ.get("MDP_FIXTURE_MODE") == "1":
        raise RuntimeError(
            "Unset MDP_FIXTURE_MODE: its probes are test fixtures, not sources"
        )
    catalog = {k: m for k, m in discover().items() if m.kind == "invoke"}
    rights = {r["source_key"]: r for r in read_csv(root / RIGHTS)}
    sources: dict[str, Source] = {}
    for key in sorted({*catalog, *rights, *RETIRED}):
        right = rights.get(key, {})
        source = Source(
            key,
            status="retired" if key in RETIRED else "registry only",
            refresh=right.get("refresh_cadence", ""),
            category=right.get("category", ""),
            license_ref=right.get("license_ref", ""),
            learn=right.get("learning_eligible", ""),
            resale=right.get("resale_permitted", ""),
        )
        manifest = catalog.get(key)
        if manifest:
            source.status = (
                "built, off" if manifest.knobs.get("enabled") is False else "built"
            )
            if key.startswith("sc_"):
                source.status = "fixtures only"
            source.layer = manifest.layer
            source.scope = "tenant" if manifest.tenant_bound else "global"
            source.refresh = manifest.cadence
            if (right.get("refresh_cadence") == "weekly" and manifest.cadence == "daily"
                    and any(c.endswith("_week") for c in manifest.input_version)):
                # A weekly lookup inside the daily job: its input_version carries the cycle's ISO week.
                source.refresh = f"weekly (runs in the daily {source.scope} job, keyed by ISO week)"
            if manifest.time_budget_s:
                source.refresh += f", {round(manifest.time_budget_s / 60)} min budget"
            if manifest.external:
                source.proxy = "yes" if manifest.transport == "residential" else "no"
            source.hosts = list(manifest.hosts)
            declared = {k: v for k, v in manifest.public().items() if k != "source_key"}
            source.signature = json.dumps(
                [declared, manifest.knobs], sort_keys=True, default=str
            )
        sources[key] = source
    for key in sorted(sources):
        daily = key.removesuffix("_weekly")
        if daily != key and daily in sources and twins(sources[daily], sources[key]):
            sources[daily].twin = True
            del sources[key]
    return sources


def twins(daily: Source, weekly: Source) -> bool:
    fields = (
        "status",
        "layer",
        "scope",
        "category",
        "license_ref",
        "learn",
        "resale",
        "proxy",
        "hosts",
        "signature",
    )
    return all(getattr(daily, f) == getattr(weekly, f) for f in fields)


def check_note(
    key: str, note: dict[str, str], planned: bool, retired: bool
) -> list[str]:
    found = []
    if not retired and "—" in (note["speed_breadth"], note["market_edge"]):
        found.append(
            f"{key}: only a retired source leaves speed_breadth or market_edge as `—`"
        )
    if note["role"] not in ROLES:
        found.append(f"{key}: role must be one of {', '.join(ROLES)}")
    if note["method"] not in METHODS:
        found.append(f"{key}: method must be one of {', '.join(METHODS)}")
    level, _, reason = note["market_edge"].partition(": ")
    if note["market_edge"] != "—" and (
        level not in EDGES or not 6 <= len(reason.split()) <= 10
    ):
        found.append(f"{key}: market_edge is `<{'|'.join(EDGES)}>: <6-10 word reason>`")
    for column in ("speed_breadth", "market_edge", "api"):
        if not note[column]:
            found.append(
                f"{key}: {column} is empty (write `not measured` for an unmeasured size)"
            )
    for column, value in note.items():
        if "|" in (value or "") or "\n" in (value or ""):
            found.append(
                f"{key}: {column} holds `|` or a line break, which breaks the table"
            )
    if planned:
        if (
            note["class"] not in CLASSES
            or note["scope"] not in ("global", "tenant")
            or not note["refresh"]
        ):
            found.append(
                f"{key}: a planned row needs class ({', '.join(CLASSES)}), scope and refresh"
            )
        if note["category"] not in ("public charts and catalogs", "platform playlists", "") or any(
            note[c] not in ("true", "false", "") for c in ("learn", "resale")
        ):
            found.append(
                f"{key}: category is public charts and catalogs, platform playlists or empty; learn and resale are true, false or empty"
            )
        if note["status"] != "planned" and not (
            note["status"].startswith(GATED + ": ")
            and note["status"].removeprefix(GATED + ": ").strip()
        ):
            found.append(f"{key}: a planned status is `planned` or `{GATED}: <gate>`")
    else:
        filled = [c for c in PLANNED_FACTS if note[c]]
        if filled:
            found.append(
                f"{key}: code and the rights registry own {', '.join(filled)}; clear them"
            )
    return found


def catalog(root: Path = REPO) -> tuple[list[Source], list[str]]:
    """The table rows in order, and every problem that keeps the annotations from matching code."""
    sources = facts(root)
    path = root / ANNOTATIONS
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        header, notes = tuple(reader.fieldnames or ()), list(reader)
    if header != COLUMNS:
        return [], [f"{ANNOTATIONS}: header must be {','.join(COLUMNS)}"]
    found: list[str] = []
    seen: set[str] = set()
    rows: list[Source] = []
    for note in notes:
        key, status = note["source_key"], note["status"]
        if key in seen:
            found.append(f"{key}: annotated twice")
            continue
        seen.add(key)
        planned = status.startswith("planned")
        if key in sources:
            source = sources[key]
            if planned:
                found.append(
                    f"{key}: code or the rights registry now knows it; clear its status and planned-only columns"
                )
                continue
            if status == "fixtures only" and source.status == "built":
                source.status = status
            elif status:
                found.append(
                    f"{key}: status is set by code; an annotation may only say `fixtures only` on a built source"
                )
            missing = [
                h for h in source.hosts if h.removeprefix("*.") not in note["api"]
            ]
            if missing and source.status != "fixtures only":
                found.append(
                    f"{key}: api must name its declared hosts {', '.join(missing)}"
                )
        elif planned:
            source = Source(
                key,
                status=status,
                layer=note["class"],
                scope=note["scope"],
                refresh=note["refresh"],
                category=note["category"],
                license_ref=note["license_ref"],
                learn=note["learn"],
                resale=note["resale"],
                proxy="—" if note["method"] in NO_EGRESS else "no",
            )
        elif (
            key.removesuffix("_weekly") in sources
            and sources[key.removesuffix("_weekly")].twin
        ):
            found.append(
                f"{key}: its daily twin's row covers it; annotate {key.removesuffix('_weekly')} only"
            )
            continue
        else:
            found.append(
                f"{key}: not a registered function, a retired key or a rights row, and not planned"
            )
            continue
        found.extend(check_note(key, note, planned, source.status == "retired"))
        source.note = note
        rows.append(source)
    for key in sorted(set(sources) - seen):
        found.append(f"{key}: no row in {ANNOTATIONS}; add one")
    if found:
        return [], found
    return sorted(rows, key=lambda s: s.rank), []


def yes(flag: str) -> str:
    return {"true": "yes", "false": "no"}.get(flag, "?")


def row(source: Source) -> str:
    note = source.note
    name = f"`{source.key}`" + (" + `_weekly`" if source.twin else "")
    if source.layer and source.layer != "bronze":
        name += f" · {source.layer}"
    refresh = source.refresh + (" + weekly twin" if source.twin else "")
    if source.scope == "tenant" and "tenant job" not in refresh:
        refresh += " · tenant"
    method = note["method"] + (" (scraped)" if METHODS[note["method"]] else "")
    rights = f"{yes(source.learn)}/{yes(source.resale)}" + (
        f" · {source.license_ref}" if source.license_ref else ""
    )
    if not (source.learn or source.resale or source.license_ref):
        rights = "—"  # no rights row: a retired key whose row was removed
    cells = [
        name,
        source.status,
        note["role"],
        method,
        source.proxy,
        refresh or "—",
        note["speed_breadth"],
        source.category or "—",
        note["api"],
        rights,
    ]
    return "| " + " | ".join(cells) + " |"


def counts(values: list[str], order: tuple[str, ...]) -> str:
    tally = Counter(values)
    return " · ".join(f"{name} {tally[name]}" for name in order if tally[name])


TEMPLATE = """# Data sources

<!-- Generated by `{command}`. Edit the function declarations or {annotations}, then regenerate; never edit this file. -->

Sources collected, derived, imported or planned. Facts come from function declarations, `streamline_defaults` and [`{rights}`](../{rights}). Assessments come from [`{annotations}`](sources/annotations.csv). Planned rows are recorded in the same annotations file.

Public charts and catalogs, platform playlists, and derived tables have distinct access and refresh schedules. The registry records each source’s usage permissions.

- **Status**: `built` and `built, off` are the switch a new streamline starts with in code; operators flip the live switch in the console. `fixtures only` lands checked-in fixtures. `registry only` has a rights row and no function. `retired` keeps its streamline and landed rows, disabled.
- **Method**: `(scraped)` marks a public page or embed read with ordinary HTTP and an honest user agent. SoundCloud live access requires an official, credentialed integration; the included readers are offline fixtures only.
- **Proxy**: `no` fetches direct from Fly; `—` makes no outside request.
- **Refresh**: the declared cadence. `+ weekly twin` means a `<key>_weekly` key shares the row and fetches weekly members in weekday buckets; `tenant` means tenant-bound; a budget is the time after which a gold run stops and ends partial.
- **Speed & breadth**: measured from recorded runs, else `not measured`; `plan:` is a planned size, not a measurement.
- **Rights**: learning eligible / resale permitted, then the licence reference. Rights annotate rows and never gate them.

## Summary

- **Rows**: {rows}; a `_weekly` twin shares its daily key's row.
- **Status**: {statuses}.
- **Category**: {categories}.
- **Method**: {methods}.
- **Scraped**: {scraped} rows read a public page or embed; {scraped_built} of them are built.
- **Proxy**: {proxied} rows declare the residential tier. It exists for targets whose market changes the response, and an operator knob can force it per streamline.
{own_summary}
## Sources

| Source | Status | Role | Method (scrape?) | Proxy | Refresh | Speed & breadth | Cat | API | Rights (learn/resale) |
|---|---|---|---|---|---|---|---|---|---|
{table}

## Notes

{notes}
"""


def render(rows: list[Source]) -> str:
    scraped = [s for s in rows if METHODS[s.note["method"]]]
    grouped: dict[str, list[str]] = {}
    for source in rows:
        if source.note["notes"]:
            grouped.setdefault(source.note["notes"], []).append(f"`{source.key}`")
    return TEMPLATE.format(
        command=COMMAND,
        annotations=ANNOTATIONS,
        rights=RIGHTS,
        rows=len(rows),
        statuses=counts(
            [GATED if s.status.startswith(GATED) else s.status for s in rows], STATUSES
        ),
        categories=counts([s.category or "none" for s in rows], ("public charts and catalogs", "platform playlists", "none")),
        methods=counts([s.note["method"] for s in rows], tuple(METHODS)),
        scraped=len(scraped),
        scraped_built=sum(
            s.status in ("built", "built, off", "fixtures only") for s in scraped
        ),
        proxied=sum(s.proxy == "yes" for s in rows),
        own_summary=("- **Own-derived**: " + ", ".join(
            f"`{s.key}`"
            for s in rows
            if s.note["market_edge"].startswith("own-derived")
        ) + ".\n") if any(s.note["market_edge"].startswith("own-derived") for s in rows) else "",
        table="\n".join(row(s) for s in rows),
        notes="\n".join(
            f"- {', '.join(keys)}: {text}" for text, keys in grouped.items()
        ),
    )


def check(root: Path = REPO) -> list[str]:
    rows, found = catalog(root)
    if found:
        return found
    if not (root / OUTPUT).exists() or (root / OUTPUT).read_text() != render(rows):
        return [f"{OUTPUT} is stale: run `{COMMAND}`"]
    return []


def write(root: Path = REPO) -> list[str]:
    """Regenerates the doc, or returns the problems that stop it."""
    rows, found = catalog(root)
    if not found:
        (root / OUTPUT).write_text(render(rows))
    return found
