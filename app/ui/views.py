"""Escaped view data and centrally registered Jinja templates."""

from __future__ import annotations

from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse, Response

from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE, token_digest

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
TEMPLATES.env.globals["result_labels"] = {
    "succeeded": ("✓", "Erfolgreich"),
    "failed": ("!", "Fehlgeschlagen"),
    "blocked": ("⛔", "Blockiert"),
    "cancelled": ("×", "Abgebrochen"),
}

# Single-use PRG handoffs keep chat and draft content out of URLs, cookies and the DB.
_PREVIEW_TTL = 300
_PREVIEW_LIMIT = 32
_previews: dict[str, tuple[str, str, float, dict[str, Any]]] = {}
_preview_lock = Lock()


def _prune_previews() -> None:
    now = monotonic()
    for key, (_, _, expires, _) in list(_previews.items()):
        if expires <= now:
            del _previews[key]


def preview_redirect(request: Request, path: str, **context: Any) -> Response:
    """Hold a bounded, short-lived preview in this process until its redirected GET."""
    token = token_urlsafe(32)
    session_hash = token_digest(request.cookies[SESSION_COOKIE])
    assert session_hash is not None
    with _preview_lock:
        _prune_previews()
        while len(_previews) >= _PREVIEW_LIMIT:
            del _previews[next(iter(_previews))]
        _previews[token] = (path, session_hash, monotonic() + _PREVIEW_TTL, context)
    return RedirectResponse(f"{path}?preview={token}", status_code=303)


def preview_context(request: Request) -> dict[str, Any]:
    """Only the originating owner session and destination can consume a preview."""
    token = request.query_params.get("preview")
    if token is None:
        return {}
    session_hash = token_digest(request.cookies[SESSION_COOKIE])
    if session_hash is None:
        return {"error": "preview_unavailable"}
    with _preview_lock:
        _prune_previews()
        preview = _previews.get(token)
        if preview is not None:
            path, bound_session, _, context = preview
            if path == request.url.path and bound_session == session_hash:
                del _previews[token]
                return context
    return {"error": "preview_unavailable"}


def page(request: Request, name: str, *, status: int = 200, **context: Any) -> Response:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context={
            "csrf": request.cookies.get(CSRF_COOKIE, ""),
            "owner": True,
            "controls": getattr(request.state, "controls", None),
            "lane_health": getattr(request.state, "lane_health", []),
            "health_unavailable": getattr(request.state, "health_unavailable", False),
            **context,
        },
        status_code=status,
    )
