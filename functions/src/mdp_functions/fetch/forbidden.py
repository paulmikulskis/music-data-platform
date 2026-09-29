"""The runtime-wide forbidden-path list. No DSP web-player token, token bootstrap, or
internal API is ever called: the refusal sits in every client transport the runtime builds, so a request
the traced client sends, an auth flow's follow-up, an event hook's rewrite, a redirect hop, and the HTTP/1.1
and proxy clients are all checked, whatever a function's declared hosts allow. It is one list, not a
per-function declaration.

Three forms, all refused with `forbidden_path`: listed paths (`FORBIDDEN`), hosts where only listed
paths pass (`ALLOWLISTS`), and request rules matched on method, path, query and body where a path cannot say
enough (`REQUEST_RULES`). Which tab or file a frozen spec names is the calling function's check; the rules
here fix the request's shape."""

import re
from collections.abc import Callable
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import parse_qsl, unquote

import httpx

from mdp_functions.errors import ServiceError
from mdp_functions.fetch.guard import DOC, source_layer, transport_network

# (host pattern, path prefix or "" for every path on the host, what it is). Each has an example URL in
# functions/tests/test_forbidden_paths.py.
FORBIDDEN = (
    (r"open\.spotify\.com", "/api/token", "Spotify web-player token bootstrap"),
    (r"open\.spotify\.com", "/get_access_token", "Spotify web-player token bootstrap"),
    (r"clienttoken\.spotify\.com", "", "Spotify client token"),
    (r"api-partner\.spotify\.com", "/pathfinder", "Spotify internal GraphQL API"),
    (r"(?:[a-z0-9-]+\.)*[a-z0-9-]*spclient[a-z0-9-]*(?:\.[a-z0-9-]+)*\.spotify\.com", "", "Spotify spclient host"),
    (r"amp-api[a-z0-9-]*\.music\.apple\.com", "", "Apple Music web app catalog API (developer token)"),
    (r"www\.shazam\.com", "/shazam/v1", "Shazam internal API"),
    (r"www\.shazam\.com", "/shazam/v3", "Shazam internal API"),
)
_COMPILED = [(re.compile(host), path, what) for host, path, what in FORBIDDEN]

# (host pattern, the only paths that pass as full matches on the normalized path, what any other path is).
ALLOWLISTS = (
    (r"api\.typesafe\.ai", (r"/v1/systemone",), "TypeSafe path outside the decision endpoint"),
    (
        r"(?:labs\.)?api\.listenbrainz\.org",
        (
            r"/1/popularity/(?:recording|artist|release|release-group)",
            r"/1/stats/sitewide(?:/.*)?",
            r"/1/explore/fresh-releases(?:/.*)?",
            r"/similar-artists/json",
        ),
        "ListenBrainz path outside its allowlist (user, listener and donor endpoints return usernames)",
    ),
)
_ALLOWLISTS = [(re.compile(host), [re.compile(path) for path in paths], what) for host, paths, what in ALLOWLISTS]


MODEL_HOSTS = re.compile(
    r"(?:.*\.)?(?:openai\.com|anthropic\.com|typesafe\.ai|typesafe\.com|"
    r"openrouter\.ai|together\.xyz|groq\.com|generativelanguage\.googleapis\.com|"
    r"bedrock-runtime\..*\.amazonaws\.com|.*\.openai\.azure\.com)"
)
MODEL_PATHS = re.compile(r"/(?:v[0-9]+/)?(?:chat/completions|responses|messages|embeddings|completions|typesafe/v1/systemone)(?:/|$)")
MODEL_MESSAGE = (
    "Model calls outside gold are refused because enrichment needs a prompt, budget and lineage; "
    "land the page in bronze, then use a gold llm_step "
    "(docs/operating.md#add-a-local-llm-step); typed decisions use ctx.jev (docs/jev.md)."
)
COOKIE_MESSAGE = (
    "Cookies are refused because visitor sessions cannot be reused; fetch the public page "
    f"and observe Set-Cookie names only ({DOC})."
)


def refuse_model(request, layer, model_hosts=()):
    host = request.url.host.lower().rstrip(".")
    if layer != "gold" and (
        MODEL_HOSTS.fullmatch(host) or host in model_hosts or "litellm" in host
        or "typesafe" in host or MODEL_PATHS.search(normalize(request.url.path))
    ):
        raise ServiceError("forbidden_path", MODEL_MESSAGE)


class NoCookies(CookieJar):
    def __init__(self):
        super().__init__(policy=DefaultCookiePolicy(allowed_domains=[]))

    def set_cookie(self, cookie, *args, **kwargs):
        raise ServiceError("forbidden_path", COOKIE_MESSAGE)

    def extract_cookies(self, response, request):
        # Headers remain observable, but response values never enter a session jar.
        return None


