"""Tests for enrichment budget."""


from uuid import uuid4

import httpx
from conftest import bound
from mdp_functions.fetch.forbidden import RefusingClient
from test_enrichment_runtime import configure, inputs


async def test_llm_overage_records_actual_and_stops(rt, databases, monkeypatch):
    inputs(databases, 3)
    configure(rt, databases, version=50, cap=100)
    rt.settings.litellm_base_url = "http://fixture.invalid/v1"
    rt.settings.litellm_keys = {"fixture": uuid4().hex}
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": uuid4().hex,
                "choices": [{"message": {"content": "fixture"}}],
                "usage": {"total_tokens": 1},
            },
            headers={"x-litellm-response-cost": "1.25"},
        )

    # The runtime's LLM client is a refusing client; the fixture keeps its refusing transport.
    monkeypatch.setattr(
        "mdp_functions.llm.RefusingClient",
        lambda **kw: RefusingClient(transport=httpx.MockTransport(response), **kw),
    )
    _, run = await bound(rt, "fixture_enrichment")
    await rt.execute(run["id"])
    saved = rt.db.one(
        "SELECT status,error_class,cost_cents,error_message FROM control.run WHERE id=%s",
        (run["id"],),
    )
    assert saved["error_class"] == "cost_cap_hit" and saved["cost_cents"] == 125
    assert len(calls) == 1
    receipt = rt.db.one(
        "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='cost_cap_hit'",
        (run["id"],),
    )
    assert receipt["attrs"] == {"reserved": 25, "spent": 125, "overage": 100}
