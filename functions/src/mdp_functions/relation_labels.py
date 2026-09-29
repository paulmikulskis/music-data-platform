"""Relation labels derived from dbt lineage, declarations and registered rights."""

import csv
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CATEGORIES = [
    "public platform data",
    "tenant monitoring",
    "vendor-licensed",
    "tenant-private",
    "personal",
]
UNKNOWN = {
    "layer": "bronze",
    "category": "personal",
    "tenant": "unknown",
    "grain": [],
    "learning": False,
    "resale": False,
    "licence_status": "unverified",
    "description": "Unclassified relation; review before reuse.",
}

# Frozen membership metadata contains warehouse IDs and close stamps, never target values.
# Keep every other control mirror field hidden in the generated safe copies.
HISTORY_METADATA = {
    "raw.cycles": {
        "id",
        "cadence",
        "scope",
        "opened_at",
        "closed_at",
        "status",
        "manifest_mode",
        "close_no",
    },
    "raw.dump_stamps": {"dump_id", "scope", "close_no", "target_table"},
    "raw.cycle_inputs": {"cycle_id", "dump_id"},
    "raw._load_receipts": {
        "dump_id",
        "target_table",
        "rows",
        "rows_deduped",
        "committed_at",
    },
}

# Row restrictions accompany the reviewed columns in every generated projection.
HISTORY_FILTERS = {
    "raw.cycles": "src.scope = 'global'",
    "raw.dump_stamps": "src.scope = 'global'",
    "raw.cycle_inputs": (
        "EXISTS (SELECT 1 FROM raw.cycles owner "
        "WHERE owner.id = src.cycle_id AND owner.scope = 'global')"
    ),
    "raw._load_receipts": (
        "EXISTS (SELECT 1 FROM raw.dump_stamps stamp "
        "WHERE stamp.dump_id = src.dump_id AND stamp.scope = 'global') "
        "OR EXISTS (SELECT 1 FROM raw.cycle_inputs member "
        "JOIN raw.cycles owner ON owner.id = member.cycle_id "
        "WHERE member.dump_id = src.dump_id AND owner.scope = 'global')"
    ),
}
HISTORY_FILTER_RELATIONS = {
    "raw.cycle_inputs": ["raw.cycles"],
    "raw._load_receipts": ["raw.cycles", "raw.cycle_inputs", "raw.dump_stamps"],
}


def combine(labels):
    labels = list(labels)
    if not labels:
        return dict(UNKNOWN)
    licences = sorted(
        {
            licence
            for value in labels
            for licence in value.get(
                "licences", [value.get("licence_status") or "unverified"]
            )
        }
    )
    return {
        "licences": licences,
        "layer": max(
            (v["layer"] for v in labels),
            key=["bronze", "silver", "gold", "sandbox"].index,
        ),
        "category": max((v["category"] for v in labels), key=CATEGORIES.index),
        "tenant": "global"
        if all(v["tenant"] == "global" for v in labels)
        else "tenant",
        "grain": [],
        "learning": all(v.get("learning") is True for v in labels),
        "resale": all(v.get("resale") is True for v in labels),
        "licence_status": ", ".join(licences),
        "description": "",
    }


