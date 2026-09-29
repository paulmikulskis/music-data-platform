"""Explicit, country-pinned Webshare sessions; never a response to a block.

API/username format: https://apidocs.webshare.io/proxy-config and
https://apidocs.webshare.io/proxy-connection . Credentials stay in memory.
Proxy provider budgets use uuid5(NAMESPACE_URL, 'mdp:proxy:<name>') as scope_id; a vendor provider's
request cap uses 'mdp:provider:<name>' (budget.vendor_scope_id).
"""

import asyncio
import base64
import hashlib
import re
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5
from weakref import WeakKeyDictionary

import httpx
from pydantic import SecretStr

from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient
from mdp_functions.fetch.logging import protect_transport_logs


class ProxyProvider(Protocol):
    name: str
    price_microcents_per_byte: Decimal

    async def proxy_url(
        self, run_id: str, target_id: str, country: str
    ) -> SecretStr: ...
    def redact(self, text: str) -> str: ...


def provider_scope_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "mdp:proxy:" + name))


def market_country(params: dict[str, Any], override: str | None = None) -> str:
    market = params.get("market") or params.get("storefront")
    if not isinstance(market, str) or not re.fullmatch("[A-Za-z]{2}", market):
        raise ServiceError(
            "proxy_market_required",
            "Residential transport requires a two-letter target market or storefront",
        )
    country = override or market
    if not re.fullmatch("[A-Za-z]{2}", country):
        raise ServiceError(
            "proxy_market_required", "Proxy country must be a two-letter country code"
        )
    if country.lower() != market.lower():
        raise ServiceError(
            "variant_mismatch",
            "Proxy country disagrees with frozen market; export a new variant",
        )
    return country.lower()


class Webshare:
    name = "webshare"

    def __init__(
        self,
        api_key: SecretStr,
        price_microcents_per_byte: Decimal = Decimal(0),
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self.price_microcents_per_byte = price_microcents_per_byte
        self._transport = transport
        self._credentials: tuple[str, str] | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()
        self._secrets: set[str] = set()

    def redact(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    async def proxy_url(self, run_id: str, target_id: str, country: str) -> SecretStr:
        if not re.fullmatch("[a-z]{2}", country):
            raise ServiceError("proxy_market_required", "Invalid proxy country")
        async with self._lock:
            if not self._credentials or monotonic() >= self._expires:
                if not self._key.get_secret_value():
                    raise ServiceError(
                        "proxy_unavailable", "Webshare API key is not configured"
                    )
                self._secrets.add(self._key.get_secret_value())
                try:
                    protect_transport_logs()
                    async with RefusingClient(
                        transport=self._transport, trust_env=False, timeout=10
                    ) as client:
                        response = await client.get(
                            "https://proxy.webshare.io/api/v2/proxy/config/",
                            headers={
                                "Authorization": "Token " + self._key.get_secret_value()
                            },
                        )
                    if response.status_code in (402, 407):
                        raise ServiceError(
                            "proxy_quota_exhausted",
                            "Webshare quota or subscription requires review",
                        )
                    response.raise_for_status()
                    config = response.json()
                    self._credentials = (config["username"], config["password"])
                    if not all(isinstance(v, str) and v for v in self._credentials):
                        raise ValueError("Invalid credentials")
                except ServiceError:
                    raise
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    raise ServiceError(
                        "proxy_unavailable", "Webshare configuration unavailable"
                    ) from None
                self._secrets.update(self._credentials)
                self._expires = monotonic() + 300
            username, password = self._credentials
        session = str(
            int(hashlib.sha256(f"{run_id}:{target_id}".encode()).hexdigest()[:15], 16)
        )
        username = f"{username}-{country}-{session}"
        self._secrets.update(
            (
                username,
                quote(username, safe=""),
                quote(password, safe=""),
                base64.b64encode(f"{username}:{password}".encode()).decode(),
            )
        )
        url = f"http://{quote(username, safe='')}:{quote(password, safe='')}@p.webshare.io:80"
        self._secrets.add(url)
        return SecretStr(url)


_PROVIDERS: WeakKeyDictionary = WeakKeyDictionary()


def webshare_provider(api_key: SecretStr, price: Decimal) -> Webshare:
    """Share config credentials across batches on the api process's event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # AsyncClient may be constructed synchronously before its event loop starts.
        return Webshare(api_key, price)
    providers = _PROVIDERS.setdefault(loop, {})
    key = (hashlib.sha256(api_key.get_secret_value().encode()).digest(), price)
    if key not in providers:
        providers[key] = Webshare(api_key, price)
    return providers[key]
