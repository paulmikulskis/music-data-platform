"""Read pinned objects and emit reviewed preview facts. See ops/showcase/README.md."""

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from http.client import HTTPException
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[3]
LIB = Path("control/apps/showcase/lib")
LINKS = Path("ops/showcase/links")
LINK_ID_PATTERN = r"^[a-z][a-z0-9-]+$"


class Refused(ValueError):
    def __init__(self, link_id, result="links_invalid"):
        self.link_id = (
            link_id
            if isinstance(link_id, str)
            and re.fullmatch(LINK_ID_PATTERN, link_id)
            and not any(name.casefold() in link_id.casefold() for name in deny_names())
            else "links"
        )
        self.result = result
        super().__init__(f"{self.link_id} {result}")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Code(Strict):
    repo: Literal["music-data-platform"]
    path: str
    anchor: str | None


class Route(Strict):
    route: str
    back: str | None


class Host(Strict):
    host: str


class Link(Strict):
    id: str = Field(pattern=LINK_ID_PATTERN)
    label: str
    category: Literal[
        "repo-path",
        "repo",
        "docs",
        "analyst-sql",
        "console",
        "deployed demo",
        "dashboard",
        "internal",
        "provider",
    ]
    destination: Code | Route | Host
    access: Literal[
        "code", "session", "invite", "gateway", "in-app", "public"
    ]
    surfaces: list[str] = Field(min_length=1)
    thumbnail: Literal["folder", "file", "doc", "page", "door", "none"]
    nouns: list[str] = Field(min_length=2, max_length=3)
    highlights: list[str] = Field(max_length=3)
    headings: list[str] = Field(max_length=3)
    line_marker: str | None


class Manifest(Strict):
    schema_version: Literal[1]
    console_tenant_review: str | None = Field(min_length=1)
    links: list[Link]


class NameExemption(Strict):
    path: str
    reason: str = Field(min_length=1)
    reviewed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("path")
    @classmethod
    def exact_path(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", value) or any(
            part in {"", ".", ".."} for part in value.split("/")
        ):
            raise ValueError("Use an exact repository file path")
        return value

    @field_validator("reviewed_on")
    @classmethod
    def reviewed_date(cls, value):
        dt.date.fromisoformat(value)
        return value


def aware_date(value):
    if value is not None:
        instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("Date needs a timezone")
    return value


class Probe(Strict):
    state: Literal["not_checked", "answered", "no_answer"]
    checked_at: str | None
    note: Literal["Not checked at deploy", "Answered at deploy", "No answer at deploy"]

    _date = field_validator("checked_at")(aware_date)


class Fact(Strict):
    id: str
    variant: str
    destination: Code | Route | Host
    object_type: Literal["tree", "blob"] | None
    highlights: list[str] = Field(max_length=3)
    listing_count: int | None = Field(ge=0)
    line_count: int | None = Field(ge=0)
    headings: list[str] = Field(max_length=3)
    line: int | None = Field(gt=0)
    body: list[str]
    proper_names: list[str]
    probe: Probe
    names: Literal["checked", "names_not_checked"]
    exempted_name_matches: int = Field(ge=0)


class Generated(Strict):
    schema_version: Literal[1]
    revision: str | None = Field(pattern=r"^[0-9a-f]{40}$")
    collected_at: str
    input_hashes: dict[str, str]
    names: Literal["checked", "names_not_checked"]
    tenants: Literal["checked", "not_checked"]
    entries: list[Fact]

    _date = field_validator("collected_at")(aware_date)


def read(path):
    return json.loads(path.read_text())


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    if result.returncode:
        raise Refused("links", "links_git_unavailable")
    return result.stdout.decode("utf-8", errors="replace")


