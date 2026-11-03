"""Escaped view data and centrally registered Jinja templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.core.schemas import DataClass
from app.db.models import Task
from app.redaction.placeholders import PLACEHOLDER_PATTERN
from app.redaction.repository import rehydrate_for_task
from app.ui.auth import CSRF_COOKIE

TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
STATUS_LABELS = {
    "awaiting_approval": ("◷", "Start-Freigabe"),
    "queued": ("○", "Wartend"),
    "leased": ("↗", "Zugewiesen"),
    "running": ("▶", "Laeuft"),
    "review": ("◇", "Review"),
    "done": ("✓", "Erledigt"),
    "failed": ("!", "Fehlgeschlagen"),
    "blocked": ("⛔", "Blockiert"),
    "cancelled": ("×", "Abgebrochen"),
    "expired": ("⌛", "Deadline abgelaufen"),
    "succeeded": ("✓", "Historisch erfolgreich"),
}
TEMPLATES.env.globals["status_labels"] = STATUS_LABELS


def page(request: Request, name: str, *, status: int = 200, **context: Any) -> Response:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context={
            "csrf": request.cookies.get(CSRF_COOKIE, ""),
            "owner": True,
            "controls": getattr(request.state, "controls", None),
            **context,
        },
        status_code=status,
    )


@dataclass(frozen=True)
class TextSegment:
    text: str
    restored: bool = False


def output_segments(session: Session, task: Task, text: str) -> list[TextSegment]:
    """Mark restored text without returning the map or marking strings as safe HTML."""
    if task.data_class != DataClass.CLOUD_REDACTED:
        return [TextSegment(text)]
    # Validate the entire output, including malformed/foreign reserved prefixes, first.
    rehydrate_for_task(session, task_id=task.id, text=text)
    segments = []
    start = 0
    for match in PLACEHOLDER_PATTERN.finditer(text):
        segments.append(TextSegment(text[start : match.start()]))
        segments.append(
            TextSegment(
                rehydrate_for_task(session, task_id=task.id, text=match.group()), restored=True
            )
        )
        start = match.end()
    segments.append(TextSegment(text[start:]))
    return segments
