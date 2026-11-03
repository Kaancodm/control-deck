"""Audit projections discard raw database detail and export bounded safe cells."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.audit.presentation import AuditFilter, audit_csv, csv_cell, project_record


def record(**changes):
    return {
        "source": "task",
        "id": 1,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "event_type": "task.retry",
        "task_id": uuid4(),
        "actor": "runner",
        "attempt": 2,
        "from_status": "running",
        "to_status": "queued",
        "detail": {},
        **changes,
    }


def test_only_known_metadata_survives_projection():
    item = project_record(
        record(
            detail={
                "worker": "claude",
                "reason": "acceptance_failed",
                "code": "acceptance_failed",
                "payload_sha256": "a" * 64,
                "files": 2,
                "bytes": 128,
                "output": "private-output-marker",
                "password": "private-password-marker",
                "file_path": "private/path",
                "input": {"text": "private-input-marker"},
            }
        )
    )
    assert item.lane == "claude"
    assert item.reason == item.error_code == "acceptance_failed"
    assert item.payload_sha256 == "a" * 64
    assert item.counts == {"files": 2, "bytes": 128}
    assert "private" not in item.model_dump_json()


@pytest.mark.parametrize("raw", ["private-marker", "a" * 64, "=1+2", ["ollama"], {}, 1])
def test_unknown_reason_actor_type_lane_and_digest_are_not_rendered(raw):
    item = project_record(
        record(
            event_type=raw,
            actor=raw,
            phase=raw,
            detail={"reason": raw, "code": raw, "worker": raw, "payload_sha256": raw},
        )
    )
    assert item.event_type == "unknown"
    assert item.actor is item.reason is item.error_code is item.lane is item.phase is None
    assert item.payload_sha256 == (raw if raw == "a" * 64 else None)


@pytest.mark.parametrize("detail", [None, "private-marker", [], 42])
def test_non_mapping_detail_is_ignored(detail):
    item = project_record(record(detail=detail))
    assert item.reason is item.lane is None
    assert item.counts == {}


@pytest.mark.parametrize("value", [-1, True, "3", 2**63])
def test_invalid_counts_and_attempts_are_omitted(value):
    item = project_record(record(attempt=value, detail={"files": value}))
    assert item.attempt is None
    assert item.counts == {}


def test_operations_are_projected_without_host_details():
    operation_id = uuid4()
    item = project_record(
        record(
            source="operation",
            task_id=None,
            operation_id=operation_id,
            event_type="operation.backup",
            actor="system",
            phase="completed",
            from_status=None,
            to_status=None,
            attempt=None,
            detail={"rows": 11, "path": "private/path", "reason": "raw-failure-message"},
        )
    )
    assert item.operation_id == operation_id and item.task_id is None
    assert item.phase == "completed" and item.counts == {"rows": 11}
    assert item.reason is None
    assert "private" not in item.model_dump_json()


@pytest.mark.parametrize(
    "value", ["=1+2", "+SUM(1)", "-1", "@sum(1)", "  =1", "\ufeff=1", "\ttext", "\rtext", "\ntext"]
)
def test_formula_cells_are_neutralized(value):
    assert csv_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["safe", "42", "", "safe,quoted", "first\nsecond"])
def test_plain_cells_preserve_content(value):
    assert csv_cell(value) == value


def test_csv_exports_visible_metadata_and_no_detail_blob():
    item = project_record(record(detail={"worker": "ollama", "output": "private-marker"}))
    data = audit_csv([item])
    rows = list(csv.DictReader(io.StringIO(data)))
    assert len(rows) == 1 and rows[0]["lane"] == "ollama"
    assert rows[0]["reason"] == "" and rows[0]["task_id"] == str(item.task_id)
    assert "detail" not in rows[0] and "private-marker" not in data


def test_csv_refuses_unbounded_export():
    item = project_record(record())
    with pytest.raises(ValueError, match="one page"):
        audit_csv([item] * 201)


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"offset": 100001},
        {"event_type": "private-marker"},
        {"actor": "private-marker"},
        {"lane": "other"},
        {"source": "other"},
        {"since": "2026-01-01T00:00:00"},
    ],
)
def test_filter_rejects_unknown_or_unbounded_selection(changes):
    with pytest.raises(ValidationError):
        AuditFilter(**changes)


def test_filter_interval_and_source_types():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert AuditFilter(since=now, until=now + timedelta(seconds=1)).limit == 100
    assert AuditFilter(source="operation", event_type="operation.restore").source == "operation"
    with pytest.raises(ValidationError):
        AuditFilter(since=now, until=now)


def test_unknown_record_source_is_rejected_without_echo():
    with pytest.raises(ValueError, match="^Unknown audit record source$"):
        project_record(record(source="private-marker"))