def secret_context(text, policy):
    """Match words and snake, kebab or camel case name parts."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = policy["opaque_token"]["secret_words"]
    return any(
        part in words or (part.endswith("s") and part[:-1] in words)
        for part in re.findall(r"[a-z]+", text.casefold())
    )


def strings(value, policy, secret=False, ancestors=frozenset()):
    if isinstance(value, str):
        yield value, secret
    elif id(value) in ancestors:
        return
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from strings(item, policy, secret, ancestors | {id(value)})
    elif isinstance(value, dict):
        for key, item in value.items():
            named_secret = isinstance(key, str) and secret_context(key, policy)
            if isinstance(key, str):
                yield key, secret
            yield from strings(
                item, policy, secret or named_secret, ancestors | {id(value)}
            )


def secret_values(text, policy, secret=False, path=None):
    """Read field context without retaining a source value in the artifact."""
    suffix = Path(path).suffix.lower() if path else ""
    if suffix == ".csv":
        rows = csv.reader(io.StringIO(text), strict=True)
        headers = next(rows, [])
        normalized = [
            " ".join(re.findall(r"[^\W_]+", header.casefold())) for header in headers
        ]
        if (
            not headers
            or any(not header for header in normalized)
            or len(set(normalized)) != len(headers)
        ):
            raise ValueError("CSV headers must be distinct and nonempty")
        for row in rows:
            if len(row) != len(headers):
                raise ValueError("CSV values must match the headers")
            for header, cell in zip(headers, row, strict=True):
                yield from secret_values(
                    cell, policy, secret or secret_context(header, policy)
                )
        return
    if secret:
        yield text
        return
    for line in text.splitlines():
        if secret_context(line, policy):
            yield line
    try:
        if suffix == ".json":
            values = [json.loads(text)]
        elif suffix in {".yaml", ".yml"}:
            values = yaml.safe_load_all(text)
        else:
            return
        for value in values:
            for content, named_secret in strings(value, policy):
                if named_secret:
                    yield content
    except (ValueError, RecursionError, yaml.YAMLError):
        # Malformed source still receives the line scan and every known-prefix check.
        return


def opaque_token(value, policy):
    """Recognize public hashes and slugs before checking credential-shaped text."""
    if re.fullmatch(policy["hash"], value):
        return False
    if re.fullmatch(policy["slug"], value) and not re.search(
        policy["long_numeric_word"], value
    ):
        return False
    bare = value.rstrip("=")
    # Keep long unbroken strings conservative, including weak synthetic tokens.
    if len(bare) >= policy["long_alphanumeric_length"] and re.fullmatch(
        "[A-Za-z0-9]+", bare
    ):
        return True
    if (re.fullmatch("[A-Za-z0-9]+", bare) or "+" in value or "=" in value) and all(
        re.search(pattern, value) for pattern in ["[a-z]", "[A-Z]", "[0-9]"]
    ):
        return True
    entropy = -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in Counter(value).values()
    )
    threshold = min(
        policy["minimum_entropy"], math.log2(len(value)) - policy["short_token_margin"]
    )
    return entropy >= threshold


def pattern_matches(text, rule, policy, secret=False, path=None):
    flags = re.IGNORECASE if "i" in rule["flags"] else 0
    if rule.get("kind") == "opaque":
        try:
            return any(
                opaque_token(match[0], policy["opaque_token"])
                for content in secret_values(text, policy, secret, path)
                for match in re.finditer(rule["pattern"], content, flags)
            )
        except (csv.Error, ValueError):
            # Ambiguous CSV fields cannot establish a public value's context.
            return True
    return bool(re.search(rule["pattern"], text, flags))


def scan(value, root, deny, link_id):
    policy = read(root / LIB / "linked-content-policy.json")
    patterns = [*read(root / LIB / "sensitive-patterns.json"), *policy["hard"]]
    for text, secret in strings(value, policy):
        if any(pattern_matches(text, pattern, policy, secret) for pattern in patterns):
            raise Refused(link_id, "links_sensitive")
        if any(name.casefold() in text.casefold() for name in deny):
            raise Refused(link_id, "links_name")


def name_exemptions(root):
    try:
        value = read(root / LINKS / "name-exemptions.json")
        if not isinstance(value, list):
            raise TypeError()
        reviewed = [NameExemption.model_validate(item) for item in value]
        paths = {item.path for item in reviewed}
        if len(paths) != len(reviewed):
            raise ValueError()
        scan(value, root, deny_names(), "links")
        return paths
    except (ValueError, TypeError, OSError):
        raise Refused("links") from None


def shell_prices(text):
    """Keep literal money; shell positional expansions do not contain an amount."""
    output = []
    quote = None
    comment = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            comment = False
        if not comment:
            if char == "\\" and quote != "'":
                output.append(text[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = None
            elif char in {"'", '"'} and quote is None:
                quote = char
            elif char == "#" and quote is None:
                comment = True
            elif char == "$" and quote != "'":
                parameter = re.match(r"\$[0-9]+(?!\d|\.\d)", text[index:])
                if parameter:
                    output.append("<parameter>")
                    index += len(parameter[0])
                    continue
        output.append(char)
        index += 1
    return "".join(output)


def sql_prices(text):
    # Preserve quoted values and comments, where an amount is still literal.
    return re.sub(
        r"(?P<literal>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/|\$(?P<tag>[A-Za-z_0-9]*)\$.*?\$(?P=tag)\$)|(\$[1-9][0-9]*)(?!\d|\.\d)",
        lambda match: match["literal"] or "<parameter>",
        text,
        flags=re.DOTALL,
    )


def price_text(text, path):
    """Recognize parameter syntax only in code; prose and card text stay literal."""
    suffix = Path(path).suffix if path else ""
    if suffix in {".sh", ".bash", ".zsh"}:
        return shell_prices(text)
    if suffix == ".sql":
        return sql_prices(text)
    if suffix in {".yml", ".yaml"}:
        try:
            node = yaml.compose(text, Loader=yaml.SafeLoader)
        except yaml.YAMLError:
            return text
        spans = []
        visited = set()

        def visit(value):
            if id(value) in visited:
                return
            visited.add(id(value))
            if isinstance(value, yaml.MappingNode):
                for key, child in value.value:
                    if key.value == "run" and isinstance(child, yaml.ScalarNode):
                        spans.append(
                            (child.start_mark.index, child.end_mark.index, child.value)
                        )
                    else:
                        visit(child)
            elif isinstance(value, yaml.SequenceNode):
                for child in value.value:
                    visit(child)

        visit(node)
        for start, end, command in sorted(spans, reverse=True):
            text = text[:start] + shell_prices(command) + text[end:]
        return text
    if suffix == ".md":
        return re.sub(
            r"(?m)^```(sh|bash|zsh|sql)\s*\n([\s\S]*?)^```",
            lambda match: (
                f"```{match[1]}\n" + price_text(match[2], f"code.{match[1]}") + "```"
            ),
            text,
        )
    if suffix in {".ts", ".tsx", ".js", ".py"}:
        # SQL embedded in source strings keeps its own quotes and parameter syntax.
        return re.sub(
            r"""(?P<comment>\#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/)|(?P<quote>\"\"\"|\x27\x27\x27|`|\"|\x27)(?:\\.|(?!(?P=quote))[\s\S])*?(?P=quote)""",
            lambda match: (
                match["quote"]
                + sql_prices(match[0][len(match["quote"]) : -len(match["quote"])])
                + match["quote"]
                if not match["comment"]
                and re.search(
                    r"\b(?:SELECT|FROM|WHERE|JOIN|AND\s+[A-Za-z_][\w.]*\s*[=<>]|lower\s*\()",
                    match[0],
                    re.IGNORECASE,
                )
                else match[0]
            ),
            text,
        )
    return text


