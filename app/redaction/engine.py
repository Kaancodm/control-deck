"""Pure task-scoped pseudonymization and one-pass local rehydration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.privacy.normalization import NormalizationError, normalize_shadow, normalize_text
from app.privacy.scanner import Finding, FindingKind, scan_text
from app.redaction.placeholders import (
    MAX_PLACEHOLDER_INDEX,
    PLACEHOLDER_PATTERN,
    RESERVED_PREFIX,
    RedactionKind,
    format_placeholder,
    parse_placeholder,
)

_SUPPORTED_RULES = {
    "email": RedactionKind.EMAIL,
    "phone": RedactionKind.PHONE,
    "iban": RedactionKind.IBAN,
    "ahv": RedactionKind.AHV,
    "internal_address": RedactionKind.IP,
    "blocklist": RedactionKind.TERM,
}


class RedactionError(ValueError):
    """A stable local failure with no text, original value, or token in its message."""

    def __init__(self, code: str = "invalid_redaction") -> None:
        self.code = code
        super().__init__(f"Redaction failed ({code}).")


class RedactionBlockedError(RedactionError):
    """The complete payload must remain local after a privacy check failed."""

    def __init__(self, code: str, *, counts: Mapping[str, int] | None = None) -> None:
        self.counts = dict(counts or {})
        super().__init__(code)


@dataclass(frozen=True)
class RedactionOutcome:
    text: str = field(repr=False)
    mapping: dict[str, str] = field(repr=False)
    kinds: dict[str, int]
    applied: bool

    @property
    def placeholders(self) -> int:
        """Count distinct placeholders used in this payload, including reused ones."""
        return sum(self.kinds.values())


def _validate_mapping(task_id: UUID, mapping: Mapping[str, str]) -> dict[str, str]:
    reverse: dict[str, str] = {}
    for token, original in mapping.items():
        if not isinstance(token, str) or not isinstance(original, str) or not original:
            raise RedactionError("invalid_mapping")
        try:
            parsed = parse_placeholder(token)
            contains_reserved = RESERVED_PREFIX.search(normalize_text(original)) is not None
        except ValueError:
            raise RedactionError("invalid_mapping") from None
        if parsed.task_id != task_id or contains_reserved:
            raise RedactionError("invalid_mapping")
        if original in reverse:
            raise RedactionError("duplicate_mapping")
        reverse[original] = token
    return reverse


def _tokens_in_text(
    text: str, task_id: UUID, mapping: Mapping[str, str]
) -> list[tuple[int, int, str]]:
    """Accept known canonical tokens; reject all foreign or obfuscated reserved forms."""
    try:
        shadow = normalize_shadow(text)
    except NormalizationError:
        raise RedactionError("normalization_failed") from None
    tokens: list[tuple[int, int, str]] = []
    for prefix in RESERVED_PREFIX.finditer(shadow.text):
        match = PLACEHOLDER_PATTERN.match(shadow.text, prefix.start())
        if match is None:
            raise RedactionError("invalid_placeholder")
        token = match.group()
        try:
            parsed = parse_placeholder(token)
        except ValueError:
            raise RedactionError("invalid_placeholder") from None
        start, end = shadow.original_span(match.start(), match.end())
        if text[start:end] != token:
            raise RedactionError("noncanonical_placeholder")
        if parsed.task_id != task_id or token not in mapping:
            raise RedactionError("unknown_placeholder")
        tokens.append((start, end, token))
    return tokens


def _finding_kind(finding: Finding) -> RedactionKind | None:
    if finding.kind == FindingKind.SECRET:
        return None
    return _SUPPORTED_RULES.get(finding.rule)


def _scan(text: str, blocklist: Sequence[str]) -> list[Finding]:
    try:
        return scan_text(text, source="payload", blocklist=blocklist)
    except NormalizationError:
        raise RedactionBlockedError("normalization_failed") from None


def redact(
    text: str,
    *,
    task_id: UUID,
    existing: Mapping[str, str] | None = None,
    blocklist: Sequence[str] = (),
) -> RedactionOutcome:
    """Replace supported findings, then rescan the complete outgoing text.

    Existing mappings are copied and never mutated. Values retain their exact
    original spelling; only the scanner uses a normalized shadow. Known tokens
    are idempotent, while unknown, foreign, or malformed reserved forms fail closed.
    """
    mapping = dict(existing or {})
    reverse = _validate_mapping(task_id, mapping)
    known = _tokens_in_text(text, task_id, mapping)
    findings = _scan(text, blocklist)
    blocked = [finding for finding in findings if _finding_kind(finding) is None]
    if blocked:
        raise RedactionBlockedError(
            "unsupported_findings", counts=Counter(finding.kind.value for finding in blocked)
        )

    next_index = {kind: 1 for kind in RedactionKind}
    for existing_token in mapping:
        parsed = parse_placeholder(existing_token)
        next_index[parsed.kind] = max(next_index[parsed.kind], parsed.index + 1)

    replacements: list[tuple[int, int, str]] = []
    # Scanner findings are nonoverlapping. Retain that boundary if a future
    # scanner adapter returns overlaps, instead of corrupting original spans.
    previous_end = 0
    for finding in sorted(findings, key=lambda item: (item.start, -item.end)):
        if not 0 <= finding.start < finding.end <= len(text) or finding.start < previous_end:
            raise RedactionBlockedError("invalid_finding_span")
        if any(finding.start < end and start < finding.end for start, end, _ in known):
            raise RedactionBlockedError("placeholder_finding_overlap")
        previous_end = finding.end
        kind = _finding_kind(finding)
        assert kind is not None
        original = text[finding.start : finding.end]
        # First classification wins: later blocklist edits must not renumber
        # an exact original previously detected through a different rule.
        token = reverse.get(original)
        if token is None:
            index = next_index[kind]
            if index > MAX_PLACEHOLDER_INDEX:
                raise RedactionBlockedError("placeholder_limit")
            token = format_placeholder(task_id, kind, index)
            next_index[kind] = index + 1
            mapping[token] = original
            reverse[original] = token
        replacements.append((finding.start, finding.end, token))

    chunks: list[str] = []
    position = 0
    for start, end, token in replacements:
        chunks.extend((text[position:start], token))
        position = end
    chunks.append(text[position:])
    transformed = "".join(chunks)
    residual = _scan(transformed, blocklist)
    if residual:
        raise RedactionBlockedError(
            "residual_findings", counts=Counter(finding.kind.value for finding in residual)
        )
    used = {token for _, _, token in _tokens_in_text(transformed, task_id, mapping)}
    kinds = Counter(parse_placeholder(token).kind.value for token in used)
    return RedactionOutcome(transformed, mapping, dict(kinds), bool(used))


def rehydrate(text: str, *, task_id: UUID, mapping: Mapping[str, str]) -> str:
    """Restore only this task's known tokens in a single, nonrecursive pass."""
    _validate_mapping(task_id, mapping)
    tokens = _tokens_in_text(text, task_id, mapping)
    chunks: list[str] = []
    position = 0
    for start, end, token in tokens:
        chunks.extend((text[position:start], mapping[token]))
        position = end
    chunks.append(text[position:])
    return "".join(chunks)
