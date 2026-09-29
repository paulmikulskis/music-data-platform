"""Local transport: synthetic Fixture accounts and real public-source HTTP.

Keeps the runtime, landing protocol and cycle machinery unchanged. Explicitly opt
in with MDP_FIXTURE_MODE=1; configure all role URLs and the token in the environment.
"""

import os

import httpx
import uvicorn
from mdp_functions.api import create_app
from mdp_functions.fixture_control import PLANS, ControlledTransport, FixturePlan
from mdp_functions.runs import Runtime
from mdp_functions.settings import Settings
from mdp_functions.telemetry import configure


class ControlTransport(ControlledTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "fixture.invalid":
            return await super().handle_async_request(request)
        # A fresh transport per request avoids sharing a closed pool across the
        # runtime's per-batch clients; responses are consumed before leaving it.
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.send(request)
            await response.aread()
            return response


def factory():
    if os.environ.get("MDP_FIXTURE_MODE") != "1":
        raise RuntimeError("This local helper requires MDP_FIXTURE_MODE=1")
    settings = Settings()
    configure(settings)
    runtime = Runtime(settings)
    runtime.transport = ControlTransport(runtime.db)
    PLANS["fixture_accounts"] = FixturePlan(source_key="fixture_accounts", pages=100)
    return create_app(settings, runtime=runtime)


if __name__ == "__main__":
    uvicorn.run(factory(), host="0.0.0.0", port=8083)