def content_classes(value, root, deny, path=None, review=None):
    """Classify a GitHub landing view without retaining any matched value."""
    policy = read(root / LIB / "linked-content-policy.json")
    found = set()
    for text, secret in strings(value, policy):
        for rule in [*policy["hard"], *policy["record"]]:
            scanned = price_text(text, path) if rule["class"] == "price" else text
            if pattern_matches(scanned, rule, policy, secret, path):
                found.add(rule["class"])
        if any(name.casefold() in text.casefold() for name in deny):
            found.add("name")
    if review is not None and path and "credential" in found:
        scan(path, root, deny, "links")
        review["refused_files"].add(path)
    return found


def check_content(classes, root, link_id):
    hard = {
        rule["class"]
        for rule in read(root / LIB / "linked-content-policy.json")["hard"]
    }
    for result in sorted(classes):
        if result == "name":
            raise Refused(link_id, "links_name")
        if result in hard:
            raise Refused(link_id, f"links_content_{result}")


def deny_names():
    return [
        s.strip()
        for s in os.environ.get("MDP_SHOWCASE_DENY_NAMES", "").splitlines()
        if s.strip()
    ]


def slug(text):
    return re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-")


def headings(text):
    result = {}
    counts = {}
    fenced = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*$", line)
        if fenced or not match:
            continue
        title = match[1]
        base = slug(title)
        count = counts.get(base, 0)
        counts[base] = count + 1
        result[base if count == 0 else f"{base}-{count}"] = title
    return result


