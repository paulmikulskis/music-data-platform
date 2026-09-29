"""Recovery text for analyst reads, shared through the error catalog."""

import json

from mdp_functions.errors import error_hint


def failure(code, detail=None, next_step=None):
    hint = error_hint(code)
    step = next_step or hint["next_step"]
    return {
        "error_class": code,
        "message": hint["summary"] + (" " + detail if detail else "") + " " + step,
        "next_step": step,
        "runbook": ("/runbooks/" + hint["runbook"]) if hint["runbook"] else None,
    }


def local_build_command(model, tenant=False):
    scope = (
        "DBT_MDP_SCOPE=tenant:00000000-0000-0000-0000-000000000001 " if tenant else ""
    )
    variables = (
        "{dry_run: true, tenant_slug: demo}"
        if tenant
        else '{dry_run: true, cycle_opened_at: "2026-09-20T23:00:00Z"}'
    )
    return (
        f"source ops/local/env.sh && {scope}uv run --project dbt dbt build "
        f"--target pg_local --vars '{variables}' --select +{model} --indirect-selection cautious"
    )


def ephemeral_next_step(name, schema):
    """Name a stored global descendant when direct SQL names an inline model."""
    from mdp_functions.settings import REPO

    path = REPO / "dbt/target/manifest.json"
    if not path.exists():
        return None
    nodes = json.loads(path.read_text()).get("nodes", {})
    candidates = {
        key: node for key, node in nodes.items()
        if key.startswith("model.") and "scope:tenant" not in node.get("tags", [])
    }
    roots = [
        key for key, node in candidates.items()
        if (node.get("alias") or node.get("name")) == name
        and node.get("config", {}).get("schema") == schema.removeprefix("explore_")
        and node.get("config", {}).get("materialized") == "ephemeral"
    ]
    if not roots:
        return None
    pending, seen = roots, set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        for key, node in sorted(candidates.items()):
            if current not in node.get("depends_on", {}).get("nodes", []):
                continue
            config = node.get("config", {})
            if config.get("materialized") in {"table", "view", "incremental"} and config.get("schema") in {"marts", "intermediate"}:
                relation = config["schema"] + "." + (node.get("alias") or node["name"])
                return f"Use SELECT * FROM {relation} in a draft; if it is not built, open /explorer for its build command."
            pending.append(key)
    return error_hint("model_ephemeral")["next_step"]
