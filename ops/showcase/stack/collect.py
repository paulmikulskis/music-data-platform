"""Collect public aggregates only. Raw deployment responses never become artifacts."""

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Probe(Strict):
    state: Literal["not_checked", "answered", "no_answer"]
    name: Literal[
        "Not checked",
        "Console HTTP health",
        "Runner state, last rounds and rebuilt tables",
    ]
    checked_at: str | None


class Fact(Strict):
    text: str
    captured_at: str
    evidence: str


class Service(Strict):
    alias: str
    kind: Literal["Server", "Database", "On a timer"]
    process_role: str
    region: Literal["New Jersey", "Not checked"]
    machine_count: int | None = Field(ge=0)
    cpu_total: int | None = Field(ge=0)
    memory_mb_total: int | None = Field(ge=0)
    storage_gb_total: int | None = Field(ge=0)
    encrypted: bool | None
    measured_at: str | None
    public_link: str | None
    status: Probe
    facts: list[Fact]


class Stack(Strict):
    schema_version: Literal[1]
    input_hashes: dict[str, str]
    captured_at: str
    services: list[Service]


def collect(root=ROOT, fetch=None, now=None):
    apps = json.loads((root / "ops/showcase/stack/apps.json").read_text())
    facts = json.loads((root / "ops/showcase/stack/facts.json").read_text())
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    services = []
    for app in apps:
        machines = fetch(app["app"], "machines") if fetch else None
        volumes = fetch(app["app"], "volumes") if fetch else None
        # Process groups are explicit. Temporary restore/import machines never count.
        selected = [
            m
            for m in machines or []
            if (app["machine_names"] and m.get("name") in app["machine_names"])
            or (
                not app["machine_names"]
                and m.get("config", {}).get("metadata", {}).get("fly_process_group")
                in app["process_groups"]
            )
        ]
        guests = [m.get("config", {}).get("guest", {}) for m in selected]
        volume_ids = {
            mount.get("volume")
            for m in selected
            for mount in m.get("config", {}).get("mounts", [])
        }
        disks = [v for v in volumes or [] if v.get("id") in volume_ids]
        measured = machines is not None
        service = Service(
            alias=app["alias"],
            kind=app["kind"],
            process_role=app["process_role"],
            region="New Jersey"
            if selected and all(m.get("region") == "ewr" for m in selected)
            else "Not checked",
            machine_count=len(selected) if measured else None,
            cpu_total=sum(g["cpus"] for g in guests)
            if measured and all(type(g.get("cpus")) is int for g in guests)
            else None,
            memory_mb_total=sum(g["memory_mb"] for g in guests)
            if measured and all(type(g.get("memory_mb")) is int for g in guests)
            else None,
            storage_gb_total=sum(v["size_gb"] for v in disks)
            if volumes is not None and all(type(v.get("size_gb")) is int for v in disks)
            else None,
            encrypted=all(v["encrypted"] for v in disks)
            if disks and all(type(v.get("encrypted")) is bool for v in disks)
            else None,
            measured_at=now if measured else None,
            public_link=app["public_link"],
            status=Probe(state="not_checked", name="Not checked", checked_at=None),
            facts=[Fact.model_validate(f) for f in facts.get(app["alias"], [])],
        )
        services.append(service)
    # Hash only normalized, sanitized measurements. Private provider metadata is never retained.
    values = [s.model_dump() for s in services]
    return Stack(
        schema_version=1,
        captured_at=now,
        input_hashes={
            "apps": digest(apps),
            "facts": digest(facts),
            "measurements": digest(values),
        },
        services=services,
    ).model_dump()


def validate(value, root=ROOT):
    stack = Stack.model_validate(value)
    apps = json.loads((root / "ops/showcase/stack/apps.json").read_text())
    facts = json.loads((root / "ops/showcase/stack/facts.json").read_text())
    assert stack.input_hashes == {
        "apps": digest(apps),
        "facts": digest(facts),
        "measurements": digest([s.model_dump() for s in stack.services]),
    }, "Stack inputs differ. Run ops/showcase/stack/collect.sh."
    assert len(stack.services) == len(apps), (
        "Stack service missing. Run ops/showcase/stack/collect.sh."
    )
    for service, app in zip(stack.services, apps, strict=True):
        for field in ("alias", "kind", "process_role", "public_link"):
            assert getattr(service, field) == app[field], (
                "Unknown display value. Review ops/showcase/stack/apps.json."
            )
        assert [f.model_dump() for f in service.facts] == facts.get(
            service.alias, []
        ), "Fact differs. Review ops/showcase/stack/facts.json."
        for fact in service.facts:
            dt.datetime.fromisoformat(fact.captured_at)
        assert service.status.state == "not_checked" or service.status.checked_at, (
            "Probe time missing. Record the probe time."
        )
    return stack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dated-only", action="store_true")
    args = parser.parse_args()

    def fetch(app, kind):
        command = (
            ["fly", "machine", "list", "-a", app, "--json"]
            if kind == "machines"
            else ["fly", "volumes", "list", "-a", app, "--json"]
        )
        return json.loads(subprocess.check_output(command, timeout=30))

    value = collect(fetch=None if args.dated_only else fetch)
    validate(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(
        "Stack facts prepared. Run python ops/showcase/artifacts.py to validate the overlay."
    )


if __name__ == "__main__":
    main()
