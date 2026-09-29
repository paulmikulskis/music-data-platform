"""Vendor pages: strict fixtures, bounded retries, trace context, and call accounting."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import ROUND_CEILING
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx
from opentelemetry import trace

from mdp_functions.budget import draw, provider_cap
from mdp_functions.control_db import ControlDB, alert, block_host, envelope_miss
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.detectors import classify, retry_after
from mdp_functions.fetch.forbidden import (
    NoCookies,
    RefusingClient,
    refuse,
    refuse_model,
)
from mdp_functions.fetch.guard import transport_network
from mdp_functions.fetch.hosts import (
    DEFAULT_USER_AGENT,
    host_key,
    host_limiter,
)
from mdp_functions.fetch.logging import protect_transport_logs
from mdp_functions.fetch.providers import (
    ProxyProvider,
    market_country,
    provider_scope_id,
    webshare_provider,
)
from mdp_functions.layers import Ctx
from mdp_functions.settings import Settings


class FixtureTransport(httpx.AsyncBaseTransport):
    def __init__(self, paths: list[Path]) -> None:
        self.responses: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.offsets: dict[tuple[str, str], int] = {}
        for path in paths:
            for line in path.read_text().splitlines():
                entry = json.loads(line)
                request = entry["request"]
                url = httpx.URL(request["url"], params=request.get("params"))
                key = (request["method"].upper(), str(url))
                self.responses.setdefault(key, []).append(entry["response"])

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, str(request.url))
        if key not in self.responses:
            raise ServiceError(
                "fixture_miss", f"No fixture for {request.method} {request.url}"
            )
        index = self.offsets.get(key, 0)
        values = self.responses[key]
        self.offsets[key] = index + 1
        value = values[min(index, len(values) - 1)]
        body = value["body"]
        return httpx.Response(
            value["status"],
            headers=value.get("headers", {}),
            request=request,
            content=json.dumps(body).encode()
            if not isinstance(body, str)
            else body.encode(),
        )


def no_cookies():
    return NoCookies()

class DryRunUnavailable(BaseException):
    """The transport requires state that an in-memory probe cannot publish."""


class TracedClient(RefusingClient):
    def __init__(
        self,
        ctx: Ctx,
        db: ControlDB,
        run: dict[str, Any],
        before_page: Callable[[], Awaitable[None]],
        backoff_s: float = 0.25,
        estimate_cents: int = 0,
        probe: bool = False,
        dry_run: bool = False,
        settings: Settings | None = None,
        provider: ProxyProvider | None = None,
        **kwargs: Any,
    ) -> None:
        protect_transport_logs()
        settings = settings or Settings()
        kwargs.setdefault("trust_env", False)
        super().__init__(
            timeout=httpx.Timeout(10, connect=5), cookies=no_cookies(),
            layer=ctx.manifest.layer,
            model_hosts=(httpx.URL(settings.litellm_base_url).host,) if settings and settings.litellm_base_url else (),
            **kwargs
        )
        self.ctx, self.db, self.run = ctx, db, run
        self._before_page, self.backoff_s = before_page, backoff_s
        self.probe = probe or dry_run
        self.dry_run = dry_run
        self.estimate_cents = max(0, estimate_cents)
        self.settings = settings
        self.provider = provider or webshare_provider(
            self.settings.webshare_api_key, self.settings.webshare_microcents_per_byte
        )
        self._fixture_transport = kwargs.get("transport")
        self._last_response: httpx.Response | None = None
        self._last_host = ""
        self._blocked = {}

    async def before_page(self) -> None:
        with transport_network():
            await self._before_page()

    def build_request(self, *args, **kwargs):
        # Construction can refuse cookies before send() sees a request.
        self.ctx.request_id = None
        return super().build_request(*args, **kwargs)

    def block_seconds(self):
        self._blocked[self._last_host] = self._block_signature
        return 0 if self.settings.fixture or not self.settings.dbt_cloud_verify else self.settings.host_block_s

    def _block_host(self, host: str, signature: str) -> None:
        self._block_signature = signature
        seconds = self.block_seconds()
        # A probe keeps its refusal local. Only collection updates shared health.
        if not self.probe:
            block_host(self.db, self.ctx.run_id, host, signature, seconds)

    def _reject(self, reason: str) -> None:
        self.ctx.observed(1)
        self.ctx.reject({"host": self._last_host}, reason=reason)

    def _check_health(self, health: dict[str, Any]) -> None:
        until = health.get("blocked_until")
        if self._last_host in self._blocked or (until and until > datetime.now(UTC)):
            if self.dry_run:
                raise DryRunUnavailable("Shared host health is blocked")
            sig = self._blocked.get(self._last_host) or health.get("last_signature") or "unknown"
            self._reject("blocked:" + sig)
            raise ServiceError("scrape_blocked", f"blocked:{sig}: request refused; do not retry a challenge; use a public feed or review ops/runbooks/scrape_blocked.md.")

    def envelope_miss(self, surface: str, path: str) -> None:
        if self._last_response is not None:
            result = classify(self._last_response, envelope_missing=True)
            if result.kind == "blocked":
                sig = result.signature or "unknown"
                self._block_host(self._last_host, sig)
                self._reject("blocked:" + sig)
                raise ServiceError("scrape_blocked", f"blocked:{sig}: request refused; do not retry a challenge; use a public feed or review ops/runbooks/scrape_blocked.md.")
        # A gold input has no target: its input_ref keys the event, and the derived path decides
        # surface_drift (consecutive misses on inputs not rejected before), so one track's page
        # without its entity is that track's failure.
        gold = self.ctx.manifest.layer == "gold"
        key = self.ctx.target_id or (getattr(self.ctx, "input_row", None) or {}).get("input_ref")
        if not self.dry_run:
            envelope_miss(self.db, self.ctx.run_id, key, surface, path, decide=not gold)

    def _provider_budget(self) -> None:
        budgets = self.db.all(
            "SELECT * FROM control.budget WHERE scope='provider' AND scope_id=%s",
            (provider_scope_id(self.provider.name),),
        )
        for budget in budgets:
            period = {
                "hourly": "hour",
                "daily": "day",
                "weekly": "week",
                "monthly": "month",
            }.get(budget["period"], "month")
            used = self.db.one(
                "SELECT coalesce(sum(cost_microcents),0) AS n, coalesce(sum(quantity),0) AS bytes FROM control.cost_ledger WHERE vendor=%s AND unit='byte' AND is_current AND occurred_at>=date_trunc(%s,now())",
                (self.provider.name, period),
            )
            if (
                budget.get("cap_bytes") is not None
                and used["bytes"] >= budget["cap_bytes"]
            ) or (
                used["n"] >= budget["cap_cents"] * 1_000_000
                and budget["hard_action"] != "warn"
            ):
                with self.db.transaction() as conn:
                    alert(
                        conn,
                        self.run["id"],
                        "proxy_quota_exhausted",
                        self.provider.name,
                    )
                raise ServiceError("proxy_quota_exhausted", "Provider budget exhausted")

        if not any(b.get("cap_bytes") is not None for b in budgets):
            raise ServiceError(
                "proxy_quota_exhausted", "Provider byte quota is not configured"
            )

    def _bytes_cost(self, request_id: str, quantity: int) -> None:
        microcents = int(
            (quantity * self.provider.price_microcents_per_byte).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO control.cost_ledger
                (run_id,streamline_id,tenant_id,vendor,provider_request_id,unit,quantity,cost_cents,cost_microcents,origin)
                VALUES (%s,%s,%s,%s,%s,'byte',%s,%s,%s,'estimate')""",
                (
                    self.run["id"],
                    self.run["streamline_id"],
                    self.run.get("tenant_id"),
                    self.provider.name,
                    request_id,
                    quantity,
                    microcents // 1_000_000,
                    microcents,
                ),
            )
            conn.execute(
                "UPDATE control.run SET cost_cents=cost_cents+%s WHERE id=%s",
                (microcents // 1_000_000, self.run["id"]),
            )

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        with trace.get_tracer("mdp.functions").start_as_current_span(
            "vendor.page",
            attributes={
                "source_key": self.ctx.manifest.source_key,
                "target_id": self.ctx.target_id or "",
                "run_id": self.ctx.run_id,
                "cycle_id": self.ctx.cycle_id,
            },
        ):
            return await self._send(request, **kwargs)

    async def _send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        # Refused before the allowlist and any ledger write; the transports refuse again whatever httpx
        # sends after this (auth follow-ups, event-hook rewrites).
        # Emitted rows already own their lineage; a refused new call must not reuse its id.
        self.ctx.request_id = None
        model_host = httpx.URL(self.settings.litellm_base_url).host if self.settings.litellm_base_url else ""
        refuse_model(request, self.ctx.manifest.layer, (model_host,))
        refuse(request)
        if not self.ctx.manifest.external:
            raise ServiceError(
                "undeclared_egress", "This function declares external=False"
            )
        host = host_key(request.url.host, self.ctx.manifest.hosts)
        if host is None:
            raise ServiceError(
                "undeclared_egress", "Host is absent from the declared allowlist"
            )
        if request.url.username or request.url.password:
            raise ServiceError(
                "undeclared_egress", "Credentials in destination URLs are not supported"
            )
        await self.before_page()
        self._last_host, self._last_response = host, None
        knobs = (
            await asyncio.to_thread(
                self.db.one,
                "SELECT * FROM control.streamline WHERE id=%s",
                (self.run["streamline_id"],),
            )
            or {}
        )
        if not knobs.get("enabled", True):
            if self.probe:
                raise DryRunUnavailable("Function is paused")
            raise ServiceError("surface_drift", "Streamline is paused")
        tier = knobs.get("transport_override") or self.ctx.manifest.transport
        if tier not in {"direct", "residential"}:
            raise ServiceError("invalid_transport", "Unsupported transport")
        if self.dry_run and (tier != "direct" or self.ctx.manifest.provider or self.estimate_cents > 0 or request.method not in {"GET", "HEAD"}):
            raise DryRunUnavailable("This request needs paid accounting or a write method")
        country = None
        if tier == "residential":
            country = market_country(
                (self.ctx.target or {}).get("params_json") or {},
                knobs.get("proxy_country"),
            )
            if knobs.get("proxy_provider") not in (None, self.provider.name):
                raise ServiceError("proxy_unavailable", "Unsupported proxy provider")
        health = (
            await asyncio.to_thread(
                self.db.one, "SELECT * FROM control.host_health WHERE host=%s", (host,)
            )
            or {}
        )
        self._check_health(health)
        rps = float(health.get("host_rps", 1))
        limiter = host_limiter(host, rps)
        request.headers["User-Agent"] = health.get("user_agent") or DEFAULT_USER_AGENT
        request.headers.pop("Proxy-Authorization", None)
        request_id = str(uuid4())
        self.ctx.request_id = request_id
        request.headers["X-Request-ID"] = request_id
        trace.get_current_span().set_attribute("request_id", request_id)
        # Redirects must pass this same policy gate, including the destination's allowlist and pause.
        follow = kwargs.pop("follow_redirects", self.follow_redirects)
        if not isinstance(follow, bool):
            follow = self.follow_redirects
        kwargs["follow_redirects"] = False
        kwargs["stream"] = False
        for attempt in range(1 if self.probe else 4):
            trace.get_current_span().set_attribute("attempt", attempt + 1)
            async with limiter.permit(wait=not self.dry_run) as admitted:
                if not admitted:
                    raise DryRunUnavailable("Shared host capacity or backoff is unavailable")
                health = (
                    await asyncio.to_thread(
                        self.db.one,
                        "SELECT * FROM control.host_health WHERE host=%s",
                        (host,),
                    )
                    or {}
                )
                self._check_health(health)
                if self.probe:
                    current = await asyncio.to_thread(
                        self.db.one, "SELECT enabled FROM control.streamline WHERE id=%s",
                        (self.run["streamline_id"],),
                    )
                    if current and not current["enabled"]:
                        raise DryRunUnavailable("Function is paused")
                proxy = None
                if country:
                    await asyncio.to_thread(self._provider_budget)
                    proxy = await self.provider.proxy_url(
                        self.ctx.run_id, self.ctx.target_id or "", country
                    )
                provider = self.ctx.manifest.provider
                if provider:
                    # A vendor's request cap, on every tier and before every attempt.
                    await asyncio.to_thread(provider_cap, self.db, self.run, provider)
                if not self.dry_run:
                    await asyncio.to_thread(
                        draw,
                        self.db,
                        self.run,
                        provider or host,
                        f"{request_id}:{attempt}",
                        self.estimate_cents,
                    )
                start, response, failure = monotonic(), None, None
                synthetic = isinstance(
                    self._fixture_transport, (FixtureTransport, httpx.MockTransport)
                )
                if hasattr(self._fixture_transport, "is_synthetic"):
                    synthetic = self._fixture_transport.is_synthetic(request)
                protect_transport_logs()
                try:
                    if proxy or health.get("http_version") == "1.1":
                        async with RefusingClient(
                            layer=self.ctx.manifest.layer,
                            model_hosts=self._model_hosts,
                            proxy=proxy.get_secret_value()
                            if proxy and not synthetic
                            else None,
                            transport=self._fixture_transport if synthetic else None,
                            http2=False,
                            trust_env=False,
                            timeout=self.timeout,
                            cookies=no_cookies(),
                        ) as client:
                            response = await client.send(request, **kwargs)
                    else:
                        response = await super().send(request, **kwargs)
                except httpx.TransportError as exc:
                    # Exception messages can contain authenticated proxy URLs.
                    failure = type(exc).__name__
                if response is not None:
                    response.extensions["mdp_tier"] = "fixture" if synthetic else tier
                result = classify(response) if response is not None else None
                bytes_in = (
                    response.num_bytes_downloaded or len(response.content)
                    if response is not None
                    else 0
                )
                # HTTP framing/header estimates, before any credential redaction. Provider
                # reconciliation remains authoritative for compression, TLS and retries.
                if response is not None:
                    bytes_in += (
                        len(
                            f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n".encode()
                        )
                        + sum(len(k) + len(v) + 4 for k, v in response.headers.raw)
                        + 2
                    )
                bytes_out = (
                    len(request.content)
                    + len(request.method.encode())
                    + len(request.url.raw_path)
                    + 12
                    + sum(len(k) + len(v) + 4 for k, v in request.headers.raw)
                    + 2
                )
                if not self.dry_run:
                    await asyncio.to_thread(
                        self.db.execute,
                        """INSERT INTO control.call_ledger
                        (run_id,target_id,vendor,endpoint,request_id,http_status,duration_ms,attempt,cost_cents,tier,provider,bytes_in,bytes_out,block_signature)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            self.run["id"],
                            self.ctx.target_id,
                            host,
                            request.url.path,
                            request_id,
                            response.status_code if response is not None else None,
                            int((monotonic() - start) * 1000),
                            attempt + 1,
                            self.estimate_cents,
                            "fixture" if synthetic else tier,
                            self.provider.name if proxy and not synthetic else None,
                            bytes_in,
                            bytes_out,
                            result.signature if result else None,
                        ),
                    )
                if proxy and not synthetic:
                    await asyncio.to_thread(
                        self._bytes_cost,
                        f"{request_id}:{attempt}",
                        bytes_in + bytes_out,
                    )
                if proxy and response is not None:
                    # Sanitize before any caller, dump, error or telemetry can retain an echoed credential.
                    clean = self.provider.redact(response.text)
                    if clean != response.text:
                        response._content = clean.encode(
                            response.encoding or "utf-8"
                        )
                        response._text = clean
                    for key, value in response.headers.items():
                        response.headers[key] = self.provider.redact(value)
                if result and result.kind == "blocked":
                    sig = result.signature or "unknown"
                    await asyncio.to_thread(self._block_host, host, sig)
                    self._reject("blocked:" + sig)
                    raise ServiceError("scrape_blocked", f"blocked:{sig}: request refused; do not retry a challenge; use a public feed or review ops/runbooks/scrape_blocked.md.")
                if (
                    proxy
                    and response is not None
                    and response.status_code in (402, 407)
                ):
                    with self.db.transaction() as conn:
                        alert(
                            conn,
                            self.run["id"],
                            "proxy_quota_exhausted",
                            self.provider.name,
                        )
                    raise ServiceError(
                        "proxy_quota_exhausted",
                        "Proxy quota or subscription requires review",
                    )
                if result and result.kind == "ok":
                    self._last_response = response
                    if self.ctx.manifest.keep_payload:
                        self.ctx.payloads.append(
                            {"request_id": request_id, "body": response.text}
                        )
                    # Following happens outside the host semaphore.
                    break
                if response is not None and response.status_code in request.extensions.get("mdp_accept", ()):
                    # The caller reads this status's typed error body itself (a vendor's quota or
                    # an empty listing); it is neither stale nor retried here.
                    self._last_response = response
                    break
                if result and result.kind == "gone":
                    self._reject("stale_target")
                    raise ServiceError("stale_target", f"HTTP {response.status_code}", vendor_status=response.status_code)
                retryable = failure is not None or result.kind in {
                    "retryable",
                    "throttled",
                }
                delay = self.backoff_s * 2**attempt
                if result and result.kind == "throttled":
                    parsed = retry_after(response.headers.get("Retry-After", ""))
                    delay = parsed if parsed is not None else delay
                    if not self.probe or self.dry_run:
                        limiter.throttle(delay)
                if not retryable or self.probe or attempt == 3:
                    raise ServiceError(
                        "vendor_retryable" if retryable else "vendor_4xx",
                        failure or f"HTTP {response.status_code}",
                        vendor_status=None if failure else response.status_code,
                    )
                if response is not None:
                    await response.aclose()
            await asyncio.sleep(delay)
        if follow and response.next_request is not None:
            redirects = request.extensions.get("mdp_redirects", 0)
            if redirects >= self.max_redirects:
                raise ServiceError("vendor_4xx", "Too many redirects")
            response.next_request.extensions["mdp_redirects"] = redirects + 1
            return await self.send(
                response.next_request, **{**kwargs, "follow_redirects": True}
            )
        return response
