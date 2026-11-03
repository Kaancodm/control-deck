"""Build and recheck the exact outgoing text without exposing local mappings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256

from sqlalchemy.orm import Session

from app.core.schemas import DataClass, InputType, TaskV1, Worker
from app.db.models import Task
from app.privacy.config import PrivacySettings, get_privacy_settings
from app.privacy.gate import FileHash, PrivacyInputError, inspect_task
from app.privacy.scanner import FindingKind, scan_text
from app.redaction.engine import redact
from app.redaction.repository import redact_for_task


class LaneNotAllowedError(ValueError):
    """The requested worker is not permitted for this task."""


@dataclass(frozen=True)
class PayloadBuild:
    """Only transport bytes and non-sensitive metadata; never a redaction map."""

    text: str = field(repr=False)
    sha256: str
    applied: bool
    placeholders: int
    file_hashes: tuple[FileHash, ...] = field(default=(), repr=False)


def render_payload(
    task: TaskV1,
    *,
    settings: PrivacySettings,
    file_hashes: list[FileHash] | None = None,
) -> str:
    """Read each attachment once, then render every supplied text field."""
    files: dict[int, str] = {}
    report = inspect_task(task, settings=settings, file_texts=files)
    if file_hashes is not None:
        file_hashes.clear()
        file_hashes.extend(report.file_hashes)
    if report.has_secrets:
        raise PrivacyInputError("secret_detected")
    if task.data_class == DataClass.CLOUD_OK and report.blocked:
        raise PrivacyInputError("privacy_gate")

    parts = [f"# Task {task.task_id}", "## Title", task.title, "## Instructions", task.instructions]
    for index, item in enumerate(task.inputs):
        parts.extend([f"## Input {index}", "Name: " + item.name])
        if item.path is not None:
            parts.append("Path: " + item.path)
        if item.mime is not None:
            parts.append("MIME: " + item.mime)
        if item.value is not None:
            # Preserve field-name context for credential detection.
            parts.append(item.name + " = " + item.value)
        if item.type == InputType.FILE:
            parts.append(files[index])
    for rule in task.acceptance:
        parts.append("## Acceptance: " + rule.type.value)
        if rule.value is not None:
            parts.append(rule.value)
    if task.labels:
        parts.append("## Labels")
        parts.extend(key + " = " + value for key, value in task.labels.items())
    text = "\n".join(parts) + "\n"
    if len(text.encode("utf-8")) > 5 * 1024 * 1024:
        raise PrivacyInputError("payload_too_large")
    return text


def _check_lane(task: TaskV1, lane: Worker) -> None:
    if not isinstance(lane, Worker):
        raise LaneNotAllowedError("A known worker is required.")
    if task.data_class == DataClass.LOCAL_ONLY and lane != Worker.OLLAMA:
        raise LaneNotAllowedError("Local tasks require the local worker.")
    if lane in task.policy.forbidden_workers or (
        task.policy.allowed_workers is not None and lane not in task.policy.allowed_workers
    ):
        raise LaneNotAllowedError("The task policy does not permit this worker.")


def _result(
    text: str, *, applied: bool, placeholders: int, file_hashes: tuple[FileHash, ...] = ()
) -> PayloadBuild:
    data = text.encode("utf-8")
    if len(data) > 5 * 1024 * 1024:
        raise PrivacyInputError("payload_too_large")
    return PayloadBuild(text, sha256(data).hexdigest(), applied, placeholders, file_hashes)


def build_payload(
    task: TaskV1,
    *,
    lane: Worker,
    settings: PrivacySettings | None = None,
    existing: Mapping[str, str] | None = None,
    mapping_sink: dict[str, str] | None = None,
) -> PayloadBuild:
    """Prepare a payload in memory, including the reference executor's fake path.

    Persistent runs use prepare_task_payload so allocation is serialized by the
    task lock. The optional mapping sink belongs only to the local reference
    harness; transport results never contain the map. No provider is called.
    """
    _check_lane(task, lane)
    settings = settings or get_privacy_settings()
    file_hashes: list[FileHash] = []
    text = render_payload(task, settings=settings, file_hashes=file_hashes)
    if task.data_class == DataClass.CLOUD_REDACTED:
        outcome = redact(
            text, task_id=task.task_id, existing=existing, blocklist=settings.blocklist
        )
        result = _result(
            outcome.text,
            applied=outcome.applied,
            placeholders=outcome.placeholders,
            file_hashes=tuple(file_hashes),
        )
        if mapping_sink is not None:
            mapping_sink.clear()
            mapping_sink.update(outcome.mapping)
        return result
    findings = scan_text(text, source="payload", blocklist=settings.blocklist)
    if any(item.kind == FindingKind.SECRET for item in findings) or (
        findings and task.data_class == DataClass.CLOUD_OK
    ):
        raise PrivacyInputError("privacy_gate")
    return _result(text, applied=False, placeholders=0, file_hashes=tuple(file_hashes))


def prepare_task_payload(
    session: Session,
    task: TaskV1,
    *,
    lane: Worker,
    attempt: int,
    settings: PrivacySettings | None = None,
    expected_file_hashes: tuple[FileHash, ...] | None = None,
) -> PayloadBuild:
    """Build a persisted task's payload; the caller controls commit and delivery."""
    _check_lane(task, lane)
    stored = session.get(Task, task.task_id)
    if stored is None or TaskV1.model_validate(stored.payload) != task:
        raise PrivacyInputError("task_payload_mismatch")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise PrivacyInputError("invalid_attempt")
    settings = settings or get_privacy_settings()
    if task.data_class != DataClass.CLOUD_REDACTED:
        result = build_payload(task, lane=lane, settings=settings)
        if expected_file_hashes is not None and result.file_hashes != expected_file_hashes:
            raise PrivacyInputError("approval_hash_mismatch")
        return result
    file_hashes: list[FileHash] = []
    text = render_payload(task, settings=settings, file_hashes=file_hashes)
    if expected_file_hashes is not None and tuple(file_hashes) != expected_file_hashes:
        raise PrivacyInputError("approval_hash_mismatch")
    with session.begin_nested():
        outcome = redact_for_task(
            session,
            task_id=task.task_id,
            text=text,
            attempt=attempt,
            blocklist=settings.blocklist,
        )
        return _result(
            outcome.text,
            applied=outcome.applied,
            placeholders=outcome.placeholders,
            file_hashes=tuple(file_hashes),
        )