def decoded(value: str) -> str:
    """A path segment, query key or value percent-decoded until stable."""
    for _ in range(4):
        value, previous = unquote(value), value
        if value == previous:
            break
    return value


def segments(path: str) -> list[str] | None:
    """The raw path split on `/` first, then each segment percent-decoded until stable, lower-cased, with its
    `;params` cut and empty segments dropped. None when a decoded segment holds a `/`, `\\` or NUL: a server
    that decodes before routing reads more segments than were checked."""
    parts = []
    for segment in path.split("/"):
        segment = decoded(segment).lower().split(";", 1)[0]
        if any(c in segment for c in "/\\\x00"):
            return None
        if segment:
            parts.append(segment)
    return parts


def readings(path: str) -> list[str] | None:
    """The paths a server may route: the decoded segments as sent, and the same with `.` and `..` resolved."""
    parts = segments(path)
    if parts is None:
        return None
    resolved: list[str] = []
    for segment in parts:
        if segment == "..":
            if resolved:
                resolved.pop()
        elif segment != ".":
            resolved.append(segment)
    return ["/" + "/".join(parts), "/" + "/".join(resolved)]


def normalize(path: str) -> str:
    """The resolved reading (a decoded separator reads as one), which the request rules match."""
    views = readings(path)
    return views[1] if views is not None else normalize(decoded(path).replace("\\", "/").split("\x00", 1)[0])


def listed(host: str) -> bool:
    """A host some list entry, allowlist or request rule names."""
    patterns = [p for p, _, _ in _COMPILED] + [p for p, _, _ in _ALLOWLISTS] + [p for p, _ in _RULES]
    return any(pattern.fullmatch(host) for pattern in patterns)


def forbidden(host: str, path: str) -> str | None:
    """What a forbidden URL is, or None. Hosts compare case-insensitively. On a listed host a path is
    refused when any reading matches a listed prefix (the prefix itself or anything below it), when any
    reading leaves a host's allowlist, or when a segment decodes to a separator."""
    host = host.lower().rstrip(".")
    if not listed(host):
        return None
    views = readings(path)
    if views is None:
        return "a path segment that decodes to /, \\ or NUL on a listed host"
    for pattern, prefix, what in _COMPILED:
        if pattern.fullmatch(host) and any(not prefix or v == prefix or v.startswith(prefix + "/") for v in views):
            return what
    for pattern, paths, what in _ALLOWLISTS:
        if pattern.fullmatch(host) and not all(any(allowed.fullmatch(v) for allowed in paths) for v in views):
            return what
    return None


def params(request: httpx.Request) -> list[tuple[str, str]]:
    """The query string's pairs, repeated keys kept, each key and value fully decoded."""
    query = request.url.query.decode("ascii", "replace")
    return [(decoded(k), decoded(v)) for k, v in parse_qsl(query, keep_blank_values=True)]


def has_body(request: httpx.Request) -> bool:
    return request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers




def typesafe(request: httpx.Request, path: str) -> str | None:
    if request.method != "POST" or path not in {"/v1/systemone", "/typesafe/v1/systemone"} or request.url.query:
        return "Jev allows only POST to its decision endpoint without query parameters"
    return None


# (host pattern, rule); a rule returns why a request is refused, or None.
REQUEST_RULES: tuple[tuple[str, Callable[[httpx.Request, str], str | None]], ...] = (
    (r"api\.typesafe\.ai", typesafe),
)
_RULES = [(re.compile(host), rule) for host, rule in REQUEST_RULES]


def header_host(value: str) -> str:
    """The host of a Host header value (`host`, `host:port`, or `[v6]:port`)."""
    value = value.strip().lower()
    return value[1 : value.find("]")] if value.startswith("[") else value.split(":", 1)[0]


# The request extensions the runtime sets; httpcore acts on others (`target` rewrites the request-target,
# `trace` hands a callback the live request before its headers are written, `sni_hostname` changes the TLS
# name), so any other is refused.
REQUEST_EXTENSIONS = frozenset({"timeout", "mdp_accept", "mdp_redirects"})
# The response extensions a caller may read; `network_stream` would let it write raw bytes to the connection.
RESPONSE_EXTENSIONS = frozenset({"http_version", "reason_phrase", "stream_id"})


