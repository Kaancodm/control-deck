"""Explicit owner form values become a validated frozen task envelope."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.schemas import TaskV1
from app.core.validate import validate_task
from app.kb.snapshots import resolve_submission_contexts
from app.privacy.gate import validate_submission
from app.ui.files import Upload


class FormError(ValueError):
    def __init__(self, code: str = "invalid_task") -> None:
        self.code = code
        super().__init__("Please check the task fields.")


def _workers(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _context_refs(value: str) -> list[dict[str, str]]:
    if len(value) > 1024:
        raise FormError("invalid_context_references")
    references = value.split()
    if len(references) > 16 or len(set(references)) != len(references):
        raise FormError("invalid_context_references")
    for reference in references:
        if str(UUID(reference)) != reference:
            raise FormError("invalid_context_references")
    return [{"source": "knowledge_db", "ref": reference} for reference in references]


def task_from_form(values: Mapping[str, str], uploads: Sequence[Upload] = ()) -> TaskV1:
    """Validate text and upload bytes before the upload writer receives any content."""
    try:
        acceptance = json.loads(values.get("acceptance", "[]") or "[]")
        data_class = values.get("data_class", "")
        if data_class != "local_only" and values.get("cloud_confirm") != "yes":
            raise FormError("cloud_confirmation_required")
        inputs = []
        if text := values.get("input_text", ""):
            inputs.append({"name": "text", "type": "text", "value": text})
        inputs.extend(
            {"name": item.filename, "type": "text", "value": item.text()} for item in uploads
        )
        payload: dict[str, Any] = {
            "schema_version": "task.v1",
            "task_id": str(uuid4()),
            "parent_task_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "requested_by": "ui",
            "kind": values.get("kind", "custom"),
            "title": values.get("title", ""),
            "instructions": values.get("instructions", ""),
            "inputs": inputs,
            "context_refs": _context_refs(values.get("context_refs", "")),
            "data_class": data_class,
            "policy": {
                "allowed_workers": _workers(values.get("allowed_workers", "")) or None,
                "forbidden_workers": _workers(values.get("forbidden_workers", "")),
                "max_attempts": int(values.get("max_attempts", "3")),
                "timeout_s": int(values.get("timeout_s", "900")),
                "require_review": values.get("require_review") == "yes",
            },
            "acceptance": acceptance,
            "fallback_chain": _workers(values.get("fallback_chain", "")),
            "labels": {},
        }
        if validate_task(payload):
            raise FormError()
        task = TaskV1.model_validate(payload)
        validate_submission(task, contexts=resolve_submission_contexts(task))
        return task
    except (ValueError, TypeError, UnicodeError) as exc:
        if isinstance(exc, FormError):
            raise
        raise FormError() from None


def validate_draft(raw: str) -> TaskV1:
    """A chat draft is detached, local, and cannot choose files or external context."""
    if len(raw.encode("utf-8")) > 65536:
        raise FormError("draft_too_large")
    try:
        payload = json.loads(raw)
        if validate_task(payload):
            raise FormError("invalid_draft")
        task = TaskV1.model_validate(payload)
        if task.data_class.value != "local_only" or task.context_refs:
            raise FormError("draft_context_forbidden")
        if any(item.type.value != "text" for item in task.inputs):
            raise FormError("draft_files_forbidden")
        validate_submission(task)
        return task
    except (ValueError, TypeError, UnicodeError):
        raise FormError("invalid_draft") from None


def form_from_draft(task: TaskV1) -> dict[str, str]:
    """Only explicit draft fields populate a new form; identity is generated on submit."""
    return {
        "title": task.title,
        "instructions": task.instructions,
        "kind": task.kind.value,
        "data_class": task.data_class.value,
        "input_text": "\n\n".join(item.value or "" for item in task.inputs),
        "fallback_chain": ",".join(worker.value for worker in task.fallback_chain),
        "allowed_workers": ",".join(worker.value for worker in task.policy.allowed_workers or []),
        "forbidden_workers": ",".join(worker.value for worker in task.policy.forbidden_workers),
        "max_attempts": str(task.policy.max_attempts),
        "timeout_s": str(task.policy.timeout_s),
        "require_review": "yes" if task.policy.require_review else "no",
        "acceptance": json.dumps([rule.model_dump(mode="json") for rule in task.acceptance]),
    }
