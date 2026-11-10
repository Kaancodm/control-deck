"""Bounded HTTPS fetch and documented search providers.

Production stays off until the owner selects ``mediawiki`` or ``searxng``.
MediaWiki search is encyclopedia-only. SearXNG is a configured instance, not a
public service this process starts. ``for_fixture`` is test-only. ``local_only``
and ``cloud_redacted`` raise before a connection.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urljoin, urlsplit

from app.assistant.errors import AssistantError
from app.assistant.providers import FetchedDocument
from app.assistant.text import SOURCE_TEXT_MAX, reject_secrets, require_data_class

_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TITLE = re.compile(r"<title>([^<]{1,120})</title>", re.IGNORECASE)
_TIME = re.compile(
    r"""<time[^>]*datetime=["'](\d{4}-\d{2}-\d{2})""",
    re.IGNORECASE,
)
_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_STYLE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class _Body(Protocol):
    status: int

    def getheader(self, name: str, default: str | None = None) -> str | None: ...

    def read(self, amt: int) -> bytes: ...

    def close(self) -> None: ...


class _Sock(Protocol):
    def getpeername(self) -> tuple[object, ...]: ...


class _Conn(Protocol):
    sock: _Sock | None

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> _Body: ...

    def close(self) -> None: ...


Opener = Callable[[str, str, int], _Conn]
Resolver = Callable[..., list[tuple[object, ...]]]


def _public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _split_url(raw: str, *, https_only: bool, limit: int = 200) -> tuple[str, str, int, str]:
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > limit
        or any(char in raw for char in "\r\n\0 ")
    ):
        raise AssistantError("assistant_web_blocked")
    parts = urlsplit(raw)
    if parts.username or parts.password or parts.fragment:
        raise AssistantError("assistant_web_blocked")
    if parts.scheme not in {"https", "http"}:
        raise AssistantError("assistant_web_blocked")
    if https_only and parts.scheme != "https":
        raise AssistantError("assistant_web_blocked")
    host = parts.hostname
    if host is None or host != host.casefold():
        raise AssistantError("assistant_web_blocked")
    try:
        port = 443 if parts.scheme == "https" and parts.port is None else parts.port
        if parts.scheme == "http" and port is None:
            port = 80
        if port is None or not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise AssistantError("assistant_web_blocked") from None
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    if path.startswith("//") or "\\" in path:
        raise AssistantError("assistant_web_blocked")
    return parts.scheme, host, port, path


def parse_allowlist(raw: str) -> frozenset[tuple[str, int]]:
    """Parse ``host`` or ``host:port`` entries. Private addresses and schemes are rejected."""
    if not raw.strip() or len(raw) > 2000:
        raise AssistantError("assistant_web_unconfigured")
    found: set[tuple[str, int]] = set()
    for item in raw.split(","):
        token = item.strip().casefold()
        if not token or "://" in token or "/" in token or "@" in token:
            raise AssistantError("assistant_web_unconfigured")
        host, separator, port_text = token.rpartition(":")
        if separator and port_text.isdigit() and host:
            port = int(port_text)
            name = host
        else:
            port = 443
            name = token
        if not 1 <= port <= 65535 or not _HOST.fullmatch(name):
            raise AssistantError("assistant_web_unconfigured")
        try:
            ip = ipaddress.ip_address(name)
        except ValueError:
            ip = None
        if ip is not None and not _public_ip(ip):
            raise AssistantError("assistant_web_unconfigured")
        found.add((name, port))
    if not found:
        raise AssistantError("assistant_web_unconfigured")
    return frozenset(found)


@dataclass
class _Target:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.path}"