def refuse(request: httpx.Request) -> None:
    """Raise forbidden_path for a listed URL, a path outside a host's allowlist, or a request a host's rule
    refuses, also when a Host header (the HTTP/2 :authority) names the host. A Host header naming another
    authority than the URL's, and a request extension outside REQUEST_EXTENSIONS, are refused too. Hosts
    compare in their ASCII (IDNA) form, as the Host header carries them."""
    if "cookie" in request.headers:
        raise ServiceError("forbidden_path", COOKIE_MESSAGE)
    extra = sorted(set(request.extensions) - REQUEST_EXTENSIONS)
    if extra:
        raise ServiceError("forbidden_path", f"Request extensions outside {sorted(REQUEST_EXTENSIONS)} are refused: {extra}; extensions can change routing; use ctx.http without transport overrides ({DOC}).")
    raw_path = request.url.raw_path.split(b"?", 1)[0].decode("ascii", "replace")
    url_host = request.url.raw_host.decode("ascii", "replace").lower().rstrip(".")
    authority = header_host(request.headers.get("host") or url_host).rstrip(".")
    for host in dict.fromkeys((url_host, authority)):
        what = forbidden(host, raw_path)
        if what:
            raise ServiceError("forbidden_path", f"{host}{raw_path} is on the runtime forbidden-path list: {what}; private APIs cannot be scraped; use a public page or feed ({DOC}).")
    if authority != url_host:
        raise ServiceError("forbidden_path", f"A Host header naming another authority than the URL is refused because it changes routing; put the destination in the URL ({DOC}).")
    path = normalize(raw_path)
    # A LiteLLM host is configured per installation. Reserve this passthrough family on every host.
    if any("typesafe" in view.split("/") for view in (readings(raw_path) or [normalize(raw_path)])):
        why = typesafe(request, path)
        if why or readings(raw_path) != ["/typesafe/v1/systemone"] * 2:
            raise ServiceError("forbidden_path", why or "Jev passthrough must use its exact path")
    for pattern, rule in _RULES:
        why = rule(request, path) if pattern.fullmatch(url_host) else None
        if why:
            raise ServiceError("forbidden_path", f"{url_host}{raw_path} is refused by its request rule: {why}; use the documented public request shape ({DOC}).")


class RuntimeStream(httpx.AsyncByteStream):
    """Permit deferred transport I/O without granting the response consumer network access."""

    def __init__(self, inner):
        self.inner = inner

    async def __aiter__(self):
        with transport_network():
            iterator = self.inner.__aiter__()
        while True:
            with transport_network():
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    return
            yield chunk

    async def aclose(self):
        with transport_network():
            await self.inner.aclose()


class RefusingTransport(httpx.AsyncBaseTransport):
    """Checks every request against the forbidden-path list before the wrapped transport sends it, and hands
    back no connection handle with the response."""

    def __init__(self, inner: httpx.AsyncBaseTransport, layer=None, model_hosts=()) -> None:
        self.inner = inner
        self.layer, self.model_hosts = layer, model_hosts

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        refuse_model(request, source_layer(self.layer), self.model_hosts)
        refuse(request)
        with transport_network():
            response = await self.inner.handle_async_request(request)
        response.stream = RuntimeStream(response.stream)
        for key in set(response.extensions) - RESPONSE_EXTENSIONS:
            del response.extensions[key]
        return response

    async def aclose(self) -> None:
        with transport_network():
            await self.inner.aclose()


class RefusingClient(httpx.AsyncClient):
    """An httpx client whose transport, proxy mounts and explicit `mounts=` all refuse the forbidden-path list."""

    def _merge_cookies(self, cookies=None):
        if cookies or self.cookies:
            raise ServiceError("forbidden_path", COOKIE_MESSAGE)
        return super()._merge_cookies(cookies)

    @property
    def cookies(self):
        return self._cookies

    @cookies.setter
    def cookies(self, value):
        raise ServiceError("forbidden_path", COOKIE_MESSAGE)

    def __init__(self, *args: Any, mounts: dict[str, httpx.AsyncBaseTransport | None] | None = None, **kwargs: Any) -> None:
        # Bare service clients enrich; source clients inherit or explicitly supply their layer.
        self._layer = kwargs.pop("layer", source_layer("gold"))
        self._model_hosts = kwargs.pop("model_hosts", ())
        cookies = kwargs.pop("cookies", None)
        if cookies:
            raise ServiceError("forbidden_path", COOKIE_MESSAGE)
        kwargs["cookies"] = NoCookies()
        if mounts is not None:
            mounts = {key: None if transport is None or isinstance(transport, RefusingTransport)
                      else RefusingTransport(transport, self._layer, self._model_hosts) for key, transport in mounts.items()}
        super().__init__(*args, mounts=mounts, **kwargs)

    def _init_transport(self, *args: Any, **kwargs: Any) -> httpx.AsyncBaseTransport:
        return RefusingTransport(super()._init_transport(*args, **kwargs), self._layer, self._model_hosts)

    def _init_proxy_transport(self, *args: Any, **kwargs: Any) -> httpx.AsyncBaseTransport:
        return RefusingTransport(super()._init_proxy_transport(*args, **kwargs), self._layer, self._model_hosts)
