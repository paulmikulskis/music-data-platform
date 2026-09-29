"""Limit evidence growth. Run python3 ops/ci/evidence_size.py --help."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "ops/evidence/"
ALLOWLIST = ROOT / "ops/ci/evidence-size-allowlist.json"
FILE_LIMIT = 1_000_000
RANGE_LIMIT = 5_000_000
GUIDE = "ops/ci/README.md#evidence-size"


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def tree(root, ref):
    files = {}
    for entry in git(root, "ls-tree", "-rlz", ref, "--", PREFIX).split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            _mode, kind, oid, size = metadata.split()
            if kind == b"blob":
                files[os.fsdecode(path)] = (oid.decode(), int(size))
    return files


def working_tree(root):
    paths = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        PREFIX,
    )
    files = {}
    for name in set(paths.split(b"\0")) - {b""}:
        path = root / os.fsdecode(name)
        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
        elif path.is_file():
            content = path.read_bytes()
        else:
            continue
        # Match Git blob IDs without adding the files to Git's object store.
        digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content)
        files[os.fsdecode(name)] = (digest.hexdigest(), len(content))
    return files


def allowances(root, path=ALLOWLIST):
    data = json.loads(path.read_text())
    limits = data["files"]
    if not limits:
        return {}
    baseline = tree(root, data["baseline"])
    for name, size in limits.items():
        if (
            type(size) is not int
            or size <= FILE_LIMIT
            or baseline.get(name, (None, None))[1] != size
        ):
            raise ValueError(f"Invalid size for {name} in {path.name}")
    return limits


def inspect(before, after, limits, *, seen=None, growth=None, check_budget=True):
    failures = []
    if growth is None:
        growth = {}
    if seen is None:
        seen = {(name, oid) for name, (oid, _) in before.items()}
    kept = []
    for name, (oid, size) in sorted(after.items()):
        old_oid, old_size = before.get(name, (None, 0))
        if oid == old_oid:
            continue
        cap = max(FILE_LIMIT, min(limits.get(name, 0), old_size))
        if size > cap:
            failures.append(
                f"{name}: {size:,} bytes exceeds {cap:,} bytes. Crop or move this file to scratch; read {GUIDE}."
            )
        elif size > FILE_LIMIT:
            kept.append(f"{name}: {size:,} bytes; existing limit {cap:,} bytes.")
        if (name, oid) not in seen:
            growth[name] = growth.get(name, 0) + size
            seen.add((name, oid))
    added = sum(growth.values())
    if check_budget and added > RANGE_LIMIT:
        failures.append(
            f"Changed evidence versions add {added:,} bytes; the range limit is {RANGE_LIMIT:,} bytes. Keep a short summary and move large files to scratch; read {GUIDE}."
        )
        failures.extend(
            f"  {name}: +{size:,} bytes. Trim this file and rerun the check."
            for name, size in sorted(
                growth.items(), key=lambda item: (-item[1], item[0])
            )
        )
    return failures, added, kept


def scan(root, base, head, limits, *, local=False):
    empty = bool(base) and set(base) == {"0"}
    before = {} if empty else tree(root, base)
    seen = {(name, oid) for name, (oid, _) in before.items()}
    growth = {}
    failures = []
    kept = []
    revisions = (
        git(root, "rev-list", "--reverse", head, *([] if empty else ["^" + base]))
        .decode()
        .splitlines()
    )
    for revision in revisions:
        parents = (
            git(root, "rev-list", "--parents", "-n", "1", revision).decode().split()
        )
        prior = tree(root, parents[1]) if len(parents) > 1 else {}
        errors, _, allowed = inspect(
            prior,
            tree(root, revision),
            limits,
            seen=seen,
            growth=growth,
            check_budget=False,
        )
        # The total budget is reported once, after every version has been counted.
        failures.extend(errors)
        kept.extend(allowed)
    final = working_tree(root) if local else tree(root, head)
    errors, added, allowed = inspect(before, final, limits, seen=seen, growth=growth)
    failures.extend(errors)
    kept.extend(allowed)
    return list(dict.fromkeys(failures)), added, list(dict.fromkeys(kept))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", help="Start commit; default: merge base of main and HEAD"
    )
    parser.add_argument(
        "--head",
        help="End commit; default: working files, including untracked evidence",
    )
    args = parser.parse_args()
    try:
        base = args.base or git(ROOT, "merge-base", "main", "HEAD").decode().strip()
        failures, added, kept = scan(
            ROOT, base, args.head or "HEAD", allowances(ROOT), local=not args.head
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(
            f"Cannot check evidence sizes ({type(exc).__name__}). Fetch full Git history and check the allowlist; read {GUIDE}."
        )
        return 1
    for line in kept:
        print(f"ALLOW {line} See {GUIDE} before replacing it.")
    for line in failures:
        print(f"FAIL {line}")
    if not failures:
        print(
            f"PASS evidence size: {added:,} added bytes (limit {RANGE_LIMIT:,}). Next: run bash ops/ready.sh."
        )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