def word_gate(body, proper, root, link_id):
    policy = read(root / LIB / "card-words.json")
    banned = re.compile(
        r"\b(?:" + "|".join(policy["banned"] + policy["jargon"]) + r")\b", re.IGNORECASE
    )
    if any(banned.search(s.replace("_", " ")) for s in body):
        raise Refused(link_id, "links_words")
    words = re.findall(r"[^\W_]+(?:[’\':.,-][^\W_]+)*", " ".join([*body, *proper]))
    if len(words) > 12 or sum(any(c.isdigit() for c in w) for w in words) > 2:
        raise Refused(link_id, "links_words")


def public_hosts(root):
    return {h["host"]: h for h in read(root / LIB / "public-hosts.json")}


def inputs(root):
    name_exemptions(root)
    return {
        str(p): digest(read(root / p))
        for p in [
            LINKS / "links.json",
            LINKS / "name-exemptions.json",
            LIB / "public-hosts.json",
            LIB / "card-words.json",
            LIB / "sensitive-patterns.json",
            LIB / "linked-content-policy.json",
            LIB / "lineage.generated.json",
            LIB / "source-registry.generated.json",
        ]
    }


def definitions(root):
    try:
        manifest = Manifest.model_validate(read(root / LINKS / "links.json"))
    except (ValueError, OSError):
        raise Refused("links") from None
    if len({l.id for l in manifest.links}) != len(
        manifest.links
    ):
        raise Refused("links")
    for link in manifest.links:
        # The identifier itself is untrusted until this scan passes. Never use
        # a rejected identifier to label its own diagnostic.
        if not re.fullmatch(LINK_ID_PATTERN, link.id):
            raise Refused("links")
        scan(link.id, root, deny_names(), "links")
        destination = link.destination
        if isinstance(destination, Code) and (
            (destination.anchor == "L") != bool(link.line_marker)
        ):
            raise Refused(link.id, "links_line")
        if (
            link.thumbnail == "doc"
            and isinstance(destination, Code)
            and (not link.headings or destination.anchor != link.headings[0])
        ):
            raise Refused(link.id, "links_anchor")
    return manifest


def reader_keys(root):
    # Lineage excludes tenant declarations. Intersect it with reviewed external readers.
    graph = read(root / LIB / "lineage.generated.json")
    files = {
        node.get("file") for node in graph["nodes"] if node["id"].startswith("fn:")
    }
    return [
        key
        for key, value in read(root / LIB / "source-registry.generated.json").items()
        if value["source"]
        and f"functions/src/mdp_functions/sources/{key}/function.py" in files
    ]


def variants(link, root):
    destination = link.destination
    if isinstance(destination, Code) and destination.path == "<node.file>":
        files = sorted(
            {
                n["file"]
                for n in read(root / LIB / "lineage.generated.json")["nodes"]
                if n.get("file")
            }
        )
        return [(path, destination.model_copy(update={"path": path})) for path in files]
    if isinstance(destination, Code) and "<key>" in destination.path:
        sources = reader_keys(root)
        return [
            (
                key,
                destination.model_copy(
                    update={"path": destination.path.replace("<key>", key)}
                ),
            )
            for key in sources
        ]
    if link.id == "proof-open-console":
        sources = reader_keys(root)
        return [
            (key, Route(route="/functions/" + key, back="/sources")) for key in sources
        ]
    if link.id == "credits-provider":
        # Provider destinations already belong to the Mark catalog. These rows carry no card.
        return []
    if link.id == "team-desk-anchors":
        return [
            (key, Route(route=route, back=None))
            for key, route in [
                ("cylinder", "/stack#warehouse"),
                ("laptop", "/team#practice"),
                ("screen", "/team#screen"),
            ]
        ]
    if link.id == "home-steps":
        return [
            (
                key,
                Route(
                    route="/team#screen" if key == "dbt" else "/team#first-question",
                    back=None,
                ),
            )
            for key in ["postgresql", "r", "python", "dbt"]
        ]
    return [("", destination)]


def check_destination(destination, link, root):
    if isinstance(destination, Host):
        if destination.host not in public_hosts(root):
            raise Refused(link.id, "links_host")
    elif isinstance(destination, Code):
        path = destination.path
        if (
            not path
            or path.startswith("/")
            or any(p in {".", "..", ""} for p in path.split("/"))
            or re.search(r"[\\%?#]", path)
        ):
            raise Refused(link.id)
    else:
        route = destination.route.split("?", 1)[0]
        if not route.startswith("/") or re.search(
            r"[\\%\s]|//|(?:^|/)\.\.?(/|$)", route
        ):
            raise Refused(link.id)
        if link.access == "session" and route.split("/")[1] not in {
            "ops",
            "functions",
            "runs",
            "workbench",
            "explorer",
        }:
            raise Refused(link.id)


