"""Tests for tenant runner lifecycle."""


import os
from uuid import uuid4

import pytest
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO
from tenant_schedule_fixture import tenant


def test_deploy_destroys_unlisted_tenant_machines_and_fails_on_a_bad_list(
    tmp_path,
) -> None:
    """The dry run shows the destroy step for a tenant machine the list no longer holds, and a tenant row
    machines.py refuses fails the deploy before any machine command."""
    import json
    import subprocess

    def dry_run(tenants):
        path = tmp_path / "tenants.json"
        path.write_text(json.dumps(tenants))
        return subprocess.run(
            [
                "bash",
                str(REPO / "ops/deploy.sh"),
                "--dry-run",
                "--app",
                "mdp-core-runner",
            ],
            env=os.environ
            | {"FLY_ORG": "example-org", "MDP_CORE_TENANTS_FILE": str(path)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    listed = dry_run([{"id": "t-1", "slug": "acme-records"}])
    assert listed.returncode == 0, listed.stderr
    assert "--name mdp-daily-acme-records" not in listed.stdout
    assert (
        "IF a machine named mdp-<cadence>-<slug> is not in the list above:"
        in listed.stdout
    )
    assert (
        "machine destroy \\<stale-machine-id\\> --force --org example-org --app mdp-core-runner"
        in listed.stdout
    )
    refused = dry_run([{"id": "t-2", "slug": "lifecycle_one"}])
    assert refused.returncode != 0
    assert "is not a machine name" in refused.stderr
    assert (
        "machine update" not in refused.stdout and "machine run" not in refused.stdout
    )


@pytest.mark.docker
async def test_run_sh_refuses_a_tenant_replay(rt: Runtime, databases) -> None:
    """Tenant Replay stays disabled until the run.sh recipe refuses it before any run starts."""
    import asyncio
    import subprocess

    _, scope = tenant(databases, slug="replay-refused", tz="UTC")
    binding = await rt.cycles.bind_cycle(
        "daily",
        scope,
        "core:" + uuid4().hex,
        "scheduled",
        f"core-daily-{scope}",
        runner="core",
    )
    rt.db.execute(
        "UPDATE control.cycle SET status='closed',closed_at=now() WHERE id=%s",
        (binding["cycle_id"],),
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"MDP_RUN_ID", "MDP_RUNNER_LOCKED", "MDP_RESTORE"}
    }
    refused = await asyncio.to_thread(
        subprocess.run,
        [
            "bash",
            str(REPO / "ops/run.sh"),
            "daily",
            "--cycle-id",
            str(binding["cycle_id"]),
        ],
        env=env | {"MDP_CONTROL_RT_URL": databases["admin_control"]},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert refused.returncode != 0
    assert (
        "replay_unavailable: tenant Replay is disabled until "
        in refused.stderr
    )
