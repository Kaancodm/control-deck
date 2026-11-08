"""Bounded Deep Search. ``local_only`` never reaches a web provider."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.assistant.errors import AssistantError
from app.assistant.providers import DisabledWebSearch, FetchedDocument, SearchAdapter
from app.assistant.text import (
    CORPUS_LIMIT,
    MESSAGE_MAX,
    SOURCE_TEXT_MAX,
    bounded,
    reject_secrets,
    require_data_class,
)

_WEB_MARKERS = ("internet", "webseite", "aktuell", "nachrichten", "heute online")
_TOKEN = re.compile(r"[a-z0-9]{5,}")
_STOP = frozenset(
    {
        "finde",
        "datum",
        "welche",
        "welches",
        "welcher",
        "dieser",
        "dieses",
        "diese",
        "nicht",
        "keine",
        "einer",
        "einen",
        "einem",
        "haben",
        "steht",
        "frage",
        "notiz",
        "wort",
        "farbe",
        "sagen",
        "traegt",
        "heisst",
        "lautet",
    }
)


@dataclass(frozen=True)
class CorpusSource:
    title: str
    published_on: str
    text: str
    claim: str | None = None
    value: str | None = None
    readable: bool = True


@dataclass(frozen=True)
class Evidence:
    title: str
    published_on: str
    excerpt: str
    provenance: str = "owner_corpus"


@dataclass(frozen=True)
class DeepSearchResult:
    data_class: str
    plan: tuple[str, ...]
    sources: tuple[Evidence, ...]
    contradictions: tuple[str, ...]
    gaps: tuple[str, ...]
    partial: bool
    blocked: bool
    network_calls: int
    hook_refusals: int
    instructions_applied: bool
    question_origin: str
    evidence_status: str

    @property
    def evidence_label(self) -> str:
        """German UI label. Corpus excerpts are not a model judgement."""
        labels = {
            "local_corpus": "Beleg aus dem lokalen Korpus, kein Modellurteil",
            "owner_fetch": "Abruf nach Owner-Freigabe, kein Modellurteil",
            "incomplete": "Unvollständig, kein Modellurteil",
            "gap": "Keine belegte Quelle",
        }
        return labels.get(self.evidence_status, "Unbekannter Belegstatus")


def needs_web(question: str) -> bool:
    text = question.casefold()
    return any(marker in text for marker in _WEB_MARKERS)


def parse_corpus(raw: str) -> tuple[CorpusSource, ...]:
    """Read an owner-supplied local corpus. An empty box is an empty corpus."""
    if raw.strip() == "":
        return ()
    if len(raw) > 20_000:
        raise AssistantError("assistant_oversize")
    reject_secrets(raw, source="corpus")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssistantError("assistant_input") from exc
    if not isinstance(payload, list) or len(payload) > CORPUS_LIMIT:
        raise AssistantError("assistant_input")
    sources: list[CorpusSource] = []
    for item in payload:
        sources.append(_source(item))
    return tuple(sources)


_WEB_GAPS = {
    "live_search_disabled": "Live-Suche ist aus. Kein externer Abruf.",
    "assistant_web_unconfigured": (
        "Web nicht konfiguriert. Es fehlen DECK_ASSISTANT_WEB, "
        "DECK_ASSISTANT_WEB_ALLOWLIST und DECK_ASSISTANT_SEARCH_URL."
    ),
    "assistant_web_blocked": (
        "Adresse nicht freigegeben. Kein privates Netz und keine fremde Umleitung."
    ),
    "assistant_web_rejected": "Der Abruf wurde abgewiesen.",
    "assistant_cancelled": "Der Abruf wurde abgebrochen.",
}


def run_deep_search(
    *,
    question: str,
    data_class: str,
    sources: Sequence[CorpusSource],
    web: SearchAdapter | None = None,
    allow_external: bool = False,
    fetch_urls: Sequence[str] = (),
) -> DeepSearchResult:
    """Plan at most four steps and keep every gap, conflict, and partial read visible."""
    cleaned = bounded(question, limit=MESSAGE_MAX)
    checked = require_data_class(data_class)
    reject_secrets(cleaned, source="search_question")
    addresses = tuple(fetch_urls)
    if len(addresses) > 3 or any(len(item) > 200 or not item.strip() for item in addresses):
        raise AssistantError("assistant_input")
    for item in addresses:
        reject_secrets(item, source="fetch_url")
    provider = web if web is not None else DisabledWebSearch()
    web_needed = needs_web(cleaned)
    plan = [
        f"Teilfrage: {cleaned[:180]}",
        "Lokale Quellen lesen",
        "Uebereinstimmung und Widerspruch pruefen",
    ]
    gaps: list[str] = []
    blocked = False
    refusals = 0
    external: list[FetchedDocument] = []
    if web_needed or addresses:
        plan.append("Externer Abruf nur nach Owner-Freigabe und Allowlist")
    if web_needed:
        pages, gap, refused = _take_external(
            lambda: provider.search(cleaned, data_class=checked),
            data_class=checked,
            allow_external=allow_external,
            hold=_hold_gap(checked, allow_external, kind="suche"),
        )
        blocked = blocked or gap is not None
        refusals += refused
        if gap is not None:
            gaps.append(gap)
        external.extend(pages)
    if addresses:
        pages, gap, refused = _take_external(
            lambda: provider.fetch(addresses, data_class=checked),
            data_class=checked,
            allow_external=allow_external,
            hold=_hold_gap(checked, allow_external, kind="adressen"),
        )
        blocked = blocked or gap is not None
        refusals += refused
        if gap is not None:
            gaps.append(gap)
        external.extend(pages)
    reported = provider.network_calls
    network_calls = reported if isinstance(reported, int) else 0
    found, partial, unread = _read_local(cleaned, sources)
    gaps.extend(unread)
    for document in external:
        if not document.published_on:
            gaps.append(f"Datum fehlt: {document.title}")
        if document.truncated:
            gaps.append(f"Antwort gekuerzt: {document.title}")
        found.append(
            Evidence(
                document.title,
                document.published_on,
                _proven_excerpt(document.text),
                provenance="owner_fetch",
            )
        )
    if not found and not unread and not external:
        gaps.append("Keine passende lokale Quelle.")
    extra = tuple(
        CorpusSource(
            document.title,
            document.published_on,
            document.text,
            document.claim or None,
            document.value or None,
        )
        for document in external
    )
    contradictions = _contradictions(cleaned, tuple(sources) + extra)
    if external and contradictions:
        gaps.append("Unsicher, weil mitgelieferte Claim-Paare sich widersprechen.")
    date_missing = any(not document.published_on or document.truncated for document in external)
    incomplete = partial or blocked or refusals > 0 or date_missing
    if incomplete:
        evidence_status = "incomplete"
    elif any(item.provenance == "owner_fetch" for item in found):
        evidence_status = "owner_fetch"
    elif found:
        evidence_status = "local_corpus"
    else:
        evidence_status = "gap"
    return DeepSearchResult(
        data_class=checked,
        plan=tuple(plan[:4]),
        sources=tuple(found[:5]),
        contradictions=tuple(contradictions),
        gaps=tuple(gaps),
        partial=incomplete,
        blocked=blocked,
        network_calls=network_calls,
        hook_refusals=refusals,
        instructions_applied=False,
        question_origin="owner",
        evidence_status=evidence_status,
    )


def _hold_gap(data_class: str, allow_external: bool, *, kind: str) -> str:
    if data_class == "local_only":
        if kind == "suche":
            return "Externe Suche blockiert, weil local_only keinen Netzabruf erlaubt."
        return "Externe Adressen blockiert, weil local_only keinen Netzabruf erlaubt."
    if data_class == "cloud_redacted":
        return "cloud_redacted verlaesst den Host nicht ohne Redaktion."
    if not allow_external:
        if kind == "suche":
            return "Externe Suche braucht die Owner-Freigabe."
        return "Externe Adressen brauchen die Owner-Freigabe."
    return "Externer Abruf bleibt zu."


def _take_external(
    action: Callable[[], tuple[FetchedDocument, ...]],
    *,
    data_class: str,
    allow_external: bool,
    hold: str,
) -> tuple[tuple[FetchedDocument, ...], str | None, int]:
    """Return pages, a visible gap, and hook refusals. Policy holds never call ``action``."""
    if data_class != "cloud_ok" or not allow_external:
        return (), hold, 0
    try:
        pages = action()
    except AssistantError as exc:
        if exc.code in {
            "assistant_secret",
            "assistant_data_class",
            "assistant_input",
            "assistant_oversize",
        }:
            raise
        gap = _WEB_GAPS.get(exc.code)
        if gap is None:
            raise
        return (), gap, 1 if exc.code == "live_search_disabled" else 0
    return pages, None, 0


def _source(item: object) -> CorpusSource:
    if not isinstance(item, dict):
        raise AssistantError("assistant_input")
    title = item.get("title")
    published = item.get("published_on")
    text = item.get("text")
    if not all(isinstance(value, str) for value in (title, published, text)):
        raise AssistantError("assistant_input")
    assert isinstance(title, str) and isinstance(published, str) and isinstance(text, str)
    if not title.strip() or len(title) > 120 or len(published) > 32 or len(text) > SOURCE_TEXT_MAX:
        raise AssistantError("assistant_input")
    claim = item.get("claim")
    value = item.get("value")
    if claim is not None and not isinstance(claim, str):
        raise AssistantError("assistant_input")
    if value is not None and not isinstance(value, str):
        raise AssistantError("assistant_input")
    readable = item.get("readable", True)
    if not isinstance(readable, bool):
        raise AssistantError("assistant_input")
    return CorpusSource(
        title=title.strip(),
        published_on=published.strip(),
        text=text,
        claim=claim,
        value=value,
        readable=readable,
    )


def _tokens(text: str) -> set[str]:
    return {word for word in _TOKEN.findall(text.casefold()) if word not in _STOP}


def _read_local(
    question: str, sources: Sequence[CorpusSource]
) -> tuple[list[Evidence], bool, list[str]]:
    wanted = _tokens(question)
    found: list[Evidence] = []
    unread: list[str] = []
    partial = False
    for source in sources:
        haystack = f"{source.title} {source.text}".casefold()
        if wanted and not any(token in haystack for token in wanted):
            continue
        if not source.readable:
            partial = True
            unread.append(f"Quelle nicht lesbar: {source.title}")
            continue
        excerpt = _proven_excerpt(source.text)
        found.append(
            Evidence(source.title, source.published_on, excerpt, provenance="owner_corpus")
        )
    if not wanted:
        return [], False, ["Keine passende lokale Quelle."]
    return found, partial, unread


def _proven_excerpt(text: str) -> str:
    """Keep the excerpt a slice of the owner text. Do not invent a citation."""
    normalized = " ".join(text.split())
    excerpt = normalized[:240]
    if excerpt not in normalized:
        raise AssistantError("assistant_input")
    return excerpt


def _contradictions(question: str, sources: Sequence[CorpusSource]) -> list[str]:
    """Report only owner-supplied claim/value pairs. Free text is not inferred."""
    wanted = _tokens(question)
    grouped: dict[str, dict[str, tuple[str, str]]] = {}
    for source in sources:
        if not source.readable or not source.claim or not source.value:
            continue
        haystack = f"{source.title} {source.text}".casefold()
        if wanted and not any(token in haystack for token in wanted):
            continue
        grouped.setdefault(source.claim, {})[source.value] = (source.title, source.published_on)
    notes: list[str] = []
    for claim, values in grouped.items():
        if len(values) < 2:
            continue
        ordered = sorted(values, key=str.casefold)
        rendered = " gegen ".join(
            f"{value} ({values[value][0]}, {values[value][1]})" for value in ordered
        )
        notes.append(f"Widerspruch bei {claim}: {rendered}.")
    return notes


def corpus_from_mappings(rows: Sequence[Mapping[str, object]]) -> tuple[CorpusSource, ...]:
    return tuple(_source(dict(row)) for row in rows)
