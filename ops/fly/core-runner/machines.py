"""The scheduled core-runner machines: one per cadence for the global scope, and one per
(cadence, tenant) for every active tenant and every cadence with tenant work. Each
wakes on an hourly Fly tick; run.sh's due gate (MDP_CORE_GATE=1) decides whether the tick runs.

python3 ops/fly/core-runner/machines.py [tenants.json] prints one tab-separated line per machine:
name, cadence, then the --env assignments. tenants.json is a list of {"id", "slug"}; bootstrap.py
writes it to $MDP_CORE_TENANTS_FILE from control on every deploy.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CADENCES = ("hourly", "daily", "weekly")


def tenant_cadences(root: Path = ROOT) -> list[str]:
    """Cadences with tenant work: the ones `mdp sources export` writes a tenant close for (a
    tenant-bound function or a tenant model, exporter.tenant_cadences), so a job it registers always
    has a machine and a machine always has a job."""
    return [c for c in CADENCES if (root / f"dbt/models/bronze/bronze_close__{c}_tenant.sql").exists()]


def machines(tenants: list[dict], root: Path = ROOT) -> list[dict]:
    common = {"MDP_RUN_REASON_CATEGORY": "scheduled", "MDP_CORE_GATE": "1"}
    result = [{"name": f"mdp-{c}", "cadence": c, "env": {**common, "DBT_MDP_SCOPE": "global"}} for c in CADENCES]
    for tenant in sorted(tenants, key=lambda t: t["slug"]):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", tenant["slug"]):
            raise SystemExit(f"tenant slug {tenant['slug']!r} is not a machine name")
        result += [
            {"name": f"mdp-{c}-{tenant['slug']}", "cadence": c,
             "env": {**common, "DBT_MDP_SCOPE": f"tenant:{tenant['id']}", "MDP_TENANT_SLUG": tenant["slug"]}}
            for c in tenant_cadences(root)
        ]
    return result


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    tenants = json.loads(path.read_text() or "[]") if path and path.exists() else []
    for machine in machines(tenants):
        print("\t".join([machine["name"], machine["cadence"], *(f"{k}={v}" for k, v in machine["env"].items())]))