class BoundedFetch:
    """One connection per hop, no proxy, no credential header, and a byte cap."""

    def __init__(
        self,
        *,
        allow: frozenset[tuple[str, int]],
        https_only: bool,
        timeout_s: float,
        max_bytes: int,
        search_url: str | None,
        fixture: bool,
        resolver: Resolver | None = None,
        opener: Opener | None = None,
    ) -> None:
        if not allow or not 0 < timeout_s <= 10 or not 1 <= max_bytes <= 16384:
            raise AssistantError("assistant_web_unconfigured")
        self.allow = allow
        self.https_only = https_only
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.search_url = search_url
        self.fixture = fixture
        self._resolver = resolver
        self._opener = opener
        self.calls = 0

    @classmethod
    def for_fixture(
        cls,
        port: int,
        *,
        search_path: str | None = None,
        timeout_s: float = 1.0,
        max_bytes: int = 4096,
    ) -> BoundedFetch:
        """Test-only loopback pin. ``load_search`` never calls this."""
        if not 1 <= port <= 65535:
            raise AssistantError("assistant_web_blocked")
        search = None
        if search_path is not None:
            if not search_path.startswith("/") or ".." in search_path:
                raise AssistantError("assistant_web_blocked")
            search = f"http://127.0.0.1:{port}{search_path}"
        return cls(
            allow=frozenset({("127.0.0.1", port)}),
            https_only=False,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            search_url=search,
            fixture=True,
        )

    def fetch_url(self, raw: str, *, cancelled: Callable[[], bool]) -> FetchedDocument:
        target = self._target(raw)
        return self._get(target, cancelled=cancelled, hops_left=1)

    def get_unfollowed(
        self, raw: str, *, cancelled: Callable[[], bool], limit: int = 800
    ) -> tuple[bytes, str, bool]:
        """GET one URL and refuse redirects so a query cannot ride to another host."""
        target = self._target(raw, limit=limit)
        return self._transfer(target, method="GET", body=None, cancelled=cancelled)

    def _target(self, raw: str, *, limit: int = 200) -> _Target:
        scheme, host, port, path = _split_url(raw, https_only=self.https_only, limit=limit)
        if (host, port) not in self.allow:
            raise AssistantError("assistant_web_blocked")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if self.fixture:
            if ip is None or not ip.is_loopback:
                raise AssistantError("assistant_web_blocked")
        elif ip is not None and not _public_ip(ip):
            raise AssistantError("assistant_web_blocked")
        elif ip is None:
            self._require_public_dns(host, port)
        return _Target(scheme, host, port, path)

    def _require_public_dns(self, host: str, port: int) -> None:
        resolver = self._resolver or socket.getaddrinfo
        try:
            infos = resolver(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except OSError as exc:
            raise AssistantError("assistant_web_rejected") from exc
        if not infos:
            raise AssistantError("assistant_web_blocked")
        for info in infos:
            sockaddr = info[4]
            if not isinstance(sockaddr, tuple) or not sockaddr:
                raise AssistantError("assistant_web_blocked")
            try:
                ip = ipaddress.ip_address(str(sockaddr[0]))
            except ValueError as exc:
                raise AssistantError("assistant_web_blocked") from exc
            if not _public_ip(ip):
                raise AssistantError("assistant_web_blocked")

    def _open(self, target: _Target) -> _Conn:
        if self._opener is not None:
            return self._opener(target.scheme, target.host, target.port)
        if target.scheme == "https":
            context = ssl.create_default_context()
            return cast(
                _Conn,
                http.client.HTTPSConnection(
                    target.host, target.port, timeout=self.timeout_s, context=context
                ),
            )
        if self.fixture and target.scheme == "http":
            return cast(
                _Conn,
                http.client.HTTPConnection(target.host, target.port, timeout=self.timeout_s),
            )
        raise AssistantError("assistant_web_blocked")

    def _transfer(
        self,
        target: _Target,
        *,
        method: str,
        body: bytes | None,
        cancelled: Callable[[], bool],
    ) -> tuple[bytes, str, bool]:
        """POST does not follow redirects, so a query cannot ride to another host."""
        if cancelled():
            raise AssistantError("assistant_cancelled")
        conn = self._open(target)
        self.calls += 1
        try:
            self._require_peer(conn, target)
            headers = {
                "Accept": "application/json, text/html, text/plain",
                "User-Agent": "control-deck-assistant/0 (local search)",
                "Connection": "close",
            }
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                conn.request(method, target.path, body=body, headers=headers)
                response = conn.getresponse()
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                raise AssistantError("assistant_web_rejected") from exc
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    raise AssistantError("assistant_web_blocked")
                if response.status != 200:
                    raise AssistantError("assistant_web_rejected")
                content_type = (response.getheader("Content-Type") or "").split(";", 1)[0]
                payload, truncated = _read_capped(response, self.max_bytes, cancelled=cancelled)
                return payload, content_type.casefold().strip(), truncated
            finally:
                response.close()
        finally:
            conn.close()

    def _get(
        self, target: _Target, *, cancelled: Callable[[], bool], hops_left: int
    ) -> FetchedDocument:
        if hops_left < 0:
            raise AssistantError("assistant_web_blocked")
        payload, content_type, truncated, redirect = self._read_get(target, cancelled=cancelled)
        if redirect is not None:
            if hops_left == 0:
                raise AssistantError("assistant_web_blocked")
            nxt = self._target(urljoin(target.origin, redirect))
            return self._get(nxt, cancelled=cancelled, hops_left=hops_left - 1)
        docs = _documents(payload, content_type, truncated, target.host)
        if len(docs) != 1:
            raise AssistantError("assistant_web_rejected")
        return docs[0]

    def _read_get(
        self, target: _Target, *, cancelled: Callable[[], bool]
    ) -> tuple[bytes, str, bool, str | None]:
        if cancelled():
            raise AssistantError("assistant_cancelled")
        conn = self._open(target)
        self.calls += 1
        try:
            self._require_peer(conn, target)
            try:
                conn.request(
                    "GET",
                    target.path,
                    body=None,
                    headers={
                        "Accept": "application/json, text/html, text/plain",
                        "User-Agent": "control-deck-assistant/0 (local search)",
                        "Connection": "close",
                    },
                )
                response = conn.getresponse()
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                raise AssistantError("assistant_web_rejected") from exc
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise AssistantError("assistant_web_rejected")
                    return b"", "", False, location
                if response.status != 200:
                    raise AssistantError("assistant_web_rejected")
                content_type = (response.getheader("Content-Type") or "").split(";", 1)[0]
                payload, truncated = _read_capped(response, self.max_bytes, cancelled=cancelled)
                return payload, content_type.casefold().strip(), truncated, None
            finally:
                response.close()
        finally:
            conn.close()

    def _require_peer(self, conn: _Conn, target: _Target) -> None:
        # Connect before reading the peer. HTTPConnection connects on request,
        # so a caller that injected a live socket already connected. For the
        # stdlib client, connect now and then check the peer before the request
        # body is written when the socket is still closed.
        connect = getattr(conn, "connect", None)
        if callable(connect) and getattr(conn, "sock", None) is None:
            try:
                connect()
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                raise AssistantError("assistant_web_rejected") from exc
        sock = conn.sock
        if sock is None:
            raise AssistantError("assistant_web_rejected")
        try:
            peer = sock.getpeername()
            ip = ipaddress.ip_address(str(peer[0]))
            if type(peer[1]) is not int:
                raise ValueError
            port = peer[1]
        except (OSError, TypeError, ValueError, IndexError) as exc:
            raise AssistantError("assistant_web_rejected") from exc
        if self.fixture:
            if not ip.is_loopback or (target.host, port) not in self.allow:
                raise AssistantError("assistant_web_blocked")
            return
        if not _public_ip(ip):
            raise AssistantError("assistant_web_blocked")


def _read_capped(
    response: _Body, max_bytes: int, *, cancelled: Callable[[], bool]
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        if cancelled():
            raise AssistantError("assistant_cancelled")
        try:
            part = response.read(min(1024, max_bytes + 1 - total))
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise AssistantError("assistant_web_rejected") from exc
        if not part:
            break
        total += len(part)
        chunks.append(part)
        if total > max_bytes:
            return b"".join(chunks)[:max_bytes], True
    return b"".join(chunks), False


def _documents(
    payload: bytes, content_type: str, truncated: bool, host: str
) -> tuple[FetchedDocument, ...]:
    if content_type not in {"application/json", "text/html", "text/plain"}:
        raise AssistantError("assistant_web_rejected")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise AssistantError("assistant_web_rejected") from exc
    if "\0" in text:
        raise AssistantError("assistant_web_rejected")
    reject_secrets(text, source="fetched_body")
    if content_type == "application/json":
        return _json_documents(text, truncated, host)
    if content_type == "text/html":
        title, published, body = _html_parts(text)
        return (
            FetchedDocument(
                title=title,
                published_on=published,
                text=body,
                url_label=host,
                truncated=truncated,
            ),
        )
    body = " ".join(text.split())[:SOURCE_TEXT_MAX]
    if not body:
        raise AssistantError("assistant_web_rejected")
    return (
        FetchedDocument(
            title="Text",
            published_on="",
            text=body,
            url_label=host,
            truncated=truncated,
        ),
    )


def _json_documents(text: str, truncated: bool, host: str) -> tuple[FetchedDocument, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssistantError("assistant_web_rejected") from exc
    rows: list[object]
    if isinstance(payload, dict) and "results" in payload:
        results = payload.get("results")
        if not isinstance(results, list) or len(results) > 5:
            raise AssistantError("assistant_web_rejected")
        rows = list(results)
    elif isinstance(payload, dict):
        rows = [payload]
    else:
        raise AssistantError("assistant_web_rejected")
    documents: list[FetchedDocument] = []
    for row in rows:
        documents.append(_one_json(row, truncated, host))
    if not documents:
        raise AssistantError("assistant_web_rejected")
    return tuple(documents)


def _one_json(row: object, truncated: bool, host: str) -> FetchedDocument:
    if not isinstance(row, dict):
        raise AssistantError("assistant_web_rejected")
    title = row.get("title")
    body = row.get("text")
    if not isinstance(title, str) or not isinstance(body, str):
        raise AssistantError("assistant_web_rejected")
    title = " ".join(title.split())
    body = " ".join(body.split())[:SOURCE_TEXT_MAX]
    if not title or len(title) > 120 or not body:
        raise AssistantError("assistant_web_rejected")
    published = row.get("published_on", "")
    if not isinstance(published, str) or (published and _DATE.fullmatch(published) is None):
        raise AssistantError("assistant_web_rejected")
    claim = row.get("claim", "")
    value = row.get("value", "")
    if not isinstance(claim, str) or not isinstance(value, str):
        raise AssistantError("assistant_web_rejected")
    if len(claim) > 80 or len(value) > 80:
        raise AssistantError("assistant_web_rejected")
    return FetchedDocument(
        title=title,
        published_on=published,
        text=body,
        url_label=host,
        claim=claim,
        value=value,
        truncated=truncated,
    )


def _html_parts(raw: str) -> tuple[str, str, str]:
    title_match = _TITLE.search(raw)
    time_match = _TIME.search(raw)
    stripped = _TAG.sub(" ", _STYLE.sub(" ", _SCRIPT.sub(" ", raw)))
    body = " ".join(stripped.split())[:SOURCE_TEXT_MAX]
    if not body:
        raise AssistantError("assistant_web_rejected")
    title = "Ohne Titel"
    if title_match:
        title = " ".join(title_match.group(1).split())[:120] or title
    published = time_match.group(1) if time_match else ""
    return title, published, body


class UnconfiguredWeb:
    """Visible missing-config provider. It does not open a socket."""

    def __init__(self) -> None:
        self.network_calls = 0
        self.refusals = 0

    def search(self, query: str, *, data_class: str) -> tuple[FetchedDocument, ...]:
        del query
        self._gate(data_class)
        raise AssistantError("assistant_web_unconfigured")

    def fetch(self, urls: Sequence[str], *, data_class: str) -> tuple[FetchedDocument, ...]:
        del urls
        self._gate(data_class)
        raise AssistantError("assistant_web_unconfigured")

    def _gate(self, data_class: str) -> None:
        checked = require_data_class(data_class)
        if checked == "local_only":
            raise AssistantError("assistant_data_class")


class GovernedWeb:
    """Owner-configured fetch and one documented search provider."""

    def __init__(self, client: BoundedFetch, *, provider_id: str = "", endpoint: str = "") -> None:
        self.client = client
        self.provider_id = provider_id
        self.endpoint = endpoint

    @classmethod
    def for_fixture(cls, port: int, *, provider: str) -> GovernedWeb:
        """Speak a documented contract to a loopback fixture. Production never uses this."""
        if provider == "mediawiki":
            endpoint = f"http://127.0.0.1:{port}/w/api.php"
        elif provider == "searxng":
            endpoint = f"http://127.0.0.1:{port}/search"
        else:
            raise AssistantError("assistant_web_unconfigured")
        return cls(BoundedFetch.for_fixture(port), provider_id=provider, endpoint=endpoint)

    @property
    def network_calls(self) -> int:
        return self.client.calls

    def search(self, query: str, *, data_class: str) -> tuple[FetchedDocument, ...]:
        self._gate(data_class)
        if self.provider_id == "mediawiki":
            from app.assistant.search_providers import mediawiki_request_url, normalize_mediawiki

            requested = mediawiki_request_url(self.endpoint, query)
            normalise = normalize_mediawiki
        elif self.provider_id == "searxng":
            from app.assistant.search_providers import normalize_searxng, searxng_request_url

            requested = searxng_request_url(self.endpoint, query)
            normalise = normalize_searxng
        else:
            raise AssistantError("assistant_web_unconfigured")
        payload, content_type, truncated = self.client.get_unfollowed(
            requested, cancelled=lambda: False
        )
        if truncated or content_type != "application/json":
            raise AssistantError("assistant_web_rejected")
        try:
            decoded = payload.decode("utf-8")
            parsed = json.loads(decoded)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AssistantError("assistant_web_rejected") from exc
        host = urlsplit(self.endpoint).hostname or ""
        return normalise(parsed, host=host)

    def fetch(self, urls: Sequence[str], *, data_class: str) -> tuple[FetchedDocument, ...]:
        self._gate(data_class)
        if len(urls) > 3:
            raise AssistantError("assistant_input")
        pages = tuple(self.client.fetch_url(url, cancelled=lambda: False) for url in urls)
        return pages

    def _gate(self, data_class: str) -> None:
        checked = require_data_class(data_class)
        if checked != "cloud_ok":
            raise AssistantError("assistant_data_class")


def load_search(environment: Mapping[str, str]) -> UnconfiguredWeb | GovernedWeb:
    """Build the production provider. Loopback entries are not an allowlist."""
    if environment.get("DECK_ASSISTANT_WEB") != "1":
        return UnconfiguredWeb()
    provider = environment.get("DECK_ASSISTANT_SEARCH_PROVIDER", "").strip()
    allow_raw = environment.get("DECK_ASSISTANT_WEB_ALLOWLIST", "").strip()
    if allow_raw:
        allow = parse_allowlist(allow_raw)
    else:
        allow = frozenset()
    if provider not in {"mediawiki", "searxng"} or not allow:
        return UnconfiguredWeb()
    if provider == "mediawiki":
        endpoint = environment.get("DECK_ASSISTANT_MEDIAWIKI_API", "").strip()
        expected = "/api.php"
    else:
        endpoint = environment.get("DECK_ASSISTANT_SEARXNG_URL", "").strip()
        expected = "/search"
    if not endpoint:
        raise AssistantError("assistant_web_unconfigured")
    scheme, host, port, path = _split_url(endpoint, https_only=True)
    if scheme != "https" or (host, port) not in allow or "?" in path or not path.endswith(expected):
        raise AssistantError("assistant_web_unconfigured")
    return GovernedWeb(
        BoundedFetch(
            allow=allow,
            https_only=True,
            timeout_s=3,
            max_bytes=16384,
            search_url=None,
            fixture=False,
        ),
        provider_id=provider,
        endpoint=endpoint,
    )