def generate(manifest, root=REPO):
    from mdp_functions.registry import discover
    from mdp_functions.shared_privacy import policies

    shared = policies(root)
    declarations = discover()
    rights = {
        r["source_key"]: r
        for r in csv.DictReader((root / "dbt/seeds/rights_registry.csv").open())
    }
    nodes = {**manifest["sources"], **manifest["nodes"]}
    cache = {}

    def label(uid, visiting=frozenset()):
        if uid in cache:
            return cache[uid]
        if uid in visiting or uid not in nodes:
            return dict(UNKNOWN)
        node = nodes[uid]
        config = node.get("config", {})
        tags = node.get("tags", config.get("tags", []))
        columns = node.get("columns", {})
        meta = {**node.get("meta", {}), **config.get("meta", {})}
        writers = meta.get("writers", [])
        inputs = [
            label(p, visiting | {uid})
            for p in node.get("depends_on", {}).get("nodes", [])
            if p in nodes
        ]
        for key in writers:
            r = rights.get(key, {})
            declaration = declarations.get(key)
            licence = r.get("license_ref") or "unverified"
            category = (
                "public platform data"
                if licence.startswith(("public-", "CC0", "cc0"))
                else "vendor-licensed"
            )
            if declaration and declaration.tenant_bound:
                category = (
                    "tenant monitoring"
                    if category == "public platform data"
                    else category
                )
            if declaration and declaration.retain_days:
                category = "personal"
            inputs.append(
                dict(
                    UNKNOWN,
                    category=category,
                    tenant="tenant"
                    if declaration and declaration.tenant_bound
                    else "global",
                    learning=r.get("learning_eligible") == "true",
                    resale=r.get("resale_permitted") == "true",
                    licence_status=licence,
                )
            )
        value = (
            combine(inputs)
            if inputs
            else dict(UNKNOWN, category="public platform data", tenant="global")
        )
        value["non_personal_category"] = max(
            (v.get("non_personal_category", v["category"]) for v in inputs),
            key=CATEGORIES.index,
            default="public platform data",
        )
        code = node.get("raw_code", "")
        if f"raw.{node['name']}" in privacy:
            value["category"] = "personal"
        if "mdp_pseudonym" in code or any(
            "pseudonym" in str(c).lower() for c in columns.values()
        ):
            value["category"] = "personal"
        if "scope:tenant" in tags or "tenant_id" in columns:
            value["tenant"] = "tenant"
            value["learning"] = False
            value["category"] = max(
                (value["category"], "tenant monitoring"), key=CATEGORIES.index
            )
        # A declared global projection can read a shared raw table with tenant writers.
        if "scope:global" in tags and "scope:tenant" not in tags:
            value["tenant"] = "global"
        if "scope:tenant" in tags:
            value["non_personal_category"] = max(
                (value["non_personal_category"], "tenant monitoring"), key=CATEGORIES.index
            )
        value["layer"] = next(
            (t for t in ("gold", "silver", "bronze") if t in tags),
            "bronze" if node.get("resource_type") == "source" else "silver",
        )
        value.update(
            grain=meta.get("grain", []),
            description=node.get("description") or node["name"].replace("_", " "),
        )
        cache[uid] = value
        return value

    privacy = {}
    for declaration in declarations.values():
        for table in declaration.writes:
            model = (
                declaration.schema.get(table)
                if isinstance(declaration.schema, dict)
                else declaration.schema
            )
            safe = (
                getattr(model, "model_config", {}).get("json_schema_extra") or {}
            ).get("non_personal", [])
            fields = getattr(model, "model_fields", {})
            if set(safe) - set(fields):
                raise ValueError(f"{table}: non_personal names an undeclared field")
            for name, field in fields.items():
                mode = (field.json_schema_extra or {}).get(
                    "explore", "non_personal" if name in safe else "omit"
                )
                if mode:
                    if mode not in {"pseudonym", "omit", "non_personal"}:
                        raise ValueError(
                            f"{table}.{name}: explore must be non_personal, pseudonym or omit"
                        )
                    privacy.setdefault(table, {})[name] = mode
    result = {}
    for uid, node in sorted(nodes.items()):
        if node.get("resource_type") not in {"source", "model", "seed"}:
            continue
        config = node.get("config", {})
        schema = (
            node.get("source_name")
            if node.get("resource_type") == "source"
            else config.get("schema") or "dbt"
        )
        if "scope:tenant" in node.get("tags", []):
            schema = "tenant_*_" + schema
        name = node.get("alias") or node.get("identifier") or node["name"]
        result[f"{schema}.{name}"] = {
            **label(uid),
            "columns": {
                k: c.get("description") or k.replace("_", " ")
                for k, c in node.get("columns", {}).items()
            },
            "model": uid,
            "materialized": config.get("materialized", "source"),
        }
        result[f"{schema}.{name}"]["shared_privacy"] = shared.get(node["name"], {})
        key = f"{schema}.{name}"
        if key in privacy:
            result[key]["privacy"] = privacy[key]
            result[key]["category"] = "personal"
    from mdp_functions.exporter import raw_schemas
    from mdp_functions.warehouse.postgres import MIRRORS

    raw = raw_schemas(declarations, root / "functions/schemas")
    raw.update({"raw." + name: columns for name, (columns, _) in MIRRORS.items()})
    # Load receipts belong to the warehouse adapter, not a function or control mirror.
    # Only these reviewed fields are exposed; other physical columns remain null.
    raw["raw._load_receipts"] = sorted(HISTORY_METADATA["raw._load_receipts"])
    for key, columns in raw.items():
        if key not in result:
            result[key] = dict(
                UNKNOWN,
                columns={k: k.replace("_", " ") for k in columns},
                description=key,
                model=None,
                materialized="source",
            )
        # Mirror sources do not repeat their column declarations in dbt YAML.
        # Their safe copies use the warehouse declaration, with the same default omission.
        for column in columns:
            result[key]["columns"].setdefault(column, column.replace("_", " "))
    from mdp_functions.schemas import LINEAGE

    for key, value in result.items():
        if key.startswith("raw."):
            if key in HISTORY_FILTERS:
                value["explore_filter"] = HISTORY_FILTERS[key]
                value["explore_filter_relations"] = HISTORY_FILTER_RELATIONS.get(
                    key, []
                )
            modes = privacy.get(key, {})
            value["privacy"] = {
                column: modes.get(
                    column,
                    "non_personal"
                    if column
                    in (set(LINEAGE) - {"_extra", "_request_id"})
                    | {"tenant_id"}
                    | HISTORY_METADATA.get(key, set())
                    else "omit",
                )
                for column in value["columns"]
            }
    for name, columns in shared.items():
        if name.startswith("catalog."):
            result[name] = dict(
                UNKNOWN,
                description="Warehouse metadata.",
                shared_privacy=columns,
                columns=dict.fromkeys(columns, "Warehouse metadata."),
            )
    return dict(sorted(result.items()))


def load():
    return json.loads(Path(__file__).with_name("relation_labels.json").read_text())


def relation_label(schema, name, definitions=None):
    from mdp_functions.sandbox_policy import POLICY

    if schema.startswith(POLICY["prefix"]):
        return dict(UNKNOWN, layer="sandbox", derived_from=[])
    definitions = load() if definitions is None else definitions
    schema = schema.removeprefix("explore_")
    match = re.fullmatch(r"tenant_(.+)_(staging|intermediate|marts)", schema)
    family = (
        "tenant_*_" + match[2]
        if match
        else "raw"
        if schema == "explore_raw"
        else schema
    )
    value = definitions.get(f"{family}.{name}")
    if value is None and schema.startswith("wb_"):
        matches = [
            v
            for key, v in definitions.items()
            if key.split(".")[-1] == name and not key.startswith("raw.")
        ]
        value = matches[0] if len(matches) == 1 else None
    value = dict(value or UNKNOWN)
    if match:
        value["tenant"] = match[1]
        value["learning"] = False
    return value
