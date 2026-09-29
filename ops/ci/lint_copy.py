"""Flag performative headline patterns and the long form of Ops in visible copy.

The constants below define the copy rules. This lint keeps the sweep that fixed
`mdp records the next movement.`, `the music leaves an asset.` and `Operations` from drifting
back: a headline that makes the product the subject of a perception verb, performs a metaphor
instead of stating a fact, opens on a rhetorical "the", or says the long form of a word people
say short; and any copy that makes the product the subject of a sentence ("Music Data Platform sorts
records", "songs mdp tracks") instead of saying what was read or done.
"""

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ALLOWLIST_FILES: set[str] = set()
# Build logs, evidence archives and agent-only orientation docs are history, not a copy surface.
ALLOWLIST_DIRS = ("ops/evidence/", "ops/CLAUDE.md", "ops/ci/lifecycle/")

# Known mannered constructions already found and fixed once. A literal match means one came back.
MANNERED_PHRASES = (
    "hears new music",
    "hears the next movement",
    "leaves an asset",
    "leaves a trace",
    "questions become answers",
    "keeps listening",
    "movement finds its music",
    "belongs to the team",
    "travels before the world knows",
)

LEADING_THE = re.compile(r"^(?:the|The)\s+\S")
BRAND_VERB = re.compile(
    r"^(?:mdp|music data platform) (?:records|hears|sees|knows|feels|reads|watches|tracks|senses|understands|listens)\b",
    re.IGNORECASE,
)
# The product as the subject of a sentence anywhere in copy ("Music Data Platform sorts records", "songs
# mdp tracks", "how mdp found it"): say what was done or read instead.
BRAND_SUBJECT = re.compile(
    r"\b(?:mdp|music data platform) (?:records|hears|sees|knows|feels|reads|watches|tracks|senses|understands|listens"
    r"|sorts|writes|adds|finds|found|tends)\b",
    re.IGNORECASE,
)
BARE_OPERATIONS = re.compile(r"\bOperations\b")

HEADING_TAG = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.DOTALL)
OUTAGE_HEADLINES = {"the music is out of reach.", "the songs are out of reach."}
COMING_TITLE = re.compile(r"<Coming\b[^>]*\btitle=\"([^\"]*)\"")
QUOTED = re.compile(r"['\"]([^'\"]{4,160})['\"]")

SCAN_GLOBS = (
    "control/apps/showcase/**/*.ts",
    "control/apps/showcase/**/*.tsx",
    "control/apps/control-api/src/**/*.ts",
    "control/apps/control-api/src/**/*.tsx",
    "control/packages/mdp-cli/src/**/*.ts",
    "control/packages/contracts/src/source-wording.ts",
    "r/mdpr/R/**/*.R",
    "ops/readme/cards.py",
    "docs/*.md",
    "README.md",
)
CATALOG = "docs/errors/catalog.yml"


def allowed(relative: str) -> bool:
    posix = relative.replace("\\", "/")
    return posix in ALLOWLIST_FILES or any(posix.startswith(d) for d in ALLOWLIST_DIRS)


def markdown_headlines(text: str):
    """Read ATX and Setext headings without treating fenced examples as copy."""
    fence = None
    previous = None
    for line, value in enumerate(text.splitlines(), 1):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", value)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            previous = None
            continue
        if fence is not None:
            continue
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", value)
        if heading:
            yield heading.group(1), line
        elif previous and re.fullmatch(r" {0,3}(?:=+|-+)\s*", value):
            yield previous, line - 1
        previous = value.strip() if value.strip() and not heading else None


def headline_candidates(text: str, suffix: str = ""):
    """Literal headline strings from heading tags and Coming's title prop, skipping expressions."""
    for match in HEADING_TAG.finditer(text):
        inner = match.group(1).strip()
        line = text.count("\n", 0, match.start()) + 1
        if "{" in inner:
            for quoted in QUOTED.finditer(inner):
                yield quoted.group(1), line
        elif inner and "<" not in inner:
            yield inner, line
    for match in COMING_TITLE.finditer(text):
        yield match.group(1), text.count("\n", 0, match.start()) + 1
    if suffix == ".md":
        yield from markdown_headlines(text)


