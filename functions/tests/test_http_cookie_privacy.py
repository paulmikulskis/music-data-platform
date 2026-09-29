"""Tests for http cookie privacy."""


from uuid import uuid4

import httpx
import pytest
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from test_fetch_runtime import FakeDB, no_page


@pytest.mark.parametrize("http_version", [None, "1.1"])
async def test_runtime_client_keeps_no_cookies(monkeypatch, http_version):
    """A Set-Cookie (Bandcamp's cart_client_id) is never sent back on a later request."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    manifest = discover()["bc_radio"]
    run_row = {
        "id": str(uuid4()),
        "cycle_id": str(uuid4()),
        "streamline_id": str(uuid4()),
        "tenant_id": None,
    }
    db = FakeDB()
    db.health = {"host_rps": 1000, "http_version": http_version}
    sent = []

    def answer(request):
        sent.append(request.headers.get("cookie"))
        return httpx.Response(
            200,
            text="{}",
            headers={"set-cookie": "cart_client_id=abc; Domain=.bandcamp.com; Path=/"},
        )

    ctx = Ctx(manifest, run_row)
    async with TracedClient(
        ctx, db, run_row, no_page, transport=httpx.MockTransport(answer)
    ) as client:
        for _ in range(2):
            await client.get("https://bandcamp.com/api/x")
        assert not list(client.cookies.jar)
    assert sent == [None, None]
