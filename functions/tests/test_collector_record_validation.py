"""Tests for collector record validation."""


import httpx
from conftest import bound
from mdp_functions.runs import Runtime


async def test_fixture_accounts_non_object_user_is_rejected(rt: Runtime) -> None:
    rt.transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"record": {"identity": "bad", "stats": {}}}
        )
    )
    _, run = await bound(rt)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])
    assert result["run"]["rows_written"] == 0 and result["run"]["rows_rejected"] == 2
    assert result["run"]["error_class"] == "partial_coverage"
    assert result["receipts"][0]["target_coverage"] == 0
