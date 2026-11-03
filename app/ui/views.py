"""Escaped view data and centrally registered Jinja templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

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