def landing(
    link,
    destination,
    repo,
    revision,
    root,
    deny,
    names_checked,
    exemptions,
    review=None,
):
    path = destination.path
    ref = f"{revision}:{path}"
    try:
        kind = git(repo, "cat-file", "-t", ref).strip()
        if kind not in {"tree", "blob"}:
            raise Refused(link.id)
        names = (
            git(repo, "ls-tree", "--name-only", "-z", ref).split("\0")[:-1]
            if kind == "tree"
            else []
        )
        classes = content_classes(names, root, deny)
        if names_checked:
            for entry in names or [""]:
                subject = git(
                    repo,
                    "log",
                    "-1",
                    "--format=%s",
                    revision,
                    "--",
                    f"{path}/{entry}" if entry else path,
                )
                classes.update(content_classes(subject, root, deny))
        blobs = (
            [path]
            if kind == "blob"
            else [
                f"{path}/{n}"
                for n in names
                if n.lower() in {"readme", "readme.md", "readme.rst", "readme.txt"}
            ]
        )
        contents = ""
        exempted_matches = 0
        for blob in blobs:
            contents = git(repo, "show", f"{revision}:{blob}")
            found = content_classes(contents, root, deny, blob, review)
            if blob in exemptions and "name" in found:
                # Only this file's content is reviewed. Names and commit subjects
                # still use the normal scan, as do all other pattern classes.
                pattern = "|".join(
                    re.escape(name.casefold())
                    for name in sorted(set(deny), key=len, reverse=True)
                )
                exempted_matches += len(re.findall(pattern, contents.casefold()))
                found.remove("name")
            classes.update(found)
    except Refused as error:
        raise Refused(link.id, error.result) from None
    return kind, names, contents, classes, exempted_matches


def code_fact(
    link, destination, repo, revision, root, deny, names_checked, classes, exemptions
):
    kind, names, contents, found, exempted_matches = landing(
        link, destination, repo, revision, root, deny, names_checked, exemptions
    )
    classes.update(found)
    check_content(found, root, link.id)
    if any(name not in names for name in link.highlights):
        raise Refused(link.id, "links_highlight")
    if (link.thumbnail == "folder") != (kind == "tree"):
        raise Refused(link.id, "links_object_type")
    titles = headings(contents)
    if any(h not in titles for h in link.headings):
        raise Refused(link.id, "links_anchor")
    if (
        destination.anchor
        and destination.anchor != "L"
        and destination.anchor not in titles
    ):
        raise Refused(link.id, "links_anchor")
    line = None
    if link.line_marker:
        line = next(
            (
                i
                for i, value in enumerate(contents.splitlines(), 1)
                if link.line_marker in value
            ),
            None,
        )
        if line is None:
            raise Refused(link.id, "links_line")
    return {
        "object_type": kind,
        "highlights": link.highlights,
        "listing_count": len(names) if kind == "tree" else None,
        "line_count": len(contents.splitlines()) if kind == "blob" else None,
        "headings": [titles[h] for h in link.headings],
        "line": line,
        "exempted_name_matches": exempted_matches,
    }


def probe(host, root, now):
    value = {
        "state": "not_checked",
        "checked_at": None,
        "note": "Not checked at deploy",
    }
    if os.environ.get("MDP_SHOWCASE_PROBE_HOSTS") != "1":
        return value

    # Refuse redirects so a public endpoint cannot turn this into a private request.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(
            public_hosts(root)[host]["health"], timeout=5
        ) as response:
            answered = 200 <= response.status < 300
    except (OSError, ValueError, HTTPException):
        answered = False
    return {
        "state": "answered" if answered else "no_answer",
        "checked_at": now,
        "note": "Answered at deploy" if answered else "No answer at deploy",
    }


