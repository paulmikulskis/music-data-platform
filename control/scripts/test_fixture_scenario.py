"""The scenario option is absent in production and cannot leak into other runs."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import httpx
from mdp_functions.api import create_app
from mdp_functions.settings import Settings


def test_fixture_scenario_refused_without_opt_in(monkeypatch):
    monkeypatch.delenv("MDP_FIXTURE_MODE", raising=False)
    async def check():
        app = create_app(Settings(service_token="fixture"), runtime=SimpleNamespace(), recover=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://fixture") as client:
            response=await client.post("/v1/functions/fixture_accounts/run?fixture_scenario=html&dbt_run_id=fixture",headers={"authorization":"Bearer fixture"})
            assert response.status_code == 403
            assert response.json()["error_class"] == "fixture_unavailable"
            assert (await client.get("/v1/_fixture/state",headers={"authorization":"Bearer fixture"})).status_code == 404
    asyncio.run(check())


def test_scenario_transport_is_scoped_to_the_run(monkeypatch):
    monkeypatch.setenv("MDP_FIXTURE_MODE", "1")
    started=[]
    class Runtime:
        transport=None
        def admit(self,*args,**kwargs): return {"id":uuid4(),"status":"queued"}
        def start(self,run_id): started.append(self.transport)
    rt=Runtime()
    async def check():
        app=create_app(Settings(service_token="fixture"),runtime=rt,recover=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://fixture") as client:
            headers={"authorization":"Bearer fixture"}
            response=await client.post("/v1/functions/fixture_accounts/run?fixture_scenario=html&dbt_run_id=fixture",headers=headers)
            assert response.status_code == 202
            assert rt.transport is None
            request=httpx.Request("GET","https://fixture.invalid/public/check?username=synthetic")
            reply=await started[0].handle_async_request(request)
            assert "<html>" in reply.text
            response=await client.post("/v1/functions/fixture_accounts/run?dbt_run_id=fixture",headers=headers)
            assert response.status_code == 202
            assert started[-1] is None
            response=await client.post("/v1/functions/fixture_accounts/run?fixture_scenario=not_found&dbt_run_id=fixture",headers=headers)
            assert response.status_code == 202
            transport=started[-1]
            missing=await transport.handle_async_request(httpx.Request("GET","https://fixture.invalid/public/check?username=first"))
            malformed=await transport.handle_async_request(httpx.Request("GET","https://fixture.invalid/public/check?username=second"))
            assert missing.status_code == 404
            assert malformed.status_code == 200 and malformed.json() == {"record":{}}
            assert rt.transport is None
    asyncio.run(check())
