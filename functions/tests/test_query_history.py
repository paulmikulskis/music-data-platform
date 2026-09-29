"""Query review reads the audit table with the caller's Workbench access."""

from unittest.mock import Mock

import httpx
import psycopg
import pytest
from mdp_functions import workbench
from mdp_functions.settings import Settings


@pytest.fixture
async def history_client(catalog_databases, monkeypatch):
    with psycopg.connect(catalog_databases["admin_warehouse"]) as conn:
        conn.execute(
            "INSERT INTO catalog.query_audit "
            "(event_id,actor,occurred_at,channel,query_hash,tenants,labels,cross_tenant,unresolved) "
            "VALUES ('own','staff:viewer',now(),'workbench','own-hash','[]','{}',false,false), "
            "('other','staff:other',now(),'workbench','other-hash','[\"one\",\"two\"]','{}',true,true)"
        )
    monkeypatch.setattr(workbench, "Workbench", Mock())
    app = workbench.create_app(
        Settings(
            service_token="fixture",
            workbench_admin_url=catalog_databases["admin_warehouse"],
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        yield client


@pytest.mark.parametrize(
    ("user", "staff", "expected"),
    [
        ("reviewer", False, ["other", "own"]),
        ("staff:viewer", True, ["own"]),
        ("staff:other", True, ["other"]),
        ("staff:empty", True, []),
    ],
)
async def test_query_history_access(history_client, user, staff, expected):
    response = await history_client.post(
        "/v1/workbench/queries",
        headers={"authorization": "Bearer fixture"},
        json={"userId": user, "staff": staff},
    )
    assert response.status_code == 200, response.text
    assert [row["event_id"] for row in response.json()["queries"]] == expected


@pytest.mark.parametrize("token", [None, "wrong"])
async def test_query_history_requires_internal_service_auth(history_client, token):
    response = await history_client.post(
        "/v1/workbench/queries",
        headers={"authorization": f"Bearer {token}"} if token else {},
        json={"userId": "reviewer", "staff": False},
    )
    assert response.status_code == 401
    assert response.json()["error_class"] == "unauthorized"
    assert "queries" not in response.json()


async def test_query_history_rejects_unprefixed_analyst(history_client):
    response = await history_client.post(
        "/v1/workbench/queries",
        headers={"authorization": "Bearer fixture"},
        json={"userId": "reviewer", "staff": True},
    )
    assert response.status_code == 403
    assert response.json()["error_class"] == "forbidden"
