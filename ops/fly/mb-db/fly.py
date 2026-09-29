#!/usr/bin/env python3
"""Task-scoped Fly CLI. Require org and verify ownership for app-scoped commands."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

FLY = str(Path.home() / ".fly" / "bin" / "flyctl")
ORG = os.environ.get("FLY_ORG", "example-org")
# The mirror and its importer; nothing else. There is no replicator.
ALLOWED = {"mdp-mb-db", "mdp-mb-import"}


def run(args, **kwargs):
    args = list(args)
    if "--org" not in args or args[args.index("--org") + 1] != ORG:
        raise SystemExit("Explicit --org example-org required")
    if args[:2] == ["apps", "create"] and args[2] not in ALLOWED:
        raise SystemExit("App outside the MusicBrainz mirror scope")
    env = os.environ.copy()
    match = re.search(
        r'^access_token:\s*[\'"]?([^\'"\s]+)',
        Path.home().joinpath(".fly/config.yml").read_text(),
        re.MULTILINE,
    )
    if not match:
        raise SystemExit("Fly access_token missing")
    env["FLY_ACCESS_TOKEN"] = match.group(1)
    for flag in ("-a", "--app"):
        if flag in args:
            app = args[args.index(flag) + 1]
            if app not in ALLOWED:
                raise SystemExit("App outside the MusicBrainz mirror scope")
            apps = json.loads(
                subprocess.check_output(
                    [FLY, "apps", "list", "--org", ORG, "--json"], env=env
                )
            )
            if not any(a.get("Name", a.get("name")) == app for a in apps):
                raise SystemExit("App not in example-org")
    # Several app-scoped flyctl subcommands do not accept --org. Ownership was
    # checked above; retain --org on commands that actually support it.
    grouped = args[0] in {
        "apps",
        "machine",
        "machines",
        "volumes",
        "secrets",
        "ips",
        "ssh",
        "config",
    }
    help_text = subprocess.check_output(
        [FLY, *args[: 2 if grouped else 1], "--help"], env=env, text=True
    )
    if "--org " not in help_text:
        pos = args.index("--org")
        del args[pos : pos + 2]
    return subprocess.run(
        [FLY, *args], env=env, check=kwargs.pop("check", False), **kwargs
    )


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]).returncode)
