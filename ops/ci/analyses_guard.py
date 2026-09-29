#!/usr/bin/env python3
"""Refuse data, credentials and executed notebooks in analysis source folders."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ("analyses/", "r/mdpr/inst/templates/")
DATA_SUFFIXES = {".duckdb", ".parquet", ".feather", ".rds", ".rdata", ".rda"}
SECRET_NAMES = {".renviron", ".pgpass", "pgpass.conf"}


def offenders(root=ROOT):
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    for name in sorted(set(names)):
        if not name.startswith(PREFIXES):
            continue
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink():
            yield name, "replace the symlink with source; data must stay outside git"
            continue
        if path.suffix.lower() in DATA_SUFFIXES or path.name.lower() in SECRET_NAMES:
            yield name, "untrack this file and add it to the analysis .gitignore"
            continue
        size = path.stat().st_size
        if size > 5_000_000 or (path.suffix.lower() == ".csv" and size > 1_000_000):
            yield name, "move this data outside git; commit code that produces it"
            continue
        if path.suffix.lower() == ".ipynb":
            try:
                notebook = json.loads(path.read_text())
                cells = notebook["cells"]
                if any(
                    cell.get("outputs") or cell.get("execution_count") is not None
                    for cell in cells
                ):
                    yield (
                        name,
                        "clear notebook outputs and execution counts before committing",
                    )
            except (ValueError, KeyError, TypeError, AttributeError):
                yield name, "repair the notebook JSON and clear its outputs"


def main():
    failures = list(offenders())
    for name, fix in failures:
        print(f"FAIL {name}: {fix}")
    if not failures:
        print("PASS analyses guard")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
