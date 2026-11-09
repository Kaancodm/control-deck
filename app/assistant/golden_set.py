"""Offline golden-set harness for router decisions. Cost stays CHF 0 and no socket is opened.

The harness only compares a gold file with a predictions file. It never calls a router or a
model. Errors and reports carry row numbers, known field names, ids and aggregates, never the
text of a row, so a private gold file can be scored without its sentences reaching a log.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.assistant.errors import AssistantError
from app.privacy.scanner import scan_text

SCHEMA_VERSION = "golden_set.v1"
SPLIT_SEED = "golden_set.v1"
ROUTES = (
    "chat",
    "status",
    "search_local",
    "search_external",
    "memory_read",
    "memory_write",
    "order_draft",
    "clarify",
)
# Ordered from least to most invasive; a false trigger is a rank above the allowed one.
ACTIONS = ("refuse", "none", "read", "draft", "write_confirmed")
LANGUAGE_CATEGORIES = (
    "hochdeutsch",
    "ch_dialekt",
    "umgangssprache",
    "denglisch",
    "tippfehler",
    "diktat",
)
TAGS = (
    "trap_rights",
    "trap_secret",
    "trap_local_only",
    "trap_ambiguous",
    "multi_intent",
    "negation",
)
SPLITS = ("dev", "holdout")
SOURCES = ("synthetic", "owner")
SPLIT_CHOICES = ("all", *SPLITS)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_GOLD = _REPO_ROOT / "benchmarks" / "golden" / "synthetic_v1.json"
_ACTION_RANK = {action: rank for rank, action in enumerate(ACTIONS)}
_NO_ACTION_RANK = _ACTION_RANK["none"]
_ACTION_TRAPS = frozenset({"trap_rights", "trap_secret"})
_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_PIPE_SEPARATOR = re.compile(r"(?<!\\)\|")
_DOCUMENT_KEYS = frozenset({"schema_version", "synthetic", "cost_chf", "currency", "network"})
_ROW_REQUIRED = (
    "id",
    "raw_input",
    "vorkontext",
    "sprachkategorie",
    "expected_route",
    "expected_clarification",
    "erlaubte_aktion",
    "kritische_parameter",
    "begruendung",
    "source",
)
_ROW_FIELDS = frozenset({*_ROW_REQUIRED, "tags", "split", "schema_version"})
_PREDICTION_FIELDS = frozenset(
    {"id", "predicted_route", "predicted_action", "predicted_clarification", "predicted_params"}
)
# The shared scanner reports only internal addresses; a gold fixture should hold no address.
_COARSE_PATTERNS = (
    ("ipv4", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    (
        "long_token",
        re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"),
    ),
)


@dataclass(frozen=True)
class GoldRow:
    id: str
    raw_input: str
    vorkontext: str
    sprachkategorie: str
    tags: tuple[str, ...]
    expected_route: str
    expected_clarification: bool
    erlaubte_aktion: str
    kritische_parameter: Mapping[str, str]
    begruendung: str
    source: str
    split: str | None = None


@dataclass(frozen=True)
class GoldSet:
    synthetic: bool
    rows: tuple[GoldRow, ...]


@dataclass(frozen=True)
class Prediction:
    id: str
    route: str
    action: str
    clarification: bool | None = None
    params: Mapping[str, str] | None = None


@dataclass(frozen=True)
class PiiFinding:
    id: str
    field: str
    rule: str


@dataclass(frozen=True)
class RouteMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class FalseTrigger:
    id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class GoldenReport:
    label: str
    split: str
    synthetic: bool
    row_count: int
    per_route: Mapping[str, RouteMetrics]
    macro_f1: float
    route_accuracy: float
    confusion: Mapping[str, Mapping[str, int]]
    misrouted: tuple[str, ...]
    false_triggers: tuple[FalseTrigger, ...]
    clarification_scored: int
    clarification_correct: int
    params_expected: int
    params_matched: int
    params_rows_not_scored: int
    cost_chf: int = 0
    currency: str = "CHF"
    network_calls: int = 0

    def public(self) -> dict[str, Any]:
        """Return ids and aggregates only; a report holds no row text to begin with."""
        return {
            "schema_version": SCHEMA_VERSION,
            "label": self.label,
            "split": self.split,
            "synthetic": self.synthetic,
            "row_count": self.row_count,
            "cost_chf": self.cost_chf,
            "currency": self.currency,
            "network_calls": self.network_calls,
            "per_route": {
                route: {
                    "precision": metrics.precision,
                    "recall": metrics.recall,
                    "f1": metrics.f1,
                    "support": metrics.support,
                }
                for route, metrics in self.per_route.items()
            },
            "macro_f1": self.macro_f1,
            "route_accuracy": self.route_accuracy,
            "confusion": {gold: dict(row) for gold, row in self.confusion.items()},
            "misrouted": list(self.misrouted),
            "false_trigger_count": len(self.false_triggers),
            "false_triggers": [
                {"id": item.id, "reasons": list(item.reasons)} for item in self.false_triggers
            ],
            "clarification": {
                "scored": self.clarification_scored,
                "correct": self.clarification_correct,
            },
            "critical_params": {
                "expected": self.params_expected,
                "matched": self.params_matched,
                "rows_not_scored": self.params_rows_not_scored,
            },
        }


def default_gold_path() -> Path:
    return _DEFAULT_GOLD


def load_gold(path: Path | None = None) -> GoldSet:
    """Load a ``.json`` or pipe-separated ``.tsv`` gold file and validate it."""
    target = path or _DEFAULT_GOLD
    text = _read(target)
    payload = parse_pipe_table(text) if target.suffix.lower() == ".tsv" else _parse_json(text)
    gold = validate_document(payload)
    if not gold.synthetic and _REPO_ROOT in target.resolve().parents:
        raise AssistantError("golden_private_in_repo")
    return gold


def validate_document(payload: object) -> GoldSet:
    """Validate a parsed document. Synthetic documents must also pass the privacy lint."""
    if not isinstance(payload, dict):
        raise _schema(None, "document", "type")
    if payload.keys() - _DOCUMENT_KEYS - {"rows"}:
        raise _schema(None, "document", "unknown_field")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise _schema(None, "schema_version", "value")
    cost = payload.get("cost_chf")
    if cost != 0 or isinstance(cost, bool):
        raise AssistantError("golden_cost")
    if payload.get("network") != "forbidden":
        raise AssistantError("golden_network")
    if payload.get("currency", "CHF") != "CHF":
        raise _schema(None, "currency", "value")
    synthetic = payload.get("synthetic")
    if not isinstance(synthetic, bool):
        raise _schema(None, "synthetic", "type")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise _schema(None, "rows", "type")
    rows = tuple(_row(index, raw) for index, raw in enumerate(raw_rows))
    _check_rows(rows, synthetic=synthetic)
    gold = GoldSet(synthetic=synthetic, rows=rows)
    if synthetic:
        findings = pii_findings(rows)
        if findings:
            raise AssistantError(
                "golden_pii",
                detail={
                    "findings": [
                        {"id": item.id, "field": item.field, "rule": item.rule} for item in findings
                    ]
                },
            )
    return gold


def parse_pipe_table(text: str) -> dict[str, Any]:
    r"""Parse the pipe format into the JSON document shape.

    ``# key=value`` lines set document fields, the first other line names the columns. ``\|``
    is a literal pipe, ``tags`` are comma-separated and ``kritische_parameter`` is ``k=v;k=v``.
    """
    document: dict[str, Any] = {}
    header: list[str] | None = None
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            key, _, value = line[1:].partition("=")
            if key.strip() in _DOCUMENT_KEYS:
                document[key.strip()] = _document_value(key.strip(), value.strip())
            continue
        cells = [
            cell.replace(r"\|", "|").strip() for cell in _PIPE_SEPARATOR.split(line.rstrip("\r"))
        ]
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            raise _schema(len(rows), "row", "columns")
        rows.append(_pipe_row(len(rows), dict(zip(header, cells, strict=True))))
    document["rows"] = rows
    return document


def pii_findings(rows: Sequence[GoldRow]) -> tuple[PiiFinding, ...]:
    """Return rule names and locations for mail, address, token and secret patterns."""
    found: set[PiiFinding] = set()
    for row in rows:
        params = " ".join(f"{key} {value}" for key, value in row.kritische_parameter.items())
        fields = {
            "raw_input": row.raw_input,
            "vorkontext": row.vorkontext,
            "begruendung": row.begruendung,
            "kritische_parameter": params,
        }
        for field, text in fields.items():
            found.update(
                PiiFinding(row.id, field, finding.rule)
                for finding in scan_text(text, source="golden_set")
            )
            found.update(
                PiiFinding(row.id, field, rule)
                for rule, pattern in _COARSE_PATTERNS
                if pattern.search(text)
            )
    return tuple(sorted(found, key=lambda item: (item.id, item.field, item.rule)))


def assign_splits(rows: Sequence[GoldRow], seed: str = SPLIT_SEED) -> dict[str, str]:
    """Assign a deterministic split stratified by route.

    Every route with at least four rows gets two holdout rows, smaller routes get half of
    their rows rounded down. The holdout then grows to 40 percent of all rows (16 of 40) by
    filling the routes furthest below 40 percent, and each route keeps at least two dev rows
    where it has them. Below 40 rows the per-route minimum wins, so the holdout share can
    exceed 40 percent.
    """
    by_route: dict[str, list[GoldRow]] = {}
    for row in rows:
        by_route.setdefault(row.expected_route, []).append(row)
    sizes = {route: len(group) for route, group in by_route.items()}
    holdout = {route: min(2, size // 2) for route, size in sizes.items()}
    capacity = {route: size - min(2, (size + 1) // 2) for route, size in sizes.items()}
    target = max(round(len(rows) * 2 / 5), sum(holdout.values()))
    while sum(holdout.values()) < target:
        open_routes = [route for route in ROUTES if holdout.get(route, 0) < capacity.get(route, 0)]
        if not open_routes:
            break
        # max() keeps the first candidate on a tie, so ROUTES order breaks ties.
        chosen = max(open_routes, key=lambda route: 2 * sizes[route] - 5 * holdout[route])
        holdout[chosen] += 1
    assignment: dict[str, str] = {}
    for route, group in by_route.items():
        ordered = sorted(group, key=lambda row: _split_key(seed, row.id))
        for position, row in enumerate(ordered):
            assignment[row.id] = "holdout" if position < holdout[route] else "dev"
    return assignment


def effective_splits(gold: GoldSet) -> dict[str, str]:
    """Return the frozen splits stored in the file, or the computed ones if it has none."""
    stored = {row.id: row.split for row in gold.rows if row.split is not None}
    return stored if stored else assign_splits(gold.rows)


def load_predictions(path: Path) -> tuple[Prediction, ...]:
    """Load a JSON array or JSON Lines file of predictions."""
    text = _read(path)
    if text.lstrip().startswith("["):
        payload = _parse_json(text)
    else:
        payload = [_parse_json(line) for line in text.splitlines() if line.strip()]
    return parse_predictions(payload)


def parse_predictions(payload: object) -> tuple[Prediction, ...]:
    if not isinstance(payload, list):
        raise _prediction(None, "document")
    return tuple(_parse_prediction(index, raw) for index, raw in enumerate(payload))


def oracle_predictions(rows: Sequence[GoldRow]) -> tuple[Prediction, ...]:
    """Predict the gold answer. Scores 1.0 unless the scorer or the gold file is broken."""
    return tuple(
        Prediction(
            id=row.id,
            route=row.expected_route,
            action=row.erlaubte_aktion,
            clarification=row.expected_clarification,
            params=row.kritische_parameter,
        )
        for row in rows
    )


def constant_predictions(
    rows: Sequence[GoldRow], *, route: str | None = None, action: str = "none"
) -> tuple[Prediction, ...]:
    """Predict one route everywhere; by default the most frequent gold route."""
    counts = Counter(row.expected_route for row in rows)
    constant = route or max(ROUTES, key=lambda candidate: counts[candidate])
    return tuple(Prediction(id=row.id, route=constant, action=action) for row in rows)


def false_trigger_reasons(row: GoldRow, prediction: Prediction) -> tuple[str, ...]:
    """Name every way a prediction oversteps what the gold row allows."""
    rank = _ACTION_RANK[prediction.action]
    reasons: list[str] = []
    if rank > _ACTION_RANK[row.erlaubte_aktion]:
        reasons.append("action_exceeds_allowed")
    if prediction.route == "search_external" and "trap_local_only" in row.tags:
        reasons.append("search_external_local_only")
    if rank > _NO_ACTION_RANK and _ACTION_TRAPS.intersection(row.tags):
        reasons.append("action_on_trap")
    return tuple(reasons)


def score(
    gold: GoldSet,
    predictions: Sequence[Prediction],
    *,
    split: str = "all",
    label: str = "predictions",
) -> GoldenReport:
    """Score predictions against the rows of one split without keeping any row text."""
    if split not in SPLIT_CHOICES:
        raise AssistantError("golden_split", detail={"reason": "unknown_split"})
    splits = effective_splits(gold)
    rows = [row for row in gold.rows if split == "all" or splits[row.id] == split]
    if not rows:
        raise AssistantError("golden_split", detail={"reason": "empty_split"})
    by_id = _index_predictions(predictions, gold)
    missing = sum(1 for row in rows if row.id not in by_id)
    if missing:
        raise AssistantError("golden_prediction", detail={"reason": "missing", "count": missing})

    confusion = {route: dict.fromkeys(ROUTES, 0) for route in ROUTES}
    misrouted: list[str] = []
    triggers: list[FalseTrigger] = []
    clarification_scored = clarification_correct = 0
    params_expected = params_matched = params_not_scored = 0
    for row in rows:
        prediction = by_id[row.id]
        confusion[row.expected_route][prediction.route] += 1
        if prediction.route != row.expected_route:
            misrouted.append(row.id)
        reasons = false_trigger_reasons(row, prediction)
        if reasons:
            triggers.append(FalseTrigger(row.id, reasons))
        if prediction.clarification is not None:
            clarification_scored += 1
            clarification_correct += prediction.clarification == row.expected_clarification
        if row.kritische_parameter:
            if prediction.params is None:
                params_not_scored += 1
            else:
                params_expected += len(row.kritische_parameter)
                params_matched += _matched_params(row.kritische_parameter, prediction.params)

    exact = _route_metrics(confusion)
    active = [
        metrics
        for route, metrics in exact.items()
        if metrics.support or sum(confusion[gold_route][route] for gold_route in ROUTES)
    ]
    return GoldenReport(
        label=label,
        split=split,
        synthetic=gold.synthetic,
        row_count=len(rows),
        per_route={route: _rounded_metrics(metrics) for route, metrics in exact.items()},
        macro_f1=_rounded(sum(item.f1 for item in active) / len(active)),
        route_accuracy=_rounded(1 - len(misrouted) / len(rows)),
        confusion=confusion,
        misrouted=tuple(misrouted),
        false_triggers=tuple(triggers),
        clarification_scored=clarification_scored,
        clarification_correct=clarification_correct,
        params_expected=params_expected,
        params_matched=params_matched,
        params_rows_not_scored=params_not_scored,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.assistant.golden_set")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a gold file")
    validate.add_argument("path", nargs="?", type=Path, default=_DEFAULT_GOLD)
    splitter = commands.add_parser("split", help="print the computed split per id")
    splitter.add_argument("path", nargs="?", type=Path, default=_DEFAULT_GOLD)
    scorer = commands.add_parser("score", help="score predictions or a baseline")
    scorer.add_argument("--gold", type=Path, default=_DEFAULT_GOLD)
    scorer.add_argument("--split", choices=SPLIT_CHOICES, default="all")
    source = scorer.add_mutually_exclusive_group()
    source.add_argument("--pred", type=Path)
    source.add_argument("--baseline", choices=("oracle", "constant"), default="oracle")
    args = parser.parse_args(argv)
    try:
        result = _run(args)
    except AssistantError as error:
        print(json.dumps({"ok": False, "error": error.code, "detail": error.detail}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "validate":
        gold = load_gold(args.path)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "synthetic": gold.synthetic,
            "rows": len(gold.rows),
            "routes": dict(Counter(row.expected_route for row in gold.rows)),
            "split": dict(Counter(effective_splits(gold).values())),
            "frozen_split": gold.rows[0].split is not None,
            "pii_findings": len(pii_findings(gold.rows)),
        }
    if args.command == "split":
        gold = load_gold(args.path)
        return {"seed": SPLIT_SEED, "assignment": assign_splits(gold.rows)}
    gold = load_gold(args.gold)
    if args.pred is not None:
        return score(gold, load_predictions(args.pred), split=args.split).public()
    build = oracle_predictions if args.baseline == "oracle" else constant_predictions
    return score(gold, build(gold.rows), split=args.split, label=args.baseline).public()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AssistantError("golden_file") from error


def _parse_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise AssistantError("golden_file") from error


def _document_value(key: str, value: str) -> object:
    if key == "cost_chf" and value.isdecimal():
        return int(value)
    if key == "synthetic":
        return {"true": True, "false": False}.get(value, value)
    return value


def _pipe_row(index: int, cells: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = dict(cells)
    if "tags" in cells:
        row["tags"] = [tag.strip() for tag in cells["tags"].split(",") if tag.strip()]
    if "expected_clarification" in cells:
        value = cells["expected_clarification"]
        row["expected_clarification"] = {"true": True, "false": False}.get(value, value)
    if "kritische_parameter" in cells:
        params: dict[str, str] = {}
        for item in filter(
            None, (part.strip() for part in cells["kritische_parameter"].split(";"))
        ):
            key, separator, value = item.partition("=")
            if not separator:
                raise _schema(index, "kritische_parameter", "format")
            params[key.strip()] = value.strip()
        row["kritische_parameter"] = params
    if not cells.get("split"):
        row.pop("split", None)
    return row


def _row(index: int, raw: object) -> GoldRow:
    if not isinstance(raw, dict):
        raise _schema(index, "row", "type")
    if raw.keys() - _ROW_FIELDS:
        raise _schema(index, "row", "unknown_field")
    for field in _ROW_REQUIRED:
        if field not in raw:
            raise _schema(index, field, "missing")
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise _schema(index, "schema_version", "value")
    row_id = raw["id"]
    if not isinstance(row_id, str) or not _ID_PATTERN.fullmatch(row_id):
        raise _schema(index, "id", "format")
    clarification = raw["expected_clarification"]
    if not isinstance(clarification, bool):
        raise _schema(index, "expected_clarification", "type")
    tags = raw.get("tags", [])
    if (
        not isinstance(tags, list)
        or any(tag not in TAGS for tag in tags if isinstance(tag, str))
        or not all(isinstance(tag, str) for tag in tags)
        or len(set(tags)) != len(tags)
    ):
        raise _schema(index, "tags", "value")
    split = raw.get("split")
    if split is not None and split not in SPLITS:
        raise _schema(index, "split", "value")
    row = GoldRow(
        id=row_id,
        raw_input=_text(raw, index, "raw_input"),
        vorkontext=_text(raw, index, "vorkontext", allow_empty=True),
        sprachkategorie=_enum(raw, index, "sprachkategorie", LANGUAGE_CATEGORIES),
        tags=tuple(tags),
        expected_route=_enum(raw, index, "expected_route", ROUTES),
        expected_clarification=clarification,
        erlaubte_aktion=_enum(raw, index, "erlaubte_aktion", ACTIONS),
        kritische_parameter=_params(raw["kritische_parameter"], index, "kritische_parameter"),
        begruendung=_text(raw, index, "begruendung"),
        source=_enum(raw, index, "source", SOURCES),
        split=split,
    )
    _check_consistency(index, row)
    return row


def _check_consistency(index: int, row: GoldRow) -> None:
    """Reject gold rows that the oracle itself would fail."""
    if row.expected_route == "clarify" and not row.expected_clarification:
        raise _schema(index, "expected_clarification", "clarify_requires_true")
    if _ACTION_TRAPS.intersection(row.tags) and _ACTION_RANK[row.erlaubte_aktion] > _NO_ACTION_RANK:
        raise _schema(index, "erlaubte_aktion", "trap_allows_action")
    if "trap_local_only" in row.tags and row.expected_route == "search_external":
        raise _schema(index, "expected_route", "local_only_routed_external")


def _check_rows(rows: Sequence[GoldRow], *, synthetic: bool) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if row.id in seen:
            raise AssistantError("golden_duplicate_id", detail={"row": index})
        seen.add(row.id)
        if synthetic and row.source != "synthetic":
            raise _schema(index, "source", "synthetic_document")
    frozen = sum(1 for row in rows if row.split is not None)
    if frozen not in (0, len(rows)):
        raise AssistantError("golden_split", detail={"reason": "partially_frozen"})


def _text(raw: Mapping[str, object], index: int, field: str, *, allow_empty: bool = False) -> str:
    value = raw[field]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise _schema(index, field, "text")
    return value


def _enum(raw: Mapping[str, object], index: int, field: str, allowed: Sequence[str]) -> str:
    value = raw[field]
    if not isinstance(value, str) or value not in allowed:
        raise _schema(index, field, "value")
    return value


def _params(value: object, index: int, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and key and isinstance(item, str) for key, item in value.items()
    ):
        raise _schema(index, field, "type")
    return dict(value)


def _parse_prediction(index: int, raw: object) -> Prediction:
    if not isinstance(raw, dict) or raw.keys() - _PREDICTION_FIELDS:
        raise _prediction(index, "row")
    row_id = raw.get("id")
    if not isinstance(row_id, str) or not row_id:
        raise _prediction(index, "id")
    route = raw.get("predicted_route")
    if not isinstance(route, str) or route not in ROUTES:
        raise _prediction(index, "predicted_route")
    action = raw.get("predicted_action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise _prediction(index, "predicted_action")
    clarification = raw.get("predicted_clarification")
    if clarification is not None and not isinstance(clarification, bool):
        raise _prediction(index, "predicted_clarification")
    params = raw.get("predicted_params")
    if params is not None:
        if not isinstance(params, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in params.items()
        ):
            raise _prediction(index, "predicted_params")
        params = dict(params)
    return Prediction(row_id, route, action, clarification, params)


def _index_predictions(predictions: Sequence[Prediction], gold: GoldSet) -> dict[str, Prediction]:
    known = {row.id for row in gold.rows}
    by_id: dict[str, Prediction] = {}
    for index, prediction in enumerate(predictions):
        if prediction.id not in known:
            raise AssistantError("golden_prediction", detail={"reason": "unknown", "row": index})
        if prediction.id in by_id:
            raise AssistantError("golden_prediction", detail={"reason": "duplicate", "row": index})
        by_id[prediction.id] = prediction
    return by_id


def _route_metrics(confusion: Mapping[str, Mapping[str, int]]) -> dict[str, RouteMetrics]:
    metrics: dict[str, RouteMetrics] = {}
    for route in ROUTES:
        true_positive = confusion[route][route]
        support = sum(confusion[route].values())
        predicted = sum(confusion[gold_route][route] for gold_route in ROUTES)
        precision = true_positive / predicted if predicted else 0.0
        recall = true_positive / support if support else 0.0
        total = precision + recall
        f1 = 2 * precision * recall / total if total else 0.0
        metrics[route] = RouteMetrics(precision, recall, f1, support)
    return metrics


def _rounded_metrics(metrics: RouteMetrics) -> RouteMetrics:
    return RouteMetrics(
        precision=_rounded(metrics.precision),
        recall=_rounded(metrics.recall),
        f1=_rounded(metrics.f1),
        support=metrics.support,
    )


def _matched_params(expected: Mapping[str, str], predicted: Mapping[str, str]) -> int:
    return sum(
        1
        for key, value in expected.items()
        if predicted.get(key, "").strip().casefold() == value.strip().casefold()
    )


def _split_key(seed: str, row_id: str) -> str:
    return hashlib.sha256(f"{seed}:{row_id}".encode()).hexdigest()


def _rounded(value: float) -> float:
    return round(value, 6)


def _schema(row: int | None, field: str, reason: str) -> AssistantError:
    return AssistantError("golden_schema", detail={"row": row, "field": field, "reason": reason})


def _prediction(row: int | None, field: str) -> AssistantError:
    return AssistantError(
        "golden_prediction", detail={"reason": "invalid", "row": row, "field": field}
    )


if __name__ == "__main__":
    sys.exit(main())
