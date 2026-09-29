"""Pure challenge detection over status, headers and at most the first 64 KiB.

CDN headers alone are not evidence of a challenge. No detector solves one.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup


def _parts(headers: Mapping[str, str], body: bytes) -> tuple[dict[str, str], str]:
    return {k.lower(): v.lower() for k, v in headers.items()}, body[:65536].decode(
        "utf-8", "replace"
    ).lower()


def cloudflare(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    h, b = _parts(headers, body)
    return h.get("cf-mitigated") == "challenge" or any(
        x in b for x in ("/cdn-cgi/challenge-platform/", "cf-chl-", "cf_chl_opt")
    )


def akamai(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    _, b = _parts(headers, body)
    return ("access denied" in b and "errors.edgesuite.net" in b) or (
        "akamai" in b and "verify you are human" in b
    )


def datadome(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    h, b = _parts(headers, body)
    return "x-datadome" in h or "captcha-delivery.com" in b


def perimeterx(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    _, b = _parts(headers, body)
    return any(x in b for x in ("px-captcha", "_pxcaptcha", "captcha.px-cdn.net"))


def imperva(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    _, b = _parts(headers, body)
    return any(x in b for x in ("_incapsula_resource", "incapsula incident id"))


def aws_waf(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    h, b = _parts(headers, body)
    return (
        h.get("x-amzn-waf-action") in {"challenge", "captcha"}
        or "awswaf" in b
        and ("challenge" in b or "captcha" in b)
    )


def soft_shell(
    status: int,
    headers: Mapping[str, str],
    body: bytes,
    *,
    envelope_missing: bool = False,
) -> bool:
    if status != 200 or not envelope_missing:
        return False
    soup = BeautifulSoup(body, "html.parser")
    # Hydration data with an unfamiliar shape is parser drift, not a block.
    for tag in soup.select(
        'script[type="application/json"], script#__NEXT_DATA__, script#initialState, script#serialized-server-data'
    ):
        if tag.get_text(strip=True) not in ("", "{}", "[]", "null"):
            return False
    for tag in soup.select("script, style, noscript"):
        tag.decompose()
    return any(
        node is not None and not node.get_text(strip=True)
        for node in (
            soup.select_one("#root"),
            soup.select_one("#__next"),
            soup.select_one("#app"),
        )
    )


def signature(
    status: int,
    headers: Mapping[str, str],
    body: bytes,
    *,
    envelope_missing: bool = False,
) -> str | None:
    for detector in (cloudflare, akamai, datadome, perimeterx, imperva, aws_waf):
        if detector(status, headers, body[:65536]):
            return detector.__name__
    if soft_shell(status, headers, body, envelope_missing=envelope_missing):
        return "soft_shell"
    return None


@dataclass(frozen=True)
class Classification:
    kind: str
    signature: str | None = None


def classify(
    response: httpx.Response, *, envelope_missing: bool = False
) -> Classification:
    sig = signature(
        response.status_code,
        response.headers,
        response.content,
        envelope_missing=envelope_missing,
    )
    if sig:
        return Classification("blocked", sig)
    status = response.status_code
    if status == 429:
        return Classification("throttled")
    if status in (404, 410):
        return Classification("gone")
    if status >= 500 or status == 408:
        return Classification("retryable")
    return Classification("ok" if status < 400 else "client_error")


def retry_after(value: str, *, now: datetime | None = None) -> float | None:
    if value.strip().isdigit():
        return float(value.strip())
    try:
        date = parsedate_to_datetime(value)
        return max(0, (date - (now or datetime.now(UTC))).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def envelope_value(obj: Any, path: str) -> Any:
    """Dotted keys/list offsets or JSON Pointer; a null envelope is missing."""
    keys = (
        path.lstrip("/").split("/")
        if path.startswith("/")
        else path.removeprefix("$.").split(".")
    )
    for key in keys:
        key = key.replace("~1", "/").replace("~0", "~")
        obj = obj[int(key)] if isinstance(obj, list) else obj[key]
    if obj is None:
        raise KeyError(path)
    return obj
