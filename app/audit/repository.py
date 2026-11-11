"""Read-only, consistently paginated projections of task and operations events."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Text,
    cast,
    column,
    func,
    literal,
    select,
    table,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.audit.presentation import AuditFilter, AuditPage, project_record
from app.db.models import Event
from app.db.ui_auth import AuthEvent

# This projection does not register another ORM table or own its migrations.
# Etappe 16 owns the append-only operations table and its write validation.
_OPERATIONS = table(
    "operations_events",
    column("id", BigInteger),
    column("operation_id", UUID(as_uuid=True)),
    column("operation_type", Text),
    column("phase", Text),
    column("task_id", UUID(as_uuid=True)),
    column("detail", JSONB),
    column("created_at", DateTime(timezone=True)),
)


def audit_selection(filters: AuditFilter) -> Select[Any]:
    """Build a parameterized selection; source and identifiers are never SQL text."""
    tasks = select(
        literal("task").label("source"),
        cast(Event.id, Text).label("id"),
        Event.id.label("sort_id"),
        Event.created_at,
        Event.event_type,
        Event.task_id,
        cast(literal(None), UUID(as_uuid=True)).label("operation_id"),
        Event.actor,
        Event.attempt,
        cast(Event.from_status, Text).label("from_status"),
        cast(Event.to_status, Text).label("to_status"),
        cast(literal(None), Text).label("phase"),
        Event.detail,
        func.coalesce(Event.detail["lane"].astext, Event.detail["worker"].astext).label("lane"),
    ).where(
        # Taskless conversation/memory audit rows are not task events.
        Event.task_id.is_not(None)
    )
    operations = select(
        literal("operation").label("source"),
        cast(_OPERATIONS.c.id, Text).label("id"),
        _OPERATIONS.c.id.label("sort_id"),
        _OPERATIONS.c.created_at,
        (literal("operation.") + _OPERATIONS.c.operation_type).label("event_type"),
        _OPERATIONS.c.task_id,
        _OPERATIONS.c.operation_id,
        literal("system").label("actor"),
        cast(literal(None), Integer).label("attempt"),
        cast(literal(None), Text).label("from_status"),
        cast(literal(None), Text).label("to_status"),
        _OPERATIONS.c.phase,
        _OPERATIONS.c.detail,
        cast(literal(None), Text).label("lane"),
    )
    authentication = select(
        literal("auth").label("source"),
        cast(AuthEvent.id, Text).label("id"),
        cast(literal(0), BigInteger).label("sort_id"),
        AuthEvent.created_at,
        AuthEvent.event_type,
        cast(literal(None), UUID(as_uuid=True)).label("task_id"),
        cast(literal(None), UUID(as_uuid=True)).label("operation_id"),
        cast(literal(None), Text).label("actor"),
        cast(literal(None), Integer).label("attempt"),
        cast(literal(None), Text).label("from_status"),
        cast(literal(None), Text).label("to_status"),
        cast(literal(None), Text).label("phase"),
        func.jsonb_build_object("sessions", AuthEvent.affected_sessions).label("detail"),
        cast(literal(None), Text).label("lane"),
    )
    combined = union_all(tasks, operations, authentication).subquery()
    statement = select(combined)
    if filters.source != "all":
        statement = statement.where(combined.c.source == filters.source)
    if filters.event_type is not None:
        statement = statement.where(combined.c.event_type == filters.event_type)
    if filters.actor is not None:
        statement = statement.where(combined.c.actor == filters.actor)
    if filters.lane is not None:
        statement = statement.where(combined.c.lane == filters.lane.value)
    if filters.task_id is not None:
        statement = statement.where(combined.c.task_id == filters.task_id)
    if filters.since is not None:
        statement = statement.where(combined.c.created_at >= filters.since)
    if filters.until is not None:
        statement = statement.where(combined.c.created_at < filters.until)
    return statement


def list_audit(session: Session, filters: AuditFilter) -> AuditPage:
    """Return one stable page and total without locks, writes or task sweeps."""
    selected = audit_selection(filters).cte("selected_audit")
    total_query = select(func.count().label("total")).select_from(selected).subquery()
    page_query = (
        select(selected)
        .order_by(
            selected.c.created_at.desc(),
            selected.c.source,
            selected.c.sort_id.desc(),
            selected.c.id.desc(),
        )
        .limit(filters.limit)
        .offset(filters.offset)
        .subquery()
    )
    with session.no_autoflush:
        # Total and page share one READ COMMITTED snapshot, even beyond the end.
        rows = (
            session.execute(
                select(total_query.c.total, page_query)
                .select_from(total_query.outerjoin(page_query, true()))
                .order_by(
                    page_query.c.created_at.desc(),
                    page_query.c.source,
                    page_query.c.sort_id.desc(),
                    page_query.c.id.desc(),
                )
            )
            .mappings()
            .all()
        )
        total = int(rows[0]["total"])
        items = [
            project_record({str(key): value for key, value in row.items()})
            for row in rows
            if row["id"] is not None
        ]
    return AuditPage(items=items, total=total, limit=filters.limit, offset=filters.offset)
