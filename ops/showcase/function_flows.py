"""Join reviewed function copy to declarations. Run with the functions environment."""

import argparse
import csv
import json
import os
from pathlib import Path

from mdp_functions.registry import discover
from mdp_functions.sources_doc import ANNOTATIONS, read_csv

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "control/packages/contracts/src/function-flows.generated.json"
COMMAND = "uv run --project functions python ops/showcase/function_flows.py"
HOST_MARKS = {
    "open.spotify.com": ("spotify", ""),
    "itunes.apple.com": ("apple_music", ""),
    "music.apple.com": ("apple_music", ""),
    "www.shazam.com": ("shazam", ""),
    "api.deezer.com": ("deezer", ""),
    "bandcamp.com": ("bandcamp", ""),
    "daily.bandcamp.com": ("bandcamp", ""),
    "*.bandcamp.com": ("bandcamp", ""),
    "api-v2.soundcloud.com": ("soundcloud", ""),
    "soundcloud.com": ("soundcloud", ""),
    "api.listenbrainz.org": ("listenbrainz", ""),
    "labs.api.listenbrainz.org": ("listenbrainz", ""),
    "wikimedia.org": ("wikipedia", ""),
    "www.wikidata.org": ("wikipedia", ""),
    "api.kexp.org": ("kexp", ""),
    "api.typesafe.ai": ("", "typesafe"),
}
# Explicit exceptions: external declarations without HTTP hosts, never guessed from filenames.
HOSTLESS = {
    "billboard_hot100": {"platform": "billboard", "vendor": ""},
    **{
        key: {"platform": "musicbrainz", "vendor": "", "access": "our MusicBrainz copy"}
        for key in ("mb_spine", "mb_resolve", "mb_artist_catalog")
    },
}
ACCESS = {
    "official API": "official API",
    "public page": "public page",
    "public embed/JSON": "public page",
    "dump/mirror": "our MusicBrainz copy",
    "tenant file": "file you gave us",
    "vendor export": "file you gave us",
    "warehouse": "",
}
JOB_KINDS = {"Reader", "Page reader", "Lookup", "Import", "AI step", "Housekeeping"}


def manifests():
    os.environ.pop("MDP_FIXTURE_MODE", None)
    return discover()


def annotations(root=ROOT):
    return {row["source_key"]: row for row in read_csv(root / ANNOTATIONS)}


def flow_rows(root=ROOT):
    with (root / "docs/sources/flows.csv").open(newline="") as file:
        rows = list(csv.DictReader(file))
    result = {row["source_key"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(
            "Duplicate function copy. Keep one row per key in docs/sources/flows.csv."
        )
    return result


def generate(catalog=None, rows=None, notes=None):
    catalog = manifests() if catalog is None else catalog
    rows = flow_rows() if rows is None else rows
    notes = annotations() if notes is None else notes
    if catalog.keys() != rows.keys():
        missing = sorted(catalog.keys() - rows.keys())
        extra = sorted(rows.keys() - catalog.keys())
        raise ValueError(
            f"Function copy differs: missing {missing}; unknown {extra}. Update docs/sources/flows.csv."
        )
    result = {}
    for key, manifest in sorted(catalog.items()):
        row = rows[key]
        note = notes.get(key, notes.get(key.removesuffix("_weekly"), {}))
        hosts = list(manifest.hosts)
        host_marks = [HOST_MARKS[host] for host in hosts if host in HOST_MARKS]
        exception = HOSTLESS.get(key, {})
        platforms = {p for p, _ in host_marks if p} | {exception.get("platform", "")}
        platform = row["platform"]
        if platform and (platform not in platforms or not manifest.external):
            raise ValueError(
                f"{key}: platform disagrees with declared hosts. Correct platform in docs/sources/flows.csv."
            )
        if row["job_kind"] not in JOB_KINDS or (row["job_kind"] == "AI step") != bool(
            manifest.llm_step
        ):
            raise ValueError(
                f"{key}: job kind disagrees with its declaration. Correct job_kind in docs/sources/flows.csv."
            )
        vendor = next((v for _, v in host_marks if v), exception.get("vendor", ""))
        # An operator-loaded export has a named vendor without declaring network access.
        if note.get("method") == "vendor export":
            vendor = row["vendor"]
        elif row["vendor"] != vendor:
            raise ValueError(
                f"{key}: vendor disagrees with declared hosts. Correct vendor in docs/sources/flows.csv."
            )
        method = note.get("method", "warehouse" if not manifest.external else "")
        access = vendor if method == "vendor API" else ACCESS.get(method)
        if access is None:
            raise ValueError(
                f"{key}: access method is missing. Add its method to docs/sources/annotations.csv."
            )
        access = exception.get("access", access)
        cadence = (
            manifest.targets.member_cadence if manifest.targets else None
        ) or manifest.cadence
        stages = [
            {"stage": "Watch list", "mark": "watch_list"},
            {
                "stage": "Source",
                "mark": "apple_catalog"
                if hosts == ["itunes.apple.com"]
                else platform or "postgresql",
            },
        ]
        if access:
            stages.append({"stage": "Access", "mark": vendor or "access"})
        stages.extend(
            [
                {
                    "stage": "Collection job",
                    "mark": (vendor or "litellm") if manifest.llm_step else "python",
                },
                {"stage": "Received", "mark": "postgresql"},
                {"stage": "Cleaned and matched", "mark": "dbt"},
                {"stage": "Ready to use", "mark": "postgresql"},
                {"stage": "Your number", "mark": "mdp"},
            ]
        )
        if row["show"] not in {"yes", "hidden"}:
            raise ValueError(
                f"{key}: unknown visibility. Set show to yes or hidden in docs/sources/flows.csv."
            )
        result[key] = {
            "platform": platform or None,
            "access": access or None,
            "vendor": vendor or None,
            "job_kind": row["job_kind"],
            "cadence": cadence,
            "hosts": hosts,
            "writes": list(manifest.writes),
            "stages": stages,
            "card": {
                "what": row["what_it_does"],
                "how_often": row["how_often"],
                "reads": row["reads"],
                "why": row["why_it_matters"],
                "ideas": [row[f"idea_{i}"] for i in range(1, 4) if row[f"idea_{i}"]],
                "waiting_on": row["waiting_on"],
            },
            "hidden": row["show"] == "hidden",
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(generate(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
            raise SystemExit(f"Function flows differ. Run {COMMAND}.")
        print(
            f"Function flows match. Review docs/sources/flows.csv before running {COMMAND}."
        )
    else:
        OUTPUT.write_text(rendered)


if __name__ == "__main__":
    main()
