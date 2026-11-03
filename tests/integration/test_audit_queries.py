"""Read-only SQL audit paging across independently owned append-only logs."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.audit.presentation import AuditFilter, audit_csv
from app.audit.repository import list_audit
from app.db.models import Event
from app.db.state_machine import TaskState
from app.db.ui_auth import AuthEvent
from app.operations.models import OperationEvent
from tests.integration.test_database import _insert_task

STAMP = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def records(db_session: Session):
    task = _insert_task(db_session, approved=False)
    other = _insert_task(db_session, approved=False)
    for index, (task_id, lane, actor) in enumerate(
        [
            (task.id, "ollama", "runner"),
            (other.id, "claude", "operator"),
            (task.id, "codex", "runner"),
        ]
    ):
        db_session.add(
            Event(
                task_id=task_id,
                event_type="task.retry",
                actor=actor,
                attempt=index + 1,
                from_status=TaskState.RUNNING,
                to_status=TaskState.QUEUED,
                created_at=STAMP + timedelta(seconds=index),
                detail={
                    "worker": lane,
                    "reason": "private-reason-marker",
                    "output": "private-output-marker",
                },
            )
        )
    db_session.flush()
    operation_id = uuid4()
    db_session.add_all(
        [
            OperationEvent(
                operation_id=operation_id,
                operation_type="retention",
                phase="completed",
                task_id=task.id,
                created_at=STAMP + timedelta(seconds=2),
                detail={"maps": 3, "path": "private-path-marker"},
            ),
            OperationEvent(
                operation_id=operation_id,
                operation_type="backup",
                phase="failed",
                created_at=STAMP,
                detail={"reason": "private-error-marker"},
            ),
        ]
    )
    db_session.flush()
    return task.id, other.id, operation_id


def selection(**changes):
    return AuditFilter(since=STAMP, until=STAMP + timedelta(seconds=3), **changes)


def test_stable_combined_order_and_pagination(db_session: Session, records):
    first = list_audit(db_session, selection(limit=2))
    second = list_audit(db_session, selection(limit=2, offset=2))
    third = list_audit(db_session, selection(limit=2, offset=4))
    assert first.total == second.total == third.total == 5
    rows = first.items + second.items + third.items
    assert len({(row.source, row.id) for row in rows}) == 5
    assert [(row.source, row.event_type) for row in first.items] == [
        ("operation", "operation.retention"),
        ("task", "task.retry"),
    ]
    assert [row.created_at for row in rows] == sorted(
        [row.created_at for row in rows], reverse=True
    )


@pytest.mark.parametrize(
    "filters,count",
    [
        ({"source": "task"}, 3),
        ({"source": "operation"}, 2),
        ({"actor": "runner"}, 2),
        ({"actor": "system"}, 2),
        ({"event_type": "operation.backup"}, 1),
        ({"lane": "claude"}, 1),
    ],
)
def test_sql_filters_select_only_requested_metadata(db_session: Session, records, filters, count):
    page = list_audit(db_session, selection(**filters))
    assert page.total == len(page.items) == count


def test_task_filter_includes_correlated_operation_without_other_task(db_session: Session, records):
    page = list_audit(db_session, selection(task_id=records[0]))
    assert page.total == 3
    assert all(item.task_id == records[0] for item in page.items)


def test_upper_time_bound_is_exclusive(db_session: Session, records):
    page = list_audit(db_session, AuditFilter(since=STAMP, until=STAMP + timedelta(seconds=1)))
    assert page.total == 2
    assert all(item.created_at == STAMP for item in page.items)


def test_raw_detail_and_task_content_are_never_exposed(db_session: Session, records):
    page = list_audit(db_session, selection())
    assert "private-" not in page.model_dump_json()
    assert "private-" not in audit_csv(page.items)
    operation = next(item for item in page.items if item.event_type == "operation.retention")
    assert operation.counts == {"maps": 3}


def test_queries_have_no_writes_or_row_locks(db_session: Session, records):
    statements = []
    connection = db_session.connection()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.upper())

    event.listen(connection, "before_cursor_execute", capture)
    try:
        page = list_audit(db_session, selection(offset=100))
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert page.total == 5 and page.items == []
    assert len(statements) == 1
    assert all(
        statement.startswith("WITH SELECTED_AUDIT AS") and "FOR UPDATE" not in statement
        for statement in statements
    )


def test_authentication_audit_is_taskless_and_bounded(db_session: Session, records):
    item = AuthEvent(event_type="auth.sessions_revoked", affected_sessions=2, created_at=STAMP)
    db_session.add(item)
    db_session.flush()
    page = list_audit(db_session, selection(source="auth"))
    assert page.total == 1
    projected = page.items[0]
    assert projected.id == item.id
    assert projected.task_id is projected.operation_id is projected.actor is None
    assert projected.event_type == "auth.sessions_revoked"
    assert projected.counts == {"sessions": 2}
    assert list_audit(db_session, selection(event_type="auth.sessions_revoked")).total == 1
