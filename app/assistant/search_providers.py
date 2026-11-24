"""Documented search contracts. These are not a general web crawl.

MediaWiki ``list=search`` is encyclopedia search only. See
https://www.mediawiki.org/wiki/API:Search. SearXNG ``GET /search?format=json``
is the documented JSON contract for an owner-configured instance. This module
does not start an instance and does not call a public one.
"""

from __future__ import annotations

import html
import ipaddress
import re
from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit

from app.assistant.errors import AssistantError
from app.assistant.providers import FetchedDocument
from app.assistant.text import SOURCE_TEXT_MAX, reject_secrets

QUERY_MAX = 160
RESULT_LIMIT = 3
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_STYLE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def bound_query(query: str) -> str:
    """Reject an empty, secret, or oversized search string before it is placed in a URL."""
    if not isinstance(query, str):
        raise AssistantError("assistant_input")
    cleaned = " ".join(query.split())
    if not cleaned or len(cleaned) > QUERY_MAX:
        raise AssistantError("assistant_oversize")
    reject_secrets(cleaned, source="search_query")
    return cleaned


def mediawiki_request_url(api: str, query: str, *, limit: int = RESULT_LIMIT) -> str:
    """Build the documented MediaWiki search GET. The query is not a page fetch."""
    if limit < 1 or limit > RESULT_LIMIT:
        raise AssistantError("assistant_oversize")
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": bound_query(query),
        "srlimit": str(limit),
        "srnamespace": "0",
        "srprop": "snippet|timestamp",
        "utf8": "1",
    }
    return _endpoint(api) + "?" + urlencode(params)


def searxng_request_url(endpoint: str, query: str) -> str:
    """Build the documented SearXNG JSON GET. Result URLs are not fetched."""
    params = {"q": bound_query(query), "format": "json"}
    return _endpoint(endpoint) + "?" + urlencode(params)


def normalize_mediawiki(payload: object, *, host: str) -> tuple[FetchedDocument, ...]:
    """Map ``query.search`` into excerpts. Snippet markup stays data."""
    query = _object(payload).get("query")
    if not isinstance(query, dict):
        raise AssistantError("assistant_web_rejected")
    rows = query.get("search")
    if not isinstance(rows, list):
        raise AssistantError("assistant_web_rejected")
    documents: list[FetchedDocument] = []
    for row in rows[:RESULT_LIMIT]:
        document = _mediawiki_row(row, host)
        if document is not None:
            documents.append(document)
    return tuple(documents)


def normalize_searxng(payload: object, *, host: str) -> tuple[FetchedDocument, ...]:
    """Map SearXNG ``results`` only. Other keys are ignored and not fetched."""
    rows = _object(payload).get("results")
    if not isinstance(rows, list):
        raise AssistantError("assistant_web_rejected")
    documents: list[FetchedDocument] = []
    for row in rows[:RESULT_LIMIT]:
        document = _searxng_row(row, host)
        if document is not None:
            documents.append(document)
    return tuple(documents)


def _endpoint(raw: str) -> str:
    if "?" in raw or "#" in raw:
        raise AssistantError("assistant_web_unconfigured")
    return raw


def _object(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, dict) or "error" in payload:
        raise AssistantError("assistant_web_rejected")
    return payload


def _mediawiki_row(row: object, host: str) -> FetchedDocument | None:
    if not isinstance(row, dict):
        return None
    title = row.get("title")
    snippet = row.get("snippet")
    if not isinstance(title, str) or not isinstance(snippet, str):
        return None
    title = " ".join(title.split())[:120]
    text = _plain(snippet)
    if not title or not text:
        return None
    reject_secrets(f"{title}\n{text}", source="search_result")
    stamp = row.get("timestamp", "")
    published = ""
    if isinstance(stamp, str) and _DATE.fullmatch(stamp[:10]):
        published = stamp[:10]
    return FetchedDocument(
        title=title,
        published_on=published,
        text=text,
        url_label=host,
        kind="mediawiki",
    )


def _searxng_row(row: object, host: str) -> FetchedDocument | None:
    if not isinstance(row, dict):
        return None
    title = row.get("title")
    content = row.get("content")
    if not isinstance(title, str) or not isinstance(content, str):
        return None
    title = " ".join(title.split())[:120]
    text = _plain(content)
    if not title or not text:
        return None
    reject_secrets(f"{title}\n{text}", source="search_result")
    published = _date(row.get("publishedDate"))
    return FetchedDocument(
        title=title,
        published_on=published,
        text=text,
        url_label=_result_host(row.get("url"), fallback=host),
        kind="searxng",
    )


def _plain(value: str) -> str:
    stripped = _TAG.sub(" ", _STYLE.sub(" ", _SCRIPT.sub(" ", value)))
    return " ".join(html.unescape(stripped).split())[:SOURCE_TEXT_MAX]


def _date(value: object) -> str:
    if not isinstance(value, str):
        return ""
    prefix = value[:10]
    if _DATE.fullmatch(prefix):
        return prefix
    return ""


def _result_host(value: object, *, fallback: str) -> str:
    """Keep a public result host as provenance. Never return the raw URL."""
    if not isinstance(value, str) or not value or any(char in value for char in "\r\n\0 "):
        return "nicht freigegeben"
    parts = urlsplit(value)
    if parts.username or parts.password or parts.scheme != "https" or parts.hostname is None:
        return "nicht freigegeben"
    host = parts.hostname.casefold()
    if _private_host(host):
        return "nicht freigegeben"
    return host or fallback


def _private_host(host: str) -> bool:
    if host in {"localhost"} or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global
