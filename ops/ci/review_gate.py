#!/usr/bin/env python3
"""Validate source attribution for every served mart after dbt parse or build."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "music_data_platform"


def lineage(manifest: dict, model: str) -> set[str] | None:
    """The source keys that write any raw table upstream of `model`; None when the manifest has no such model."""
    start = f"model.{PROJECT}.{model}"
    if start not in manifest["nodes"]:
        return None
    writers: set[str] = set()
    queue, seen = [start], set()
    while queue:
        node = queue.pop()
        if node in seen:
            continue
        seen.add(node)
        if node in manifest["sources"]:
            writers |= set(manifest["sources"][node].get("meta", {}).get("writers") or [])
        elif node in manifest["nodes"]:
            queue.extend(manifest["nodes"][node].get("depends_on", {}).get("nodes", []))
    return writers


# Quoted post-hooks can contain their own {{ ... }}. Only an unquoted delimiter
# closes the outer Jinja expression; its commas and strings stay out of SQL parsing.
JINJA_STRING = r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""
SQL_TOKEN = re.compile(
    r"--[^\n]*|/\*.*?\*/|\{#.*?#\}|"
    rf"\{{\{{(?:{JINJA_STRING}|(?!\}}\}})[^'\"])*\}}\}}|"
    rf"\{{%(?:{JINJA_STRING}|(?!%\}})[^'\"])*%\}}|"
    r"'(?:''|[^'])*'|[a-zA-Z_][a-zA-Z_0-9]*|[^\s]",
    re.DOTALL,
)


def source_key_expressions(code: str) -> tuple[set[str], bool]:
    """Literal array entries assigned to _source_keys, and whether row-derived keys are present.

    Inspect SELECT items, including CASE branches and casts, without treating unrelated JSON
    evidence arrays or comments as rights keys. dbt parse supplies raw_code, so Jinja stays opaque.
    """
    tokens = [t for t in SQL_TOKEN.findall(code) if not t.startswith(("--", "/*", "{#", "{%"))]
    keys: set[str] = set()
    derived = False
    starts = [0]
    for i, token in enumerate(tokens):
        lower = token.lower()
        if lower == "_source_keys" and (i == 0 or tokens[i - 1].lower() != "as"):
            derived = True
        if lower == "as" and i + 1 < len(tokens) and tokens[i + 1].lower() == "_source_keys":
            for part in tokens[starts[-1]:i]:
                if part.lower() in {"source_keys", "source_key", "_source_key", "_source_keys"}:
                    derived = True
                if part.startswith("{{") and re.search(r"\bmdp_source_keys(?:_agg)?\s*\(", part):
                    derived = True
                for value in re.findall(r"'(?:''|[^'])*'", part):
                    try:
                        array = json.loads(value[1:-1].replace("''", "'"))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(array, list):
                        keys.update(key for key in array if isinstance(key, str))
        if token == "(":
            starts.append(i + 1)
        elif token == ")" and len(starts) > 1:
            starts.pop()
        elif token == "," or lower == "select":
            starts[-1] = i + 1
    return keys, derived


def complete_annotation(manifest: dict, node: dict) -> set[str] | None:
    """Writers guaranteed at the final annotation, independent of row joins and CASE branches.

    Parsing can establish the canonical macro call; a built manifest must also contain its
    compiled floor. A mere aggregation/forwarding expression is not a completeness proof.
    """
    tokens = [t for t in SQL_TOKEN.findall(node.get("raw_code", ""))
              if not t.startswith(("--", "/*", "{#", "{%")) and t != ";"]
    if not tokens or not re.fullmatch(r"\{\{\s*mdp_annotate\(.*\)\s*\}\}", tokens[-1], re.DOTALL):
        return None
    macro = manifest.get("macros", {}).get(f"macro.{PROJECT}.mdp_annotate", {}).get("macro_sql", "")
    # The annotation owns the union and the rights join; callers cannot disable the writer floor.
    required = ("mdp_upstream_writers(model.unique_id)", "as _mdp_lineage_writers",
                "mdp_lineage._mdp_lineage_writers", "cast(mdp_flags.keys as text) as source_keys")
    if not all(part in macro for part in required):
        return None
    compiled = node.get("compiled_code")
    if compiled is None:
        return lineage(manifest, node["name"]) or set()
    floors = re.findall(r"'((?:''|[^'])*)'\s+as\s+_mdp_lineage_writers\b", compiled, re.IGNORECASE)
    if not floors or "cast(mdp_flags.keys as text) as source_keys" not in compiled:
        return set()
    return set.intersection(*(set(json.loads(value.replace("''", "'"))) for value in floors))


def served_mart_errors(manifest: dict) -> list[str]:
    """Check every served contract, including tenant marts, using the same lineage as the rule gate."""
    errors = []
    for node in manifest["nodes"].values():
        if node.get("resource_type") != "model" or not node.get("config", {}).get("meta", {}).get("grain"):
            continue
        name = node["name"]
        writers = lineage(manifest, name) or set()
        code = node.get("raw_code", "")
        keys, derived = source_key_expressions(code)
        # Some marts annotate a ref directly rather than a local SELECT.
        for parent in re.findall(r"mdp_annotate\s*\(\s*ref\s*\(\s*['\"]([^'\"]+)['\"]", code):
            upstream = manifest["nodes"].get(f"model.{PROJECT}.{parent}", {})
            parent_keys, parent_derived = source_key_expressions(upstream.get("raw_code", ""))
            keys |= parent_keys
            derived |= parent_derived
        for key in sorted(keys - writers):
            errors.append(f"{name}: literal _source_keys entry {key!r} is not an upstream raw writer")
        guaranteed = complete_annotation(manifest, node)
        if guaranteed is None:
            guaranteed = set()
        missing = writers - guaranteed
        if missing:
            errors.append(f"{name}: source keys can omit upstream writers: {', '.join(sorted(missing))}; "
                          "use the final mdp_annotate writer floor")
        if writers and not keys and not derived and not guaranteed:
            errors.append(f"{name}: upstream raw writers exist but the mart carries no source keys")
    return errors


def main(argv: list[str]) -> int:
    manifest_path = Path(argv[0]) if argv else ROOT / "dbt/target/manifest.json"
    if not manifest_path.exists():
        print(f"::error title=review gate::{manifest_path} is missing; run dbt parse first")
        return 2
    manifest = json.loads(manifest_path.read_text())
    errors = served_mart_errors(manifest)
    for error in errors:
        print(f"::error title=mart lineage::{error}")
    if not errors:
        print("PASS mart lineage: every served mart guarantees all upstream writers; literals name upstream writers")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
