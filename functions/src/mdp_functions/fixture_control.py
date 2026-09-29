"""Opt-in HTTP fixture controls. No production worker or landing hooks."""

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field


class FixturePlan(BaseModel):
    source_key: str
    pages: int = Field(default=5, ge=1, le=100)
    fail_at: int | None = Field(default=None, ge=1)
    delay_ms: int = Field(default=0, ge=0, le=120000)
    # Hold every page this plan starts on a barrier until the harness releases it (or HOLD_LIMIT_S
    # passes), so a case can observe what happens while a page is in flight without racing a sleep.
    hold: bool = False


class FixtureRelease(BaseModel):
    source_key: str


PLANS: dict[str, FixturePlan] = {}
# One barrier per source, set by a release; a later plan without hold leaves a held page held.
BARRIERS: dict[str, asyncio.Event] = {}
HOLD_LIMIT_S = 120


def hold(source: str) -> asyncio.Event:
    """A fresh barrier for `source`: pages its holding plan starts wait on it."""
    BARRIERS[source] = asyncio.Event()
    return BARRIERS[source]


def release(source: str) -> bool:
    """Open `source`'s barrier; False when nothing holds."""
    barrier = BARRIERS.pop(source, None)
    if barrier is None:
        return False
    barrier.set()
    return True


class ControlledTransport(httpx.AsyncBaseTransport):
    """Probe pages are numbered from one; fail_at selects a failing page.

    A forced failure is a vendor outage (a transport error): the client retries it, the batch
    stops at that target, and a Retry resumes it there. (A non-429 4xx would be terminal for
    its target instead.) Probe functions issue the requested number of pages themselves.
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        self.started: dict[str, int] = {}

    def is_synthetic(self, request: httpx.Request) -> bool:
        return True

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        source = request.url.path.strip("/")
        if source == "accounts":
            source = "fixture_accounts"
        plan = PLANS.get(source)
        if plan is None:
            return httpx.Response(400, json={"error": "fixture_plan_missing"})
        page = int(request.url.params.get("page", "1"))
        if source == "fixture_accounts":
            batch = self.db.one(
                "SELECT b.index FROM control.batch b JOIN control.run r ON r.id=b.run_id "
                "JOIN control.target t ON t.id=ANY(b.target_ids) "
                "JOIN control.streamline s ON s.id=r.streamline_id "
                "WHERE t.handle=%s AND s.source_key='fixture_accounts' "
                "ORDER BY r.created_at DESC LIMIT 1",
                (request.url.params.get("username", ""),),
            )
            page = batch["index"] + 1 if batch else 1
        barrier = BARRIERS.get(source) if plan.hold else None
        self.started[source] = self.started.get(source, 0) + 1
        await asyncio.sleep(plan.delay_ms / 1000)
        if barrier is not None:
            try:
                await asyncio.wait_for(barrier.wait(), timeout=HOLD_LIMIT_S)
            except TimeoutError:
                pass
        if page > plan.pages:
            return httpx.Response(400, json={"error": "fixture_forced_failure"})
        if plan.fail_at is not None and page >= plan.fail_at:
            raise httpx.ConnectError("fixture_forced_failure", request=request)
        return httpx.Response(
            200,
            json={
                "page": page,
                "record": {
                    "identity": {},
                    "stats": {
                        "followers": 100 + page,
                        "following": 10,
                        "likes": 1000,
                        "posts": 5,
                    },
                },
            },
        )
