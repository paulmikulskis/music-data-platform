"""Generate the structural graph. Run with the functions environment after dbt parse."""

import argparse
import csv
import hashlib
import inspect
import json
from pathlib import Path

from mdp_functions.registry import discover

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path("control/apps/showcase/lib/lineage.generated.json")
GLOBAL_SCHEMAS = {"raw", "staging", "intermediate", "marts", "reference"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(
            f"{message}. Read ops/showcase/README.md and regenerate the artifacts."
        )


def normalized_manifest(manifest):
    result = {}
    for uid, node in sorted({**manifest["sources"], **manifest["nodes"]}.items()):
        if node["resource_type"] not in {"source", "seed", "model"}:
            continue
        config = node.get("config", {})
        # Explicit fields omit adapter credentials, invocation ids, timestamps and local paths.
        result[uid] = {
            "relation": f"{node['schema']}.{node.get('identifier') or node.get('alias') or node['name']}",
            "kind": node["resource_type"],
            "file": "dbt/" + node["original_file_path"],
            "tags": sorted(node.get("tags", [])),
            "materialized": config.get("materialized", "source"),
            "depends_on": sorted(node.get("depends_on", {}).get("nodes", [])),
            "columns": sorted(node.get("columns", {})),
            "code": node.get("raw_code", "").replace("\r\n", "\n"),
        }
    return result


def declarations(root):
    result = {}
    for key, item in sorted(discover().items()):
        file = inspect.getsourcefile(item.function) if item.function else None
        result[key] = {
            "reads": sorted(item.reads),
            "writes": sorted(item.writes),
            "cadence": item.cadence,
            "tenant_bound": item.tenant_bound,
            "retain_days": item.retain_days,
            "hosts": sorted(item.hosts),
            "kind": item.kind,
            "schema": item.public()["schema_mode"],
            "file": str(Path(file).relative_to(root)) if file else None,
        }
    return result


def collapse(nodes, edges):
    """Walk declared edges through hidden nodes, retaining every hidden step (groundwork algorithm)."""
    visible = {n["id"] for n in nodes if n["tier"] == 1}
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
    found = []
    for start in sorted(visible):
        pending = [(n, []) for n in sorted(adjacency.get(start, []))]
        visited = set()
        while pending:
            end, via = pending.pop(0)
            if end == start or end in visited:
                continue
            visited.add(end)
            if end in visible:
                found.append({"from": start, "to": end, "via": via})
            else:
                pending.extend((n, [*via, end]) for n in sorted(adjacency.get(end, [])))
    return sorted(found, key=encoded)


def generate(root=ROOT, manifest=None):
    source_manifest = manifest or read(root / "dbt/target/manifest.json")
    manifest = normalized_manifest(source_manifest)
    registry = declarations(root)
    config = {
        name: read(root / f"ops/showcase/lineage/{name}.json")
        for name in ("entries", "peek", "labels")
    }
    inputs = {
        **config,
        "manifest": manifest,
        "registry": registry,
        "queries": sorted(read(root / "ops/showcase/queries.json"), key=encoded),
    }
    for name in ("rights_registry",):
        with (root / f"dbt/seeds/{name}.csv").open() as source:
            inputs[name] = sorted(csv.DictReader(source), key=encoded)
    # A projection's review pins the actual SQL files, not a hand-written claim about field safety.
    reviews = config["peek"]["reviews"]
    for file, expected in reviews.items():
        require((root / file).is_file(), "Missing projection review file")
        require(
            hashlib.sha256((root / file).read_bytes()).hexdigest() == expected,
            f"Projection review changed: {file}",
        )
    # Every model in a projection's dependency closure has an explicit SQL review hash.
    by_relation = {node["relation"]: uid for uid, node in manifest.items()}
    for relation, projection in config["peek"]["projections"].items():
        pending = [by_relation[relation]]
        visited = set()
        while pending:
            uid = pending.pop()
            if uid in visited or uid not in manifest:
                continue
            visited.add(uid)
            node = manifest[uid]
            if node["kind"] == "model" and not Path(node["file"]).stem.startswith(
                ("bronze_", "gold_invoke__", "silver_invoke__", "universal_invoke__")
            ):
                require(node["file"] in reviews, "Projection dependency needs review")
                pending.extend(node["depends_on"])
    inputs["reviewed_code"] = reviews
    inputs["projection_policy"] = (
        root / "ops/showcase/lineage/permissions.py"
    ).read_text()
    inputs["macros"] = {
        key: item["macro_sql"].replace("\r\n", "\n")
        for key, item in sorted(source_manifest.get("macros", {}).items())
    }
    models = {
        uid: n
        for uid, n in manifest.items()
        if n["relation"].split(".")[0] in GLOBAL_SCHEMAS
        and "scope:tenant" not in n["tags"]
        and not Path(n["file"]).stem.startswith(
            ("bronze_", "gold_invoke__", "silver_invoke__", "universal_invoke__")
        )
    }
    relations = {n["relation"]: n for n in models.values()}
    ids = {uid: "rel:" + n["relation"] for uid, n in models.items()}
    edges = {
        (ids[dep], ids[uid], "transform")
        for uid, n in models.items()
        for dep in n["depends_on"]
        if dep in ids
    }
    labels = config["labels"]
    nodes = []
    for relation, n in sorted(relations.items()):
        require((root / n["file"]).is_file(), "Missing model file")
        label = labels.get("rel:" + relation)
        nodes.append(
            {
                "id": "rel:" + relation,
                "kind": "relation",
                "relation": relation,
                "file": n["file"],
                "tier": 1 if label else 2,
                "label": label["label"] if label else relation,
                "short_label": label["short_label"] if label else None,
                "stage": label["stage"] if label else relation.split(".")[0],
                "family": label["family"] if label else None,
                "cadence": next(
                    (t[8:] for t in n["tags"] if t.startswith("cadence:")), None
                ),
            }
        )
    for key, fn in registry.items():
        if fn["tenant_bound"] or not any(w in relations for w in fn["writes"]):
            continue
        nid = "fn:" + key
        label = labels.get(nid)
        nodes.append(
            {
                "id": nid,
                "kind": "function",
                "relation": None,
                "file": fn["file"],
                "tier": 1 if label else 2,
                "label": label["label"] if label else key,
                "short_label": label["short_label"] if label else None,
                "stage": "readers",
                "family": label["family"] if label else None,
                "cadence": fn["cadence"],
            }
        )
        edges.update((nid, "rel:" + w, "write") for w in fn["writes"] if w in relations)
        edges.update(("rel:" + r, nid, "read") for r in fn["reads"] if r in relations)
    require(
        all(0 < len(v["short_label"]) <= 18 for v in labels.values()),
        "Short label exceeds 18 characters",
    )
    # Retention taints whole descendants conservatively, including function read/write bridges.
    tainted = {
        "rel:" + w for f in registry.values() if f["retain_days"] for w in f["writes"]
    }
    while True:
        extended = tainted | {b for a, b, _ in edges if a in tainted}
        if extended == tainted:
            break
        tainted = extended
    peeks = config["peek"]["projections"]
    no_preview = config["peek"]["no_preview"]
    visible_relations = {
        n["relation"] for n in nodes if n["kind"] == "relation" and n["tier"] == 1
    }
    require(
        visible_relations <= set(peeks) | set(no_preview),
        "Visible relation needs a projection review",
    )
    require(
        all(bool(reason) for reason in no_preview.values()), "No-preview reason missing"
    )
    for relation, projection in peeks.items():
        require(
            relation in relations and "rel:" + relation not in tainted,
            "Unsafe peek relation",
        )
        fields = projection["columns"] + projection["order"] + projection["filters"]
        require(
            1 <= len(projection["columns"]) <= 4,
            "Peek needs one to four result columns",
        )
        require(
            set(fields) <= set(relations[relation]["columns"]), "Unknown peek field"
        )
        require(
            set(fields) == set(projection["provenance"]), "Missing field provenance"
        )
        require(
            not any(
                any(
                    word in c
                    for word in ("owner", "creator", "comment", "description", "tenant")
                )
                for c in fields
            ),
            "Private peek field",
        )
        for origin in projection["provenance"].values():
            require(
                origin["file"] in reviews and bool(origin["reason"]),
                "Unreviewed field provenance",
            )
        require(
            projection["public_links"] == [],
            "Review public link fields before adding them",
        )
        require(
            projection["policy"] in {"public_metadata"},
            "Unknown projection policy",
        )
        require("handle" not in fields, "Personal handles are not a public projection")
        for guard in projection["guards"]:
            require(
                guard["relation"].removeprefix("explore_") in relations
                and "rel:" + guard["relation"].removeprefix("explore_") not in tainted
                and guard["file"] in reviews,
                "Unreviewed projection guard",
            )
            require(
                not any(
                    word in field
                    for field in guard["columns"]
                    for word in ("owner", "creator", "comment", "tenant")
                ),
                "Private guard field",
            )
    for node in nodes:
        if node["kind"] == "relation":
            node["preview"] = (
                "reviewed"
                if node["relation"] in peeks
                else "No preview is available. See source details."
            )
        else:
            node["preview"] = "No preview is available. See source details."
    entries = config["entries"]
    require(len({e["id"] for e in entries}) == len(entries), "Duplicate fact entry")
    for entry in entries:
        require(entry["relation"] in relations, "Unknown fact relation")
        require((root / entry["code"]).is_file(), "Missing fact code")
        require(bool(entry["selectors"]), "Fact needs evidence selectors")
        for selector in entry["selectors"]:
            require(selector["relation"] in relations, "Unknown evidence relation")
            require(
                set(selector["row_keys"])
                <= set(relations[selector["relation"]]["columns"]),
                "Unknown evidence key",
            )
    for entry in entries:
        nodes.append(
            {
                "id": "entry:" + entry["id"],
                "kind": "fact",
                "relation": entry["relation"],
                "file": entry["code"],
                "tier": 1,
                "label": labels["entry:" + entry["id"]]["label"],
                "short_label": labels["entry:" + entry["id"]]["short_label"],
                "stage": "screen",
                "family": None,
                "cadence": None,
                "preview": "No preview is available. See source details.",
            }
        )
        edges.add(("rel:" + entry["relation"], "entry:" + entry["id"], "serves"))
        for selector in entry["selectors"]:
            edges.add(
                ("rel:" + selector["relation"], "entry:" + entry["id"], "evidence")
            )
    # Provider identity comes from the rights register, not a parallel curated source inventory.
    rights = {r["source_key"]: r for r in inputs["rights_registry"]}
    providers = {}
    for node in nodes:
        if node["kind"] != "function":
            continue
        key = node["id"][3:]
        provider = rights.get(key, {}).get("provider")
        if not provider:
            continue
        pid = "source:" + provider
        if pid not in providers:
            providers[pid] = {
                "id": pid,
                "kind": "source",
                "relation": None,
                "file": "dbt/seeds/rights_registry.csv",
                "tier": 1,
                "label": labels.get(pid, {}).get("label", provider),
                "short_label": labels.get(pid, {}).get("short_label", provider),
                "stage": "source",
                "family": None,
                "cadence": None,
                "preview": "No preview is available. See source details.",
            }
        edges.add((pid, node["id"], "declares"))
    nodes.extend(providers.values())
    require(set(labels) <= {n["id"] for n in nodes}, "Unknown visible label")
    require(
        all(
            n["short_label"] and len(n["short_label"]) <= 18
            for n in nodes
            if n["tier"] == 1
        ),
        "Visible node needs a short label",
    )
    result_edges = [{"from": a, "to": b, "kind": k} for a, b, k in sorted(edges)]
    return {
        "schema_version": 1,
        "input_hashes": {k: digest(v) for k, v in sorted(inputs.items())},
        "meaning": "Can feed this",
        "nodes": sorted(nodes, key=lambda n: n["id"]),
        "edges": result_edges,
        "simple_edges": collapse(nodes, result_edges),
        "entries": entries,
        "peeks": peeks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = json.dumps(generate(), indent=2, sort_keys=True) + "\n"
    path = ROOT / OUTPUT
    if args.check:
        require(path.exists() and path.read_text() == value, "Lineage drift")
    else:
        path.write_text(value)
    print("Lineage matches its inputs. Open ops/showcase/README.md.")


if __name__ == "__main__":
    main()
