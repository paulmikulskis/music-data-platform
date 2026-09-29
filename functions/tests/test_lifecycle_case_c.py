"""Case (c): a recovered vendor completes the same five-batch work key. The outage is a transport
failure, which stops a batch at its first unfinished target; a non-429 4xx is terminal for that one
target instead."""

from collections import Counter
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions import admission
from mdp_functions.api import create_app
from mdp_functions.errors import LeaseLost
from mdp_functions.runs import Runtime

pytestmark = pytest.mark.docker


@pytest.mark.parametrize("uploaded_before_failure", [False, True])
async def test_lifecycle_case_c_resumes_partial_batches(
    rt: Runtime, databases: dict[str, str], uploaded_before_failure: bool, monkeypatch
) -> None:
    with psycopg.connect(databases["admin_control"]) as conn:
        set_id = conn.execute("SELECT id FROM control.target_set").fetchone()[0]
        for index in range(3, 11):
            conn.execute(
                """INSERT INTO control.target(target_set_id,platform,platform_account_id,
                handle,resolution_status,activated_at)
                VALUES (%s,'fixture',%s,%s,'resolved',now())""",
                (set_id, f"acceptance-{index:03}", f"acceptance_account_{index:03}"),
            )
        conn.execute(
            "UPDATE control.streamline SET batch_size=2,max_concurrency=1 WHERE source_key='fixture_accounts'"
        )
    _, run = await bound(rt)
    batches = rt.db.all(
        "SELECT * FROM control.batch WHERE run_id=%s ORDER BY index", (run["id"],)
    )
    targets = {
        row["handle"]: row
        for row in rt.db.all("SELECT id,handle,platform_account_id FROM control.target")
    }
    batch_by_target = {
        target: batch for batch in batches for target in batch["target_ids"]
    }
    calls = Counter()
    recovered = False

    async def vendor(request: httpx.Request) -> httpx.Response:
        target = targets[request.url.params["username"]]
        batch = batch_by_target[target["id"]]
        calls[target["id"]] += 1
        failing_target = sorted(batch["target_ids"])[int(uploaded_before_failure)]
        if not recovered and batch["index"] >= 2 and target["id"] == failing_target:
            raise httpx.ConnectError("fixture outage")
        return httpx.Response(
            200,
            json={
                "record": {"identity": {"id": target["platform_account_id"]}, "stats": {}}
            },
        )

    rt.transport = httpx.MockTransport(vendor)
    await rt.execute(run["id"])
    before = rt.db.all(
        "SELECT * FROM control.batch WHERE run_id=%s ORDER BY index", (run["id"],)
    )
    assert [b["status"] for b in before] == ["succeeded"] * 2 + ["partial"] * 3
    receipt = rt.receipts(run["id"])
    assert receipt["run"]["status"] == "failed"
    assert receipt["receipts"][0]["target_coverage_met"] is False
    for batch in before[2:]:
        checkpoint = batch["cursor_checkpoint"] or {}
        assert not checkpoint.get("complete")
        completed = set(checkpoint.get("completed_targets", [])) & {str(t) for t in batch["target_ids"]}
        assert len(completed) == int(uploaded_before_failure)
        assert len(batch["dump_ids"]) == int(uploaded_before_failure)
    first = rt.db.one("SELECT * FROM control.run_attempt WHERE run_id=%s", (run["id"],))

    recovered = True
    retry_id = "local:" + uuid4().hex
    binding = await rt.cycles.bind_cycle(
        "hourly", "global", retry_id, "other", "local:hourly", runner="core"
    )
    assert binding["cycle_id"] == run["cycle_id"]
    resumed = rt.admit("fixture_accounts", dbt_run_id=retry_id)
    assert resumed["id"] == run["id"]
    assert resumed["revision_id"] == run["revision_id"]
    # Hold worker startup so the first poll deterministically tests admission,
    # rather than depending on the background task winning a scheduling race.
    monkeypatch.setattr(rt, "start", lambda run_id: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        response = await client.post(
            "/v1/invoke", json={"source_key": "fixture_accounts", "dbt_run_id": retry_id}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "running"
        poll = (await client.get(f"/v1/runs/{run['id']}")).json()
        assert poll["run"]["status"] == "running"
        assert poll["run"]["error_class"] is None
    second = rt.db.one(
        "SELECT * FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
        (run["id"],),
    )
    assert second["attempt_no"] == 2
    assert second["deadline_at"] > first["deadline_at"]
    queued = rt.db.all(
        "SELECT * FROM control.batch WHERE run_id=%s ORDER BY index", (run["id"],)
    )
    assert queued[:2] == before[:2]
    for old, new in zip(before[2:], queued[2:], strict=True):
        assert new["status"] == "queued"
        assert new["attempt_id"] == second["id"]
        for field in ("last_part_uploaded", "cursor_checkpoint", "dump_ids"):
            assert new[field] == old[field]
        with pytest.raises(LeaseLost):
            rt.complete_batch(old, "succeeded")
    assert admission.acquire(rt.db, run, first, 30) is None
    await rt.execute(run["id"])
    after = rt.db.all(
        "SELECT * FROM control.batch WHERE run_id=%s ORDER BY index", (run["id"],)
    )
    assert after[:2] == before[:2]
    assert all(batch["status"] == "succeeded" for batch in after)
    for old, new in zip(before[2:], after[2:], strict=True):
        assert new["dump_ids"][: len(old["dump_ids"])] == old["dump_ids"]
        assert len(new["dump_ids"]) == 2
        assert new["last_part_uploaded"] > (old["last_part_uploaded"] or 0)
        # The failing target was asked four times (the client's retries), then once on the Retry.
        assert sum(calls[target] for target in new["target_ids"]) == 6
    assert all(calls[target] == 1 for b in before[:2] for target in b["target_ids"])
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "succeeded"
    assert result["coverage"] == "full"
    assert result["rows_written"] == 10
    with psycopg.connect(rt.settings.warehouse_url) as conn:
        assert conn.execute(
            "SELECT count(*),count(DISTINCT platform_account_id) FROM raw.account_snapshots WHERE _run_id=%s",
            (run["id"],),
        ).fetchone() == (10, 10)
