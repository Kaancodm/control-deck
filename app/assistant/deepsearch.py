"""Bounded Deep Search. ``local_only`` never reaches a web provider."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.assistant.errors import AssistantError
from app.assistant.providers import DisabledWebSearch, SearchAdapter
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


def run_deep_search(
    *,
    question: str,
    data_class: str,
    sources: Sequence[CorpusSource],
    web: SearchAdapter | None = None,
) -> DeepSearchResult:
    """Plan at most four steps and keep every gap, conflict, and partial read visible."""
    cleaned = bounded(question, limit=MESSAGE_MAX)
    checked = require_data_class(data_class)
    reject_secrets(cleaned, source="search_question")
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
    network_calls = 0
    if web_needed:
        plan.append("Externe Suche nur ueber den gesperrten Provider")
        if checked == "local_only":
            blocked = True
            gaps.append("Externe Suche blockiert, weil local_only keinen Netzabruf erlaubt.")
        else:
            try:
                provider.search(cleaned, data_class=checked)
            except AssistantError as exc:
                if exc.code != "live_search_disabled":
                    raise
                refusals += 1
                gaps.append("Live-Suche ist aus. Kein externer Abruf.")
            reported = getattr(provider, "network_calls", 0)
            if isinstance(reported, int):
                network_calls = reported
    found, partial, unread = _read_local(cleaned, sources)
    gaps.extend(unread)
    if not found and not unread:
        gaps.append("Keine passende lokale Quelle.")
    contradictions = _contradictions(cleaned, sources)
    incomplete = partial or blocked or refusals > 0
    if incomplete:
        evidence_status = "incomplete"
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