def visible_candidates(text: str, suffix: str):
    """Read prose, literal UI strings and JSX text, leaving identifiers out."""
    if suffix in {".md", ".yml"}:
        for line, value in enumerate(text.splitlines(), 1):
            yield value, line
        return
    pattern = r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|>([^<>{}]+)<"
    for match in re.finditer(pattern, text):
        value = match.group(1) if match.group(1) is not None else match.group(0)[1:-1]
        yield value, text.count("\n", 0, match.start()) + 1


FLOW_COPY = "docs/sources/flows.csv"
FLOW_BANNED = re.compile(
    r"\b(?:bronze|silver|gold|universal|mart|invoke|cycle|tenant|knob|streamline|scope|payload|endpoint|scrape|idle)\b|switched off|15 minutes",
    re.IGNORECASE,
)
FLOW_VERBS = re.compile(
    r"^(?:Reads|Checks|Saves|Looks up|Matches|Copies|Counts|Takes|Adds|Asks|Prepares|Freezes|Closes|Searches|Finds|Collects|Lists|Compares|Groups|Records)\b"
)


def flow_violations(path: Path):
    found = []
    with path.open(newline="") as file:
        for line, row in enumerate(csv.DictReader(file), 2):
            for field in ("what_it_does", "why_it_matters"):
                value = row.get(field, "")
                if FLOW_BANNED.search(value):
                    found.append(
                        (FLOW_COPY, line, "function-jargon", row["source_key"])
                    )
                if BRAND_VERB.match(value) or BRAND_SUBJECT.search(value):
                    found.append((FLOW_COPY, line, "brand-subject", row["source_key"]))
            what = row.get("what_it_does", "")
            if not 12 <= len(what.split()) <= 45:
                found.append((FLOW_COPY, line, "function-length", row["source_key"]))
            if not FLOW_VERBS.match(what):
                found.append((FLOW_COPY, line, "function-verb", row["source_key"]))
            for field in ("idea_1", "idea_2", "idea_3"):
                if row.get(field) and not row[field].startswith("Could "):
                    found.append((FLOW_COPY, line, "function-idea", row["source_key"]))
    return found


def find_violations(root: Path = ROOT):
    violations = (
        flow_violations(root / FLOW_COPY) if (root / FLOW_COPY).exists() else []
    )
    paths = {p for pattern in SCAN_GLOBS for p in root.glob(pattern)}
    catalog = root / CATALOG
    if catalog.exists():
        paths.add(catalog)
    for path in sorted(paths):
        if (
            "node_modules" in path.parts
            or "test" in path.parts
            or "tests" in path.parts
            or path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        ):
            continue
        relative = str(path.relative_to(root))
        if allowed(relative):
            continue
        text = path.read_text()
        # Comments describe code and can quote a rejected headline.
        if path.suffix not in {".md", ".yml"}:
            text = re.sub(
                r"/\*.*?\*/",
                lambda m: "\n" * m.group(0).count("\n"),
                text,
                flags=re.DOTALL,
            )
            text = re.sub(r"(?m)^\s*(?://|#)[^\n]*", "", text)
        for headline, line in headline_candidates(text, path.suffix):
            if LEADING_THE.match(headline) and headline.lower() not in OUTAGE_HEADLINES:
                violations.append((relative, line, "leading-the", headline))
            if BRAND_VERB.match(headline):
                violations.append((relative, line, "brand-verb", headline))
            for phrase in MANNERED_PHRASES:
                if phrase in headline.lower():
                    violations.append((relative, line, "mannered-phrase", headline))
        for visible, line in visible_candidates(text, path.suffix):
            for rule, pattern in (
                ("brand-subject", BRAND_SUBJECT),
                ("long-form-ops", BARE_OPERATIONS),
            ):
                for match in pattern.finditer(visible):
                    violations.append((relative, line, rule, match.group(0)))
    return violations


def main():
    violations = find_violations()
    if violations:
        lines = [
            f"{path}:{line}: {rule} ({snippet!r})"
            for path, line, rule, snippet in violations
        ]
        raise SystemExit("FAIL copy lint:\n" + "\n".join(lines))
    print(
        "PASS copy lint: no performative headlines, mannered phrases or long-form Ops"
    )


if __name__ == "__main__":
    main()
