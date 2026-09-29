"""Build the deploy context: the archived paths at one revision (MDP_DEPLOY_REVISION, which
ops/deploy.sh pins once per run; HEAD otherwise). Nothing from the worktree enters it, so every image
of a run is the same commit. `--paths` prints the archived paths (deploy.sh refuses a modified one).
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
revision = os.environ.get("MDP_DEPLOY_REVISION", "HEAD")
# The Dockerfiles ops/deploy.sh builds from this context. Every path they COPY is archived, so an
# image never misses a file its Dockerfile names.
dockerfiles = [
    "ops/fly/core-runner/Dockerfile",
    "ops/fly/functions/Dockerfile",
    "control/apps/control-api/Dockerfile",
    "control/apps/data-api/Dockerfile",
    "control/apps/showcase/Dockerfile",
    "ops/fly/postgres/Dockerfile",
    "ops/fly/haproxy/Dockerfile",
    "ops/fly/alloy/Dockerfile",
]


def copied(dockerfile):
    """Sources of every COPY line, except copies from another stage or image."""
    for line in (root / dockerfile).read_text().splitlines():
        words = line.split()
        if words[:1] == ["COPY"] and not any(w.startswith("--from=") for w in words):
            yield from (w.rstrip("/") for w in words[1:-1] if not w.startswith("--"))


wanted = {
    ".dockerignore",
    "dbt",
    "functions",
    "control",
    "ops/fly",
    "ops/showcase",
    *(p for d in dockerfiles for p in copied(d)),
}
paths = sorted(p for p in wanted if not any(p.startswith(q + "/") for q in wanted))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path, nargs="?")
parser.add_argument("--paths", action="store_true")
parser.add_argument("--showcase", action="store_true")
args = parser.parse_args()
if args.paths:
    print("\n".join(paths))
    sys.exit(0)
if args.destination is None:
    parser.error("Pass a temporary context directory.")
destination = args.destination.resolve()
destination.mkdir(parents=True, exist_ok=True)
archive = subprocess.check_output(["git", "archive", revision, *paths], cwd=root)
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(destination, filter="data")
overlay = os.environ.get("MDP_SHOWCASE_OVERLAY")
if overlay or args.showcase:
    pinned = subprocess.check_output(
        ["git", "rev-parse", revision], cwd=root, text=True
    ).strip()
    # Collector and validator output can name private facts, so it goes to a user-only log
    # outside the build context, and the deploy prints only where to read it.
    handle, log_path = tempfile.mkstemp(prefix="mdp-showcase-artifacts-", suffix=".log")
    with os.fdopen(handle, "w") as log:
        try:
            # Both collection and validation use code and facts from the selected archive.
            # The temporary overlay never enters the worktree or the image context.
            with tempfile.TemporaryDirectory(
                prefix=".showcase-overlay-", dir=destination
            ) as scratch:
                if overlay:
                    overlay_path = Path(scratch)
                    shutil.copyfile(
                        Path(overlay).resolve() / "stack.generated.json",
                        overlay_path / "stack.generated.json",
                    )
                else:
                    overlay_path = Path(scratch)
                    subprocess.run(
                        [
                            "bash",
                            str(destination / "ops/showcase/stack/collect.sh"),
                            "--dated-only",
                            "--output",
                            str(overlay_path / "stack.generated.json"),
                        ],
                        check=True,
                        cwd=destination,
                        stdout=log,
                        stderr=log,
                    )
                subprocess.run(
                    [
                        "uv",
                        "run",
                        "--project",
                        str(destination / "functions"),
                        "python",
                        str(destination / "ops/showcase/links/collect.py"),
                        "--repo",
                        str(root),
                        "--revision",
                        pinned,
                        "--output",
                        str(overlay_path / "links.generated.json"),
                    ],
                    check=True,
                    cwd=destination,
                    stdout=log,
                    stderr=log,
                )
                subprocess.run(
                    [
                        "uv",
                        "run",
                        "--project",
                        str(destination / "functions"),
                        "python",
                        str(destination / "ops/showcase/artifacts.py"),
                        "--context",
                        str(destination),
                        "--overlay",
                        str(overlay_path),
                        "--revision",
                        pinned,
                    ],
                    check=True,
                    cwd=destination,
                    stdout=log,
                    stderr=log,
                )
        except (OSError, subprocess.SubprocessError):
            print(
                f"Showcase artifact preparation failed. Read {log_path}, then follow "
                "ops/showcase/README.md#recover.",
                file=sys.stderr,
            )
            sys.exit(1)
    os.unlink(log_path)
print(destination)
