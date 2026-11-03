"""Classify task contents before storage, approval, and queue dispatch."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from app.core.schemas import DataClass, InputType, TaskV1
from app.privacy.config import PrivacyConfigurationError, PrivacySettings, get_privacy_settings
from app.privacy.files import FileInputError, read_text_input
from app.privacy.normalization import NormalizationError
from app.privacy.scanner import Finding, FindingKind, scan_text

_MAX_TASK_BYTES = 5 * 1024 * 1024
_MAX_INPUTS = 64
_MAX_FILES = 16
_MAX_TEXT_PARTS = 1024
_MAX_FINDINGS = 128


class PrivacyInputError(ValueError):
    """Input rejected without echoing its contents, name, or filesystem path."""

    def __init__(self, code: str, *, source: str = "task") -> None:
        self.code = code
        self.source = source
        super().__init__("Task failed privacy input validation.")


class PrivacyInputUnavailable(PrivacyInputError):
    """A stored attachment is temporarily unavailable; preserve the task state."""


@dataclass(frozen=True)
class FileHash:
    """Digest of the exact inspected UTF-8 bytes, without a filename or contents."""

    input_index: int
    sha256: str


@dataclass(frozen=True)
class GateReport:
    findings: tuple[Finding, ...]
    blocked: bool
    file_hashes: tuple[FileHash, ...] = ()

    @property
    def has_secrets(self) -> bool:
        return any(finding.kind == FindingKind.SECRET for finding in self.findings)

    def safe_detail(self) -> dict[str, Any]:
        return {
            "reason": "privacy_gate",
            "findings": [
                {
                    "kind": finding.kind.value,
                    "rule": finding.rule,
                    "source": finding.source,
                    "start": finding.start,
                    "end": finding.end,
                }
                for finding in self.findings
            ],
        }


@dataclass(frozen=True)
class StoredTaskInspection:
    blocking_detail: dict[str, Any] | None = None
    file_hashes: tuple[FileHash, ...] = ()


class PrivacyViolation(PrivacyInputError):
    """A secret was found before persistence; only safe finding metadata is retained."""

    def __init__(self, report: GateReport) -> None:
        super().__init__("secret_detected")
        self.report = report


def _task_text_parts(task: TaskV1) -> Iterator[tuple[str, str]]:
    yield "title", task.title
    yield "instructions", task.instructions
    for index, item in enumerate(task.inputs):
        for field in ("name", "value", "path", "mime"):
            value = getattr(item, field)
            if value is not None:
                yield f"inputs[{index}].{field}", value
        if item.value is not None:
            # A credential can be split between a field name and its value.
            yield f"inputs[{index}].assignment", f"{item.name}={item.value}"
    for index, rule in enumerate(task.acceptance):
        if rule.value is not None:
            yield f"acceptance[{index}].value", rule.value
    for index, (key, value) in enumerate(task.labels.items()):
        yield f"labels[{index}].key", key
        yield f"labels[{index}].value", value
        yield f"labels[{index}].assignment", f"{key}={value}"
    for index, reference in enumerate(task.context_refs):
        yield f"context_refs[{index}].ref", reference.ref


def inspect_task(
    task: TaskV1,
    *,
    settings: PrivacySettings | None = None,
    file_texts: dict[int, str] | None = None,
) -> GateReport:
    """Scan all supplied text; raw findings never leave this local boundary.

    Personal data is permitted locally. Cloud classes remain blocked on findings
    until the later redaction stage provides an explicit transformation path.
    This does not resolve routing, worker identity, or knowledge-DB contents.
    """
    settings = settings or get_privacy_settings()
    if file_texts is not None:
        # A host-side payload builder may retain the exact inspected reads.
        # The collector never appears in reports, audit details, or API responses.
        file_texts.clear()
    if task.data_class != DataClass.LOCAL_ONLY and not settings.blocklist_configured:
        raise PrivacyConfigurationError("blocklist_required")
    if len(task.inputs) > _MAX_INPUTS:
        raise PrivacyInputError("too_many_inputs")
    file_count = sum(item.type == InputType.FILE for item in task.inputs)
    if file_count > _MAX_FILES:
        raise PrivacyInputError("too_many_files")

    findings: list[Finding] = []
    file_hashes: list[FileHash] = []
    total_bytes = 0

    def scan_part(source: str, content: str, *, count_bytes: bool = True) -> None:
        nonlocal total_bytes
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError:
            raise PrivacyInputError("invalid_utf8", source=source) from None
        if count_bytes:
            total_bytes += size
        if total_bytes > _MAX_TASK_BYTES:
            raise PrivacyInputError("task_too_large")
        try:
            findings.extend(scan_text(content, source=source, blocklist=settings.blocklist))
        except NormalizationError:
            raise PrivacyInputError("unicode_too_complex", source=source) from None
        # Preserve secret findings even when earlier fields filled the metadata cap.
        findings.sort(key=lambda item: (item.kind != FindingKind.SECRET, item.source, item.start))
        del findings[_MAX_FINDINGS:]

    for count, (source, content) in enumerate(_task_text_parts(task), start=1):
        if count > _MAX_TEXT_PARTS:
            raise PrivacyInputError("too_many_text_fields")
        # Combined credential context reuses bytes already counted as fields.
        scan_part(source, content, count_bytes=not source.endswith(".assignment"))
    for index, item in enumerate(task.inputs):
        if item.type == InputType.FILE:
            assert item.path is not None
            source = f"inputs[{index}].content"
            try:
                content = read_text_input(settings.inbox, item.path, mime=item.mime)
            except FileInputError as exc:
                raise PrivacyInputError(exc.code, source=source) from None
            scan_part(source, content)
            if file_texts is not None:
                file_texts[index] = content
            # The strict UTF-8 reader preserves BOM and line endings. Re-encoding
            # therefore hashes the same bytes that were read and scanned.
            file_hashes.append(FileHash(index, sha256(content.encode("utf-8")).hexdigest()))
    if task.context_refs:
        raise PrivacyInputError("context_unavailable", source="context_refs")
    has_secrets = any(item.kind == FindingKind.SECRET for item in findings)
    return GateReport(
        findings=tuple(findings),
        blocked=has_secrets or (task.data_class != DataClass.LOCAL_ONLY and bool(findings)),
        file_hashes=tuple(file_hashes),
    )


def validate_submission(task: TaskV1) -> GateReport:
    """Reject secrets before any task or event is inserted."""
    report = inspect_task(task)
    if report.has_secrets:
        raise PrivacyViolation(report)
    return report


def inspect_stored_task(payload: dict[str, Any]) -> StoredTaskInspection:
    """Recheck inputs, distinguishing terminal rejection from unavailable files."""
    try:
        task = TaskV1.model_validate(payload)
        report = inspect_task(task)
        return StoredTaskInspection(
            blocking_detail=report.safe_detail() if report.blocked else None,
            file_hashes=report.file_hashes,
        )
    except ValidationError:
        return StoredTaskInspection(
            blocking_detail={"reason": "privacy_gate", "code": "invalid_task", "source": "task"}
        )
    except PrivacyInputError as exc:
        if exc.code == "file_unavailable":
            raise PrivacyInputUnavailable(exc.code, source=exc.source) from None
        return StoredTaskInspection(
            blocking_detail={"reason": "privacy_gate", "code": exc.code, "source": exc.source}
        )
