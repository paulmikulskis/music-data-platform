"""Run polls answer while page landings fill asyncio's default executor."""

import asyncio
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
from mdp_functions.api import create_app
from mdp_functions.settings import Settings

RUN = uuid4()


def receipts(run_id):
    return {
        "run": {
            "id": RUN,
            "kind": "invoke",
            "scope": "global",
            "streamline_id": None,
            "cycle_id": None,
            "parent_run_id": None,
            "trace_id": None,
            "status": "running",
            "coverage": None,
            "error_class": None,
            "error_message": None,
            "rows_written": "0",
            "rows_rejected": "0",
            "cost_cents": "0",
            "created_at": datetime.now(UTC),
            "repairs_pending": 0,
        },
        "receipts": [],
        "repairs_pending": 0,
    }


async def test_run_poll_answers_while_landings_hold_the_default_executor() -> None:
    settings = Settings(service_token="token")
    app = create_app(settings, SimpleNamespace(receipts=receipts), recover=False)
    release = threading.Event()
    loop = asyncio.get_running_loop()
    # More blocked landings than the default executor has threads (min(32, cpus + 4)).
    landings = [loop.run_in_executor(None, release.wait, 5) for _ in range(40)]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer token"},
        ) as client:
            started = time.monotonic()
            response = await asyncio.wait_for(client.get(f"/v1/runs/{RUN}"), 3)
            elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        assert response.json()["run"]["status"] == "running"
        assert elapsed < 1
    finally:
        release.set()
        await asyncio.gather(*landings)