def body_for(link, destination, facts):
    body = list(link.nouns)
    proper = []
    if link.thumbnail == "folder":
        body = [
            destination.repo,
            link.nouns[0],
            f"+{facts['listing_count'] - len(facts['highlights'])} more",
        ]
        proper = facts["highlights"]
    elif link.thumbnail == "file":
        layer = next(
            (
                label
                for prefix, label in [
                    ("dbt/models/staging/", "Cleaned"),
                    ("dbt/models/intermediate/", "Joined"),
                    ("dbt/models/marts/", "Ready"),
                    ("functions/", "Python"),
                    ("control/", "TypeScript"),
                ]
                if destination.path.startswith(prefix)
            ),
            "Code",
        )
        body = [link.nouns[0], layer, f"{facts['line_count']} lines"]
        proper = [Path(destination.path).name]
    elif link.thumbnail == "doc":
        body = [link.nouns[0]]
        proper = facts["headings"][:1]
    return body, proper


def collect(root=ROOT, repo=ROOT, revision="HEAD", now=None):
    manifest = definitions(root)
    exemptions = name_exemptions(root)
    deny = deny_names()
    scan(manifest.console_tenant_review, root, deny, "links")
    revision = git(repo, "rev-parse", f"{revision}^{{commit}}").strip()
    names_checked = (
        bool(deny)
        and git(repo, "rev-parse", "--is-shallow-repository").strip() == "false"
    )
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    tenants = os.environ.get("MDP_SHOWCASE_TENANT_COUNT")
    if tenants is not None and not re.fullmatch(r"\d+", tenants):
        raise Refused("links", "links_tenants_unknown")
    entries = []
    probes = {}
    for link in manifest.links:
        scan(link.model_dump(), root, deny, link.id)
        if (
            tenants
            and int(tenants) > 0
            and not manifest.console_tenant_review
            and link.access == "session"
        ):
            raise Refused(link.id, "tenants_present")
        classes = set()
        for variant, destination in variants(link, root):
            check_destination(destination, link, root)
            facts = {
                "exempted_name_matches": 0,
                "object_type": None,
                "highlights": [],
                "listing_count": None,
                "line_count": None,
                "headings": [],
                "line": None,
            }
            status = {
                "state": "not_checked",
                "checked_at": None,
                "note": "Not checked at deploy",
            }
            if isinstance(destination, Code):
                if destination.repo == "music-data-platform":
                    facts = code_fact(
                        link,
                        destination,
                        repo,
                        revision,
                        root,
                        deny,
                        names_checked,
                        classes,
                        exemptions,
                    )
            if isinstance(destination, Host):
                if destination.host not in probes:
                    probes[destination.host] = probe(destination.host, root, now)
                status = probes[destination.host]
            body, proper = body_for(link, destination, facts)
            word_gate(body, proper, root, link.id)
            entries.append(
                Fact(
                    id=link.id,
                    variant=variant,
                    destination=destination,
                    **facts,
                    body=body,
                    proper_names=proper,
                    probe=Probe(**status),
                    names="checked" if names_checked else "names_not_checked",
                ).model_dump()
            )
        for result in sorted(classes):
            print(link.id, f"content_{result}", file=sys.stderr)
        print(
            link.id,
            "checked" if names_checked else "names_not_checked",
            file=sys.stderr,
        )
    value = Generated(
        schema_version=1,
        revision=revision,
        collected_at=now,
        input_hashes=inputs(root),
        names="checked" if names_checked else "names_not_checked",
        tenants="checked" if tenants is not None else "not_checked",
        entries=[Fact.model_validate(e) for e in entries],
    ).model_dump()
    validate(value, root)
    return value


def audit(root=ROOT, repo=ROOT, revision="HEAD"):
    """Count distinct link IDs per class; logs contain IDs and classes only."""
    manifest = definitions(root)
    exemptions = name_exemptions(root)
    deny = deny_names()
    revision = git(repo, "rev-parse", f"{revision}^{{commit}}").strip()
    checked = (
        bool(deny)
        and git(repo, "rev-parse", "--is-shallow-repository").strip() == "false"
    )
    policy = read(root / LIB / "linked-content-policy.json")
    counts = {rule["class"]: 0 for rule in [*policy["hard"], *policy["record"]]}
    counts["name"] = 0
    exempted_matches = 0
    credential_review = {}
    for link in manifest.links:
        classes = set()
        review = {"refused_files": set()}
        for _, destination in variants(link, root):
            if isinstance(destination, Code) and destination.repo == "music-data-platform":
                _, _, _, found, matches = landing(
                    link,
                    destination,
                    repo,
                    revision,
                    root,
                    deny,
                    checked,
                    exemptions,
                    review,
                )
                classes.update(found)
                exempted_matches += matches
        if "credential" in classes:
            credential_review[link.id] = {
                "status": "refused",
                "refused_files": sorted(review["refused_files"]),
            }
        for result in sorted(classes):
            counts[result] += 1
            print(link.id, f"content_{result}", file=sys.stderr)
        print(link.id, "checked" if checked else "names_not_checked", file=sys.stderr)
    return {
        "revision": revision,
        "names": "checked" if checked else "names_not_checked",
        "links": len(manifest.links),
        "classes": counts,
        "exempted_name_matches": exempted_matches,
        "credential_review": credential_review,
    }


