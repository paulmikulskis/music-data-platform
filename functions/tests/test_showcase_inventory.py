from uuid import uuid4

import httpx
from mdp_functions.api import create_app
from mdp_functions.errors import error_hint


async def test_inventory_alert_auth_validation_and_deduplication(rt):
    warehouse = str(rt.db.one("SELECT id FROM control.warehouse WHERE is_production")["id"])
    app = create_app(rt.settings, rt, recover=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/v1/alerts/showcase_inventory_failed", json={"warehouse": warehouse})
        assert response.status_code in (401, 403)
        client.headers["Authorization"] = "Bearer " + rt.settings.service_token
        for body in ({}, {"warehouse": warehouse}):
            response = await client.post("/v1/alerts/showcase_inventory_failed", json=body)
            assert response.status_code == 200, response.text
            assert response.json()["next_step"] == error_hint("showcase_inventory_failed")["next_step"]
            assert response.json()["warehouse"] == warehouse
        assert (await client.post("/v1/alerts/showcase_inventory_failed", json={"warehouse": "bad"})).status_code == 422
        assert (await client.post("/v1/alerts/showcase_inventory_failed", json={"warehouse": str(uuid4())})).status_code == 404
    alerts = rt.db.all("SELECT subject_id,runbook_slug FROM control.alert WHERE class='showcase_inventory_failed'")
    assert alerts == [{"subject_id": warehouse, "runbook_slug": "showcase-inventory-failed"}]
