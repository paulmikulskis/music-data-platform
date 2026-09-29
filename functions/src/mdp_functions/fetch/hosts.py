"""Process-local buckets and semaphores are correct while api runs one machine.

Use leased Postgres buckets before scaling api across machines. DB health is checked
inside the permit, so queued requests observe a block before they can dispatch.
"""

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from weakref import WeakKeyDictionary

DEFAULT_USER_AGENT = (
    "MusicDataPlatform/1.0"
)


class HostLimiter:
    def __init__(self, rps: float = 1, concurrency: int = 1) -> None:
        self.rps = rps
        self.factor = 1.0
        self.tokens = 1.0
        self.updated = monotonic()
        self.semaphore = asyncio.Semaphore(concurrency)
        self.lock = asyncio.Lock()
        self.not_before = 0.0

    def throttle(self, delay: float) -> None:
        self.factor = max(0.01, self.factor / 2)
        self.not_before = max(self.not_before, monotonic() + delay)

    def delay(self) -> float:
        now = monotonic()
        rate = max(0.001, self.rps * self.factor)
        self.tokens = min(1, self.tokens + (now - self.updated) * rate)
        self.updated = now
        return max(self.not_before - now, (1 - self.tokens) / rate)

    async def acquire(self) -> None:
        async with self.lock:
            while (delay := self.delay()) > 0:
                await asyncio.sleep(delay)
            self.tokens -= 1

    @asynccontextmanager
    async def permit(self, *, wait=True):
        # Checks and uncontended asyncio acquisitions do not yield: probes cannot queue
        # ahead of a scheduled waiter or dispatch without consuming the shared token.
        if not wait and (self.semaphore.locked() or self.lock.locked()):
            yield False
            return
        async with self.semaphore:
            if wait:
                await self.acquire()
            else:
                async with self.lock:
                    if self.delay() > 0:
                        yield False
                        return
                    self.tokens -= 1
            yield True


def host_key(host: str, declared: list[str]) -> str | None:
    """The host a request is paced, permitted, and paused under, or None when the
    allowlist does not cover it. A declared `*.example.com` covers every subdomain
    and keys them all on `example.com`; an empty allowlist permits any host."""
    host = host.lower()
    if not declared:
        return host
    for entry in (h.lower() for h in declared):
        if entry == host:
            return host
        if entry.startswith("*.") and host.endswith(entry[1:]):
            return entry[2:]
    return None


_LIMITERS: WeakKeyDictionary = WeakKeyDictionary()


def host_limiter(host: str, rps: float = 1) -> HostLimiter:
    hosts = _LIMITERS.setdefault(asyncio.get_running_loop(), {})
    limiter = hosts.setdefault(host, HostLimiter(rps))
    limiter.rps = rps
    return limiter
