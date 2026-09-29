"""Workbench readiness does not expose its authenticated operations."""

from unittest.mock import Mock

import httpx
from mdp_functions import workbench
from mdp_functions.settings import Settings


async def test_health_is_public_but_operations_stay_authenticated(monkeypatch):
    monkeypatch.setattr(workbench, "Workbench", Mock())
    app = workbench.create_app(Settings(service_token="fixture-service"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        for path in ("/", "/docs", "/openapi.json", "/health/detail"):
            assert (await client.get(path)).status_code == 401
