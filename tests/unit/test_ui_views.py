"""Rendered task cards and session-bound, transient PRG handoffs."""

from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.schemas import DataClass, Worker
from app.db.state_machine import TaskState
from app.ui import views
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE


def request(path="/ui/chat", *, session="s" * 43):
    url = urlsplit(path)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": url.path,
            "query_string": url.query.encode(),
            "headers": [
                (b"cookie", f"{SESSION_COOKIE}={session}; {CSRF_COOKIE}=synthetic-csrf".encode())
            ],
            "server": ("testserver", 443),
        }
    )


@pytest.fixture
def previews(monkeypatch):
    # Isolate the process cache and clock without touching any real sessions.
    monkeypatch.setattr(views, "_previews", {})
    monkeypatch.setattr(views, "monotonic", lambda: 1000.0)


def test_preview_redirect_uses_303_with_no_content_in_url_or_cookies(previews):
    response = views.preview_redirect(request(), "/ui/chat", answer="Synthetic private answer")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/chat?preview=")
    assert "Synthetic" not in response.headers["location"]
    assert "set-cookie" not in response.headers
    destination = request(response.headers["location"])
    assert views.preview_context(destination) == {"answer": "Synthetic private answer"}
    assert views.preview_context(destination) == {"error": "preview_unavailable"}


def test_preview_is_bound_to_session_and_destination(previews):
    response = views.preview_redirect(request(), "/ui/tasks/new", values={"title": "Synthetic"})
    destination = response.headers["location"]
    assert views.preview_context(request(destination, session="x" * 43)) == {
        "error": "preview_unavailable"
    }
    assert views.preview_context(request(destination.replace("/tasks/new", "/chat"))) == {
        "error": "preview_unavailable"
    }
    assert views.preview_context(request(destination)) == {"values": {"title": "Synthetic"}}


def test_preview_expires_and_capacity_is_bounded(previews, monkeypatch):
    monkeypatch.setattr(views, "_PREVIEW_LIMIT", 2)
    first = views.preview_redirect(request(), "/ui/chat", answer="First")
    second = views.preview_redirect(request(), "/ui/chat", answer="Second")
    third = views.preview_redirect(request(), "/ui/chat", answer="Third")
    assert len(views._previews) == 2
    assert views.preview_context(request(first.headers["location"])) == {
        "error": "preview_unavailable"
    }
    monkeypatch.setattr(views, "monotonic", lambda: 1000.0 + views._PREVIEW_TTL)
    for response in (second, third):
        assert views.preview_context(request(response.headers["location"])) == {
            "error": "preview_unavailable"
        }
    assert views._previews == {}


@pytest.mark.parametrize("state", list(TaskState))
def test_task_card_keeps_task_state_result_and_cancel_request_separate(state):
    task = SimpleNamespace(
        id=uuid4(),
        status=state,
        cancel_requested=state == TaskState.RUNNING,
        payload={"title": "<script>Synthetic title</script>"},
        data_class=DataClass.LOCAL_ONLY,
        lane="ollama",
        attempt=3,
        deadline_at=None,
        next_attempt_at=None,
        results=[
            SimpleNamespace(attempt=2, payload={"status": "succeeded"}),
            SimpleNamespace(attempt=1, payload={"status": "failed"}),
        ],
    )
    markup = views.TEMPLATES.get_template("partials/task_card.html").render(
        task=task, needs_owner=False
    )
    assert f"status-{state.value}" in markup
    assert "result-succeeded" in markup and "result-failed" not in markup
    assert "Ergebnis · Versuch 2" in markup
    assert ("Abbruch angefordert" in markup) == (state == TaskState.RUNNING)
    assert "&lt;script&gt;" in markup and "<script>" not in markup


def test_empty_result_is_explicit():
    markup = views.TEMPLATES.get_template("partials/result_status.html").render(result=None)
    assert "Ergebnis: Noch keines" in markup


class Forms(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form":
            self.current = {"method": values.get("method", "get"), "fields": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            self.current["fields"][values.get("name")] = values

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


@pytest.mark.parametrize(
    "template,state",
    [
        ("login.html", TaskState.AWAITING_APPROVAL),
        ("task_form.html", TaskState.AWAITING_APPROVAL),
        ("task_detail.html", TaskState.AWAITING_APPROVAL),
        ("task_detail.html", TaskState.REVIEW),
        ("task_detail.html", TaskState.RUNNING),
        ("chat.html", TaskState.DONE),
        ("lanes.html", TaskState.DONE),
    ],
)
def test_all_rendered_post_forms_include_csrf(template, state):
    task = SimpleNamespace(
        id=uuid4(),
        status=state,
        cancel_requested=False,
        payload={"title": "Synthetic"},
        data_class=DataClass.LOCAL_ONLY,
        lane="ollama",
        attempt=0,
        results=[],
        events=[],
    )
    controls = SimpleNamespace(
        max_parallel=1,
        paused=False,
        stop_requested=False,
        lane_paused={worker: False for worker in Worker},
    )
    markup = views.TEMPLATES.get_template(template).render(
        request=request(),
        csrf="synthetic-csrf",
        owner=template != "login.html",
        task=task,
        values={},
        outputs=[],
        controls=controls,
        observations={},
        workers=Worker,
        reply=SimpleNamespace(answer="Synthetic reply", simulation=True),
        draft_json="{}",
    )
    posts = [form for form in Forms(markup).forms if form["method"] == "post"]
    assert posts
    for form in posts:
        assert form["fields"]["csrf"]["type"] == "hidden"
        assert form["fields"]["csrf"]["value"] == "synthetic-csrf"
