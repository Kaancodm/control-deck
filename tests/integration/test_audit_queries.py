"""Read-only SQL audit paging across independently owned append-only logs."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.audit.presentation import AuditFilter, audit_csv
from app.audit.repository import list_audit
from app.db.models import Event
from app.db.state_machine import TaskState
from tests.integration.test_database import _insert_task

STAMP = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def records(db_session: Session):
    task = _insert_task(db_session, approved=False)
    other = _insert_task(db_session, approved=False)
    # A transaction-local projection fixture keeps these read tests independent
    # from operations write validators and does not modify their durable schema.
    db_session.execute(
        text("""
        CREATE TEMP TABLE operations_events (
            id bigint PRIMARY KEY, operation_id uuid NOT NULL,
            operation_type text NOT NULL, phase text NOT NULL,
            task_id uuid, detail jsonb, created_at timestamptz NOT NULL
        ) ON COMMIT DROP
    """)
    )
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
    db_session.execute(
        text("""
        INSERT INTO operations_events VALUES
        (1, :operation, 'retention', 'completed', :task,
         '{"maps": 3, "path": "private-path-marker"}', :stamp),
        (2, :operation, 'backup', 'failed', NULL,
         '{"reason": "private-error-marker"}', :older)
    """),
        {
            "operation": operation_id,
            "task": task.id,
            "stamp": STAMP + timedelta(seconds=2),
            "older": STAMP,
        },
    )
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
