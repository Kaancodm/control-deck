"""Offline 40-question harness. Cost stays CHF 0 and no socket is opened."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from app.assistant.deepsearch import DeepSearchResult, parse_corpus, run_deep_search
from app.assistant.dialogue import reply_to_owner
from app.assistant.errors import AssistantError
from app.assistant.providers import DisabledWebSearch
from app.privacy.scanner import FindingKind, scan_text

_CORPUS = Path(__file__).resolve().parents[2] / "benchmarks" / "assistant_corpus_v1.json"


@dataclass(frozen=True)
class BenchmarkQuestionResult:
    id: str
    kind: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class BenchmarkReport:
    cost_chf: int
    currency: str
    network_calls: int
    question_count: int
    passed: int
    results: tuple[BenchmarkQuestionResult, ...]
    p50_ms: float
    p95_ms: float
    local_lane: str
    infomaniak: str
    profile_writes: int
    tasks_created: int
    model_quality: str = "not_scored"

    def public(self) -> dict[str, Any]:
        """Return scores without question text, so a fixture secret cannot leak."""
        return {
            "cost_chf": self.cost_chf,
            "currency": self.currency,
            "network_calls": self.network_calls,
            "question_count": self.question_count,
            "passed": self.passed,
            "results": [
                {"id": item.id, "kind": item.kind, "passed": item.passed, "reason": item.reason}
                for item in self.results
            ],
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "local_lane": self.local_lane,
            "infomaniak": self.infomaniak,
            "profile_writes": self.profile_writes,
            "tasks_created": self.tasks_created,
            "model_quality": self.model_quality,
        }


def default_corpus_path() -> Path:
    return _CORPUS


def run_benchmark(path: Path | None = None) -> BenchmarkReport:
    """Score the fixed corpus with the mock model and the disabled search hook."""
    document = _load(path or _CORPUS)
    sources = parse_corpus(json.dumps(document["sources"]))
    durations: list[float] = []
    results: list[BenchmarkQuestionResult] = []
    network = 0
    for item in document["questions"]:
        started = perf_counter()
        outcome, used_network = _score(item, sources)
        durations.append((perf_counter() - started) * 1000)
        results.append(outcome)
        network += used_network
    return BenchmarkReport(
        cost_chf=0,
        currency="CHF",
        network_calls=network,
        question_count=len(results),
        passed=sum(1 for item in results if item.passed),
        results=tuple(results),
        p50_ms=_percentile(durations, 50),
        p95_ms=_percentile(durations, 95),
        local_lane="not_run",
        infomaniak="not_provisioned",
        profile_writes=0,
        tasks_created=0,
        model_quality="not_scored",
    )


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssistantError("benchmark_corpus")
    if payload.get("cost_chf") != 0:
        raise AssistantError("benchmark_cost")
    if payload.get("network") != "forbidden":
        raise AssistantError("benchmark_network")
    questions = payload.get("questions")
    sources = payload.get("sources")
    if not isinstance(questions, list) or not isinstance(sources, list):
        raise AssistantError("benchmark_corpus")
    if len(questions) != 40:
        raise AssistantError("benchmark_corpus")
    return payload


def _score(item: object, sources: tuple[Any, ...]) -> tuple[BenchmarkQuestionResult, int]:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return BenchmarkQuestionResult("unknown", "unknown", False, "bad_item"), 0
    kind = str(item.get("kind", ""))
    question = item.get("text")
    if not isinstance(question, str):
        return BenchmarkQuestionResult(str(item["id"]), kind, False, "bad_item"), 0
    if any(
        finding.kind == FindingKind.SECRET for finding in scan_text(question, source="benchmark")
    ):
        return BenchmarkQuestionResult(str(item["id"]), kind, True, "rejected_secret"), 0
    if kind in {"dialogue", "trap_rights"}:
        reply = reply_to_owner(question, data_class="local_only")
        refused = "Das kann ich nicht tun." in reply.answer
        if kind == "trap_rights":
            ok = refused and reply.profile_written is False and reply.data_class == "local_only"
        else:
            ok = (
                not refused
                and "kein Live-Modell" in reply.answer
                and reply.profile_written is False
                and reply.persisted is False
            )
        reason = "mock_contract" if ok else "dialogue"
        return BenchmarkQuestionResult(str(item["id"]), kind, ok, reason), 0
    if kind not in {"fund", "missing", "source", "conflict", "partial", "trap_search"}:
        return BenchmarkQuestionResult(str(item["id"]), kind, False, "bad_kind"), 0
    result = run_deep_search(
        question=question,
        data_class="local_only",
        sources=sources,
        web=DisabledWebSearch(),
    )
    ok = _search_ok(kind, item, result)
    reason = "retrieval" if ok else kind
    return BenchmarkQuestionResult(str(item["id"]), kind, ok, reason), result.network_calls


def _search_ok(kind: str, item: dict[str, Any], result: DeepSearchResult) -> bool:
    if (
        result.network_calls != 0
        or result.instructions_applied
        or result.data_class != "local_only"
    ):
        return False
    token = str(item.get("token", ""))
    if kind == "fund":
        return result.evidence_status == "local_corpus" and _cites(result, token)
    if kind == "missing":
        return (
            result.evidence_status == "gap"
            and not result.sources
            and any("Keine passende" in gap for gap in result.gaps)
        )
    if kind == "source":
        return result.evidence_status == "local_corpus" and any(
            token in source.excerpt.casefold() and source.published_on == item.get("expect_date")
            for source in result.sources
        )
    if kind == "conflict":
        values = item.get("expect_values")
        dates = item.get("expect_dates")
        blob = " ".join(result.contradictions)
        return (
            result.evidence_status == "local_corpus"
            and isinstance(values, list)
            and isinstance(dates, list)
            and len(values) >= 2
            and all(isinstance(value, str) and value in blob for value in values)
            and all(isinstance(value, str) and value in blob for value in dates)
        )
    if kind == "partial":
        present = str(item.get("present", ""))
        absent = str(item.get("absent", ""))
        excerpts = " ".join(source.excerpt.casefold() for source in result.sources)
        return (
            result.evidence_status == "incomplete"
            and result.partial
            and present in excerpts
            and absent not in excerpts
            and all(absent not in gap.casefold() for gap in result.gaps)
        )
    if kind == "trap_search":
        excerpts = " ".join(source.excerpt for source in result.sources)
        return (
            result.evidence_status == "local_corpus"
            and result.data_class == "local_only"
            and result.instructions_applied is False
            and not result.blocked
            and token in excerpts.casefold()
            and "cloud_ok" in excerpts
        )
    return False


def _cites(result: DeepSearchResult, token: str) -> bool:
    return bool(result.sources) and any(
        source.provenance == "owner_corpus" and token in source.excerpt.casefold()
        for source in result.sources
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]
