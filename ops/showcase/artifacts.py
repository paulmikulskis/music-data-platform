"""Validate and install a hash-bound showcase overlay in an archived build context."""

import argparse
import datetime as dt
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = Path("control/apps/showcase/lib")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


lineage_module = module("lineage_generator", ROOT / "ops/showcase/lineage/generate.py")
stack_module = module("stack_collector", ROOT / "ops/showcase/stack/collect.py")
links_module = module("links_collector", ROOT / "ops/showcase/links/collect.py")
digest = lineage_module.digest
read = lineage_module.read
require = lineage_module.require


def recent(value, now):
    instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(
        instant.tzinfo is not None
        and dt.timedelta(minutes=-5) <= now - instant <= dt.timedelta(days=7),
        "Artifact is stale or dated in the future",
    )


def validate(root, lineage, stack, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    require(
        lineage == read(root / LIB / "lineage.generated.json"),
        "Lineage differs from the selected revision",
    )
    stack_module.validate(stack, root)
    recent(stack["captured_at"], now)
    for service in stack["services"]:
        if service["measured_at"]:
            recent(service["measured_at"], now)


def install(root, overlay, revision):
    require(
        re.fullmatch("[a-f0-9]{40}", revision) is not None,
        "Build revision must be a full commit",
    )
    lineage = read(root / LIB / "lineage.generated.json")
    stack = read(overlay / "stack.generated.json")
    validate(root, lineage, stack)
    links = read(overlay / "links.generated.json")
    links_module.validate(links, root)
    require(
        links["revision"] == revision,
        "Link revision differs. Collect the pinned revision.",
    )
    target = root / LIB
    shutil.copyfile(overlay / "stack.generated.json", target / "stack.generated.json")
    shutil.copyfile(overlay / "links.generated.json", target / "links.generated.json")
    envelope = {
        "schema_version": 1,
        "revision": revision,
        "lineage_hash": digest(lineage),
        "stack_hash": digest(stack),
        "links_hash": digest(links),
        "lineage_inputs": lineage["input_hashes"],
        "stack_inputs": stack["input_hashes"],
        "links_inputs": links["input_hashes"],
    }
    (target / "artifacts.build.json").write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n"
    )
    return envelope


def check_image(root, payload, revision):
    lineage = read(payload / "lineage.generated.json")
    stack = read(payload / "stack.generated.json")
    envelope = read(payload / "artifacts.build.json")
    links = read(payload / "links.generated.json")
    links_module.validate(links, root, image=True)
    validate(root, lineage, stack)
    require(
        envelope
        == {
            "schema_version": 1,
            "revision": revision,
            "lineage_hash": digest(lineage),
            "stack_hash": digest(stack),
            "links_hash": digest(links),
            "lineage_inputs": lineage["input_hashes"],
            "stack_inputs": stack["input_hashes"],
            "links_inputs": links["input_hashes"],
        },
        "Image envelope differs",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--image-payload", type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.image_payload:
        check_image(args.context, args.image_payload, args.revision)
    else:
        require(args.overlay is not None, "Overlay missing")
        install(args.context, args.overlay, args.revision)
    print("Artifacts match the selected revision. Build the showcase image.")


if __name__ == "__main__":
    try:
        main()
    except links_module.Refused as error:
        print(error, file=sys.stderr)
        sys.exit(1)