def validate(value, root=ROOT, now=None, image=False):
    try:
        result = Generated.model_validate(value)
        manifest = definitions(root)
        exemptions = name_exemptions(root)
        if (
            result.input_hashes != inputs(root)
        ):
            raise Refused("links", "links_inputs")
        if not image:
            instant = dt.datetime.fromisoformat(
                result.collected_at.replace("Z", "+00:00")
            )
            age = (now or dt.datetime.now(dt.timezone.utc)) - instant
            if not dt.timedelta(minutes=-5) <= age <= dt.timedelta(days=7):
                raise Refused("links", "links_stale")
        expected = {
            (l.id, variant): (l, dest)
            for l in manifest.links
            for variant, dest in variants(l, root)
        }
        actual = {(e.id, e.variant) for e in result.entries}
        if actual != set(expected) or len(actual) != len(result.entries):
            raise Refused("links", "links_entries")
        deny = deny_names()
        scan(manifest.console_tenant_review, root, deny, "links")
        for link in manifest.links:
            scan(link.model_dump(), root, deny, link.id)
            word_gate(link.highlights, [], root, link.id)
        for entry in result.entries:
            link, destination = expected[(entry.id, entry.variant)]
            if entry.destination != destination:
                raise Refused(entry.id)
            if entry.exempted_name_matches and not (
                isinstance(destination, Code)
                and destination.repo == "music-data-platform"
                and destination.path in exemptions
                and entry.object_type == "blob"
            ):
                raise Refused(entry.id)
            check_destination(destination, link, root)
            scan(entry.model_dump(), root, deny, entry.id)
            word_gate(entry.body, entry.proper_names, root, entry.id)
            if len(entry.headings) != len(link.headings) or any(
                slug(title) != heading
                and not re.fullmatch(re.escape(slug(title)) + r"-\d+", heading)
                for title, heading in zip(entry.headings, link.headings, strict=True)
            ):
                raise Refused(entry.id, "links_anchor")
            if link.line_marker and entry.line is None:
                raise Refused(entry.id, "links_line")
            if isinstance(destination, Code):
                if link.thumbnail == "folder" and (
                    entry.listing_count is None
                    or entry.listing_count < len(link.highlights)
                ):
                    raise Refused(entry.id)
                if link.thumbnail != "folder" and (
                    entry.line_count is None
                    or (entry.line is not None and entry.line > entry.line_count)
                ):
                    raise Refused(entry.id)
            if (entry.probe.state == "not_checked") != (entry.probe.checked_at is None):
                raise Refused(entry.id)
            expected_note = {
                "not_checked": "Not checked at deploy",
                "answered": "Answered at deploy",
                "no_answer": "No answer at deploy",
            }
            if entry.probe.note != expected_note[entry.probe.state]:
                raise Refused(entry.id)
            body, proper = body_for(link, destination, entry.model_dump())
            if entry.body != body or entry.proper_names != proper:
                raise Refused(entry.id)
            if isinstance(destination, Code) and entry.object_type != (
                "tree" if link.thumbnail == "folder" else "blob"
            ):
                raise Refused(entry.id, "links_object_type")
            if isinstance(destination, Code) and (
                entry.highlights != link.highlights
                or any(h not in entry.proper_names for h in entry.highlights)
            ):
                raise Refused(entry.id)
        return result
    except (ValidationError, TypeError, KeyError, OSError, ValueError) as error:
        if isinstance(error, Refused):
            raise
        raise Refused("links") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    try:
        if args.audit:
            if args.output is None:
                raise Refused("links")
            value = audit(repo=args.repo, revision=args.revision)
        else:
            value = collect(repo=args.repo, revision=args.revision)
        output = args.output or (ROOT / LIB / "links.generated.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    except (Refused, ValidationError, OSError, subprocess.SubprocessError) as error:
        refusal = error if isinstance(error, Refused) else Refused("links")
        print(refusal, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
