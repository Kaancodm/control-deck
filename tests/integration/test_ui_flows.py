"""Owner UI flows exercise the real business services and PostgreSQL state."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from uuid import UUID

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.deps import get_db
from app.core.schemas import Worker
from app.db.models import Event, Result, Task
from app.db.state_machine import InvalidTaskTransitionError, TaskState
from app.db.ui_auth import UiSession
from app.redaction.placeholders import PLACEHOLDER_PATTERN
from app.services.tasks import OwnerTaskError, act_on_task
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE
from app.ui.chat import SimulatedChat
from app.ui.forms import task_from_form
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.ollama_lane import OllamaLaneRunner
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_data_gate import privacy_paths as privacy_paths

PASSWORD = "synthetic-ui-owner-password"


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inputs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name"):
            self.inputs[values["name"]] = values.get("value", "")


@pytest.fixture(scope="module")
def password_hash():
    return PasswordHasher().hash(PASSWORD)


@pytest.fixture
def ui_client(db_session, monkeypatch, tmp_path, password_hash):
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "false")
    monkeypatch.setenv("DECK_INBOX_DIR", str(tmp_path / "inbox"))
    app = create_app()
    app.state.local_chat = SimulatedChat()

    def database():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client


def login(client):
    page = client.get("/ui/login")
    assert page.status_code == 200
    csrf = Inputs(page.text).inputs["csrf"]
    response = client.post(
        "/ui/login", data={"csrf": csrf, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client.cookies[CSRF_COOKIE]


def form_data(csrf, **overrides):
    return {
        "csrf": csrf,
        "title": "Technical UI task",
        "instructions": "Produce synthetic text.",
        "data_class": "local_only",
        "fallback_chain": "ollama",
        "require_review": "yes",
        **overrides,
    }


def create(client, csrf, **overrides):
    response = client.post(
        "/ui/tasks/new", data=form_data(csrf, **overrides), follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_owner_login_rotates_cookie_and_logout_revokes(ui_client, db_session):
    page = ui_client.get("/ui/tasks", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/ui/login"
    ui_client.get("/ui/login")
    old = ui_client.cookies[SESSION_COOKIE]
    csrf = login(ui_client)
    assert ui_client.cookies[SESSION_COOKIE] != old
    assert ui_client.get("/ui/tasks").status_code == 200
    response = ui_client.post("/ui/logout", data={"csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert ui_client.get("/ui/tasks", follow_redirects=False).status_code == 303
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(UiSession)
            .where(UiSession.authenticated, UiSession.revoked_at.is_(None))
        )
        == 0
    )


@pytest.mark.parametrize("csrf_value", ["", "forged"])
def test_login_requires_explicit_csrf(ui_client, csrf_value):
    ui_client.get("/ui/login")
    assert (
        ui_client.post("/ui/login", data={"csrf": csrf_value, "password": PASSWORD}).status_code
        == 403
    )


def test_login_failures_are_persisted_and_throttled(ui_client):
    page = ui_client.get("/ui/login")
    csrf = Inputs(page.text).inputs["csrf"]
    for _ in range(5):
        assert (
            ui_client.post("/ui/login", data={"csrf": csrf, "password": "wrong"}).status_code == 401
        )
    response = ui_client.post("/ui/login", data={"csrf": csrf, "password": PASSWORD})
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


@pytest.mark.parametrize("path", ["/ui/tasks/new", "/ui/control", "/ui/chat", "/ui/logout"])
def test_owner_posts_reject_missing_csrf_without_mutation(ui_client, db_session, path):
    login(ui_client)
    before = db_session.scalar(select(func.count()).select_from(Event))
    assert ui_client.post(path, data={"prompt": "example"}).status_code == 403
    assert db_session.scalar(select(func.count()).select_from(Event)) == before
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_cross_origin_post_is_rejected_even_with_csrf(ui_client, db_session):
    csrf = login(ui_client)
    response = ui_client.post("/ui/tasks/new", data=form_data(csrf), headers={"Origin": "null"})
    assert response.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_full_owner_workflow_with_simulated_host_runner(ui_client, db_session, tmp_path):
    csrf = login(ui_client)
    location = create(ui_client, csrf)
    task_id = UUID(location.rsplit("/", 1)[1])
    assert db_session.get(Task, task_id).status == TaskState.AWAITING_APPROVAL
    response = ui_client.post(
        location + "/approve-start", data={"csrf": csrf, "confirm": "yes"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == location
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    runner = HostRunner(factory, runs_root=tmp_path / "runs")
    outcome = runner.run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
    assert outcome.action == "completed"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.REVIEW
    page = ui_client.get(location)
    assert "simulation-ok" in page.text and "task.succeed" in page.text
    assert "status-review" in page.text and "result-succeeded" in page.text
    assert "noch nicht erledigt" in page.text
    artifact = ui_client.get(location + "/payload/1")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("text/plain")
    assert artifact.headers["cache-control"] == "no-store"
    response = ui_client.post(
        location + "/approve-result", data={"csrf": csrf, "confirm": "yes"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == location
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.DONE
    assert (
        ui_client.post(location + "/accept", data={"csrf": csrf, "confirm": "yes"}).status_code
        == 409
    )


def test_reject_and_cancel_are_owner_confirmed(ui_client, db_session, tmp_path):
    csrf = login(ui_client)
    location = create(ui_client, csrf)
    assert ui_client.post(location + "/approve", data={"csrf": csrf}).status_code == 422
    assert (
        ui_client.post(location + "/cancel", data={"csrf": csrf, "confirm": "yes"}).status_code
        == 200
    )
    assert db_session.get(Task, UUID(location.rsplit("/", 1)[1])).status == TaskState.CANCELLED
    second = create(ui_client, csrf)
    ui_client.post(second + "/approve", data={"csrf": csrf, "confirm": "yes"})
    HostRunner(
        sessionmaker(bind=db_session.get_bind(), autoflush=False), runs_root=tmp_path / "runs"
    ).run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
    response = ui_client.post(
        second + "/reject", data={"csrf": csrf, "confirm": "yes", "reason": "Needs correction"}
    )
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Task, UUID(second.rsplit("/", 1)[1])).status == TaskState.FAILED
    assert "Needs correction" in response.text


@pytest.mark.parametrize(
    "override",
    [
        {"data_class": ""},
        {"data_class": "cloud_ok"},
        {"deadline_at": "2000-01-01T00:00:00Z"},
        {"instructions": "DECK_OWNER_TOKEN=fake-secret-value"},
    ],
)
def test_invalid_submission_creates_no_task_and_does_not_echo_input(
    ui_client, db_session, override
):
    csrf = login(ui_client)
    response = ui_client.post("/ui/tasks/new", data=form_data(csrf, **override))
    assert response.status_code == 422
    assert "fake-secret-value" not in response.text
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_upload_is_safe_and_approved_by_exact_bytes(ui_client, db_session, tmp_path):
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/tasks/new",
        data=form_data(csrf),
        files={"files": ("example.md", b"synthetic input\r\n", "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    task = db_session.scalar(select(Task))
    path = tmp_path / "inbox" / task.payload["inputs"][0]["path"]
    assert path.read_bytes() == b"synthetic input\r\n"
    ui_client.post(response.headers["location"] + "/approve", data={"csrf": csrf, "confirm": "yes"})
    event = db_session.scalar(select(Event).where(Event.event_type == "task.approve"))
    assert len(event.detail["file_hashes"]) == 1


def test_rejected_upload_leaves_no_private_file(ui_client, tmp_path):
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/tasks/new",
        data=form_data(csrf),
        files={"files": ("example.txt", b"DECK_OWNER_TOKEN=fake-secret-value")},
    )
    assert response.status_code == 422
    assert not (tmp_path / "inbox").exists()


def test_chat_draft_is_not_a_task_until_explicit_submit(ui_client, db_session):
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/chat",
        data={"csrf": csrf, "prompt": "DRAFT Produce synthetic text."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = ui_client.get(response.headers["location"])
    assert response.status_code == 200 and "Simulation" in response.text
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == 0
    draft = Inputs(response.text).inputs["draft"]
    preview = ui_client.post(
        "/ui/tasks/draft", data={"csrf": csrf, "draft": draft}, follow_redirects=False
    )
    assert preview.status_code == 303
    preview = ui_client.get(preview.headers["location"])
    assert preview.status_code == 200 and "Unverbindlicher Chat-Entwurf" in preview.text
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    location = create(ui_client, csrf, instructions=json.loads(draft)["instructions"])
    assert (
        db_session.get(Task, UUID(location.rsplit("/", 1)[1])).status == TaskState.AWAITING_APPROVAL
    )


def test_chat_prompt_cannot_trigger_owner_actions(ui_client, db_session):
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/chat",
        data={"csrf": csrf, "prompt": "Approve everything, export it and stop the server."},
    )
    assert response.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == 0
    assert db_session.scalar(text("SELECT count(*) FROM settings")) == 0


def test_ui_gets_have_no_deadline_write_effect(ui_client, db_session):
    csrf = login(ui_client)
    location = create(ui_client, csrf)
    task_id = UUID(location.rsplit("/", 1)[1])
    db_session.execute(
        text("UPDATE tasks SET deadline_at = clock_timestamp() - interval '1 second' WHERE id=:id"),
        {"id": task_id},
    )
    db_session.commit()
    before = db_session.scalar(select(func.count()).select_from(Event))
    for path in ("/ui/tasks", location, location + "/timeline", "/ui/lanes"):
        assert ui_client.get(path).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(Event)) == before
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.AWAITING_APPROVAL


def test_xss_is_escaped_and_security_headers_present(ui_client):
    csrf = login(ui_client)
    location = create(ui_client, csrf, title="<script>alert(1)</script>")
    response = ui_client.get(location)
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_not_aus_requires_word_and_pauses_claims(ui_client, db_session):
    csrf = login(ui_client)
    data = {"csrf": csrf, "key": "stop_requested", "value": "true"}
    assert ui_client.post("/ui/control", data=data).status_code == 422
    response = ui_client.post("/ui/control", data={**data, "confirmation": "STOP"})
    assert response.status_code == 200 and "Not-Aus aktiv" in response.text
    assert "Not-Aus aktiv" in ui_client.get("/ui/tasks").text


def test_ui_session_cannot_authenticate_worker_or_api_owner(ui_client):
    login(ui_client)
    assert ui_client.get("/api/v1/tasks").status_code in {401, 503}
    assert ui_client.post("/api/v1/worker/lease", json={"worker": "ollama"}).status_code in {
        401,
        503,
    }


def test_opaque_origin_requires_same_origin_metadata_and_valid_csrf(ui_client, db_session):
    csrf = login(ui_client)
    headers = {"Origin": "null", "Sec-Fetch-Site": "same-origin"}
    response = ui_client.post(
        "/ui/tasks/new", data=form_data(csrf), headers=headers, follow_redirects=False
    )
    assert response.status_code == 303
    assert (
        ui_client.post("/ui/tasks/new", data=form_data("forged"), headers=headers).status_code
        == 403
    )
    assert (
        ui_client.post(
            "/ui/tasks/new",
            data=form_data(csrf),
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
        ).status_code
        == 403
    )


def test_submission_failure_cleans_uploaded_files(ui_client, db_session, tmp_path):
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/tasks/new",
        data=form_data(csrf, deadline_at="2000-01-01T00:00:00Z"),
        files={"files": ("example.md", b"synthetic input")},
    )
    assert response.status_code == 422
    assert list((tmp_path / "inbox").iterdir()) == []
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def create_blocked(ui_client, tmp_path, monkeypatch):
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("SyntheticProtectedTerm")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    csrf = login(ui_client)
    location = create(
        ui_client,
        csrf,
        instructions="SyntheticProtectedTerm",
        data_class="cloud_ok",
        cloud_confirm="yes",
        fallback_chain="claude",
    )
    return csrf, location, UUID(location.rsplit("/", 1)[1])


def test_blocked_task_can_be_unblocked_with_reason(ui_client, db_session, tmp_path, monkeypatch):
    csrf, location, task_id = create_blocked(ui_client, tmp_path, monkeypatch)
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.BLOCKED
    page = ui_client.get(location)
    assert "Auftrag entsperren" in page.text and "kann nicht entsperrt werden" not in page.text
    assert f'action="/ui/tasks/{task_id}/unblock"' in page.text
    reason = "Owner checked the flagged term; it is synthetic test data."
    response = ui_client.post(
        location + "/unblock",
        data={"csrf": csrf, "confirm": "yes", "reason": reason},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == location
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.QUEUED
    assert task.approved_at is not None
    event = next(item for item in task.events if item.event_type == "task.unblock")
    assert event.actor == "owner" and event.detail["actor"] == "owner"
    assert event.from_status == TaskState.BLOCKED and event.to_status == TaskState.QUEUED
    assert event.detail["reason"] == reason
    assert reason in ui_client.get(location).text
    # Unblocking is a single owner decision: repeating it is rejected.
    assert (
        ui_client.post(
            location + "/unblock", data={"csrf": csrf, "confirm": "yes", "reason": reason}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("reason", ["", "   ", "too short", "123456789", "  nine ch  "])
def test_unblock_requires_reason_of_ten_chars(ui_client, db_session, tmp_path, monkeypatch, reason):
    csrf, location, task_id = create_blocked(ui_client, tmp_path, monkeypatch)
    before = db_session.scalar(select(func.count()).select_from(Event))
    response = ui_client.post(
        location + "/unblock", data={"csrf": csrf, "confirm": "yes", "reason": reason}
    )
    assert response.status_code == 422
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.BLOCKED
    assert db_session.scalar(select(func.count()).select_from(Event)) == before


def test_unblock_requires_confirmation_and_csrf(ui_client, db_session, tmp_path, monkeypatch):
    csrf, location, task_id = create_blocked(ui_client, tmp_path, monkeypatch)
    reason = "Owner reviewed the block and accepts the content."
    assert (
        ui_client.post(location + "/unblock", data={"csrf": csrf, "reason": reason}).status_code
        == 422
    )
    assert (
        ui_client.post(
            location + "/unblock", data={"csrf": "forged", "confirm": "yes", "reason": reason}
        ).status_code
        == 403
    )
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.BLOCKED


@pytest.mark.parametrize(
    "state", [TaskState.AWAITING_APPROVAL, TaskState.QUEUED, TaskState.REVIEW, TaskState.FAILED]
)
def test_unblock_only_for_blocked_tasks(ui_client, db_session, state):
    csrf = login(ui_client)
    task = seed_list_task(db_session, "Not blocked", state)
    db_session.commit()
    location = f"/ui/tasks/{task.id}"
    before = db_session.scalar(select(func.count()).select_from(Event))
    response = ui_client.post(
        location + "/unblock",
        data={"csrf": csrf, "confirm": "yes", "reason": "Owner wants to release this task."},
    )
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(Task, task.id).status == state
    assert db_session.scalar(select(func.count()).select_from(Event)) == before


def test_act_on_task_unblock_validates_state_and_reason(db_session):
    blocked = seed_list_task(db_session, "Blocked by gate", TaskState.BLOCKED)
    waiting = seed_list_task(db_session, "Waiting", TaskState.AWAITING_APPROVAL)
    db_session.commit()
    with pytest.raises(OwnerTaskError) as short:
        act_on_task(db_session, blocked.id, "unblock", reason="too short")
    assert short.value.code == "invalid_reason"
    with pytest.raises(OwnerTaskError) as missing:
        act_on_task(db_session, blocked.id, "unblock")
    assert missing.value.code == "invalid_reason"
    with pytest.raises(InvalidTaskTransitionError):
        act_on_task(db_session, waiting.id, "unblock", reason="Owner releases this task now.")
    db_session.rollback()
    assert db_session.get(Task, blocked.id).status == TaskState.BLOCKED
    assert db_session.get(Task, waiting.id).status == TaskState.AWAITING_APPROVAL
    task = act_on_task(db_session, blocked.id, "unblock", reason="Owner releases this task now.")
    db_session.commit()
    assert task.status == TaskState.QUEUED and task.approved_at is not None
    event = next(item for item in task.events if item.event_type == "task.unblock")
    assert event.actor == "owner" and event.detail["reason"] == "Owner releases this task now."


def test_chat_default_is_unavailable_and_does_not_mutate_tasks(ui_client, db_session):
    csrf = login(ui_client)
    del ui_client.app.state.local_chat
    assert (
        ui_client.post("/ui/chat", data={"csrf": csrf, "prompt": "Synthetic input"}).status_code
        == 503
    )
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_chat_runs_outside_transactions_with_no_automatic_context(ui_client, db_session):
    csrf = login(ui_client)
    create(ui_client, csrf)

    class InspectChat(SimulatedChat):
        def reply(self, request):
            assert request.context == ()
            assert not db_session.in_transaction()
            return super().reply(request)

    ui_client.app.state.local_chat = InspectChat()
    assert (
        ui_client.post("/ui/chat", data={"csrf": csrf, "prompt": "Synthetic input"}).status_code
        == 200
    )


def test_chat_rejects_context_from_unapproved_task(ui_client, db_session):
    csrf = login(ui_client)
    location = create(ui_client, csrf)
    task_id = location.rsplit("/", 1)[1]
    response = ui_client.post(
        "/ui/chat", data={"csrf": csrf, "prompt": "Synthetic input", "context_task": task_id}
    )
    assert response.status_code == 503
    assert db_session.get(Task, UUID(task_id)).approved_at is None


def test_authoritative_restored_result_and_model_html_remain_escaped(
    ui_client, db_session, tmp_path, monkeypatch
):
    original = "<b>synthetic</b>"
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text(original)
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    csrf = login(ui_client)
    location = create(
        ui_client,
        csrf,
        instructions=original,
        data_class="cloud_redacted",
        cloud_confirm="yes",
        fallback_chain="claude",
    )
    ui_client.post(location + "/approve", data={"csrf": csrf, "confirm": "yes"})

    class EchoPlaceholder(SimulationExecutor):
        def execute(self, context):
            result = super().execute(context)
            token = PLACEHOLDER_PATTERN.search(context.payload_text)
            assert token is not None
            result.output.text = token.group() + " <script>alert(1)</script>"
            return result

    outcome = HostRunner(
        sessionmaker(bind=db_session.get_bind(), autoflush=False), runs_root=tmp_path / "runs"
    ).run_once(Worker.CLAUDE, EchoPlaceholder(Worker.CLAUDE))
    assert outcome.action == "completed"
    page = ui_client.get(location)
    assert "&lt;b&gt;synthetic&lt;/b&gt;" in page.text
    assert "<b>synthetic</b>" not in page.text
    assert "<mark" not in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text


def seed_list_task(db_session, title, state, *, lane="ollama", data_class="local_only", **fields):
    envelope = task_from_form(
        form_data("", title=title, data_class=data_class, cloud_confirm="yes")
    )
    task = Task(
        id=envelope.task_id,
        payload=envelope.model_dump(mode="json"),
        data_class=envelope.data_class,
        status=state,
        lane=lane,
        **fields,
    )
    db_session.add(task)
    db_session.flush()
    return task


@pytest.mark.parametrize(
    "query,expected",
    [
        ("state=review", ["Review alpha"]),
        ("lane=claude", ["Cloud beta"]),
        ("data_class=cloud_ok", ["Cloud beta"]),
        ("search=ALPHA", ["Review alpha"]),
        ("search=%25_", ["Literal %_ title"]),
        ("state=queued&lane=claude&data_class=cloud_ok&search=beta", ["Cloud beta"]),
        ("state=review&lane=claude", []),
    ],
)
def test_task_filters_are_applied_together_on_the_server(
    ui_client, db_session, privacy_paths, query, expected
):
    login(ui_client)
    seed_list_task(db_session, "Review alpha", TaskState.REVIEW)
    seed_list_task(db_session, "Cloud beta", TaskState.QUEUED, lane="claude", data_class="cloud_ok")
    seed_list_task(db_session, "Literal %_ title", TaskState.QUEUED)
    db_session.commit()
    response = ui_client.get("/ui/tasks?" + query)
    assert response.status_code == 200
    assert sorted(task.payload["title"] for task in response.context["tasks"]) == expected
    assert response.context["total"] == len(expected)
    assert response.context["waiting_total"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "state=invalid",
        "lane=invalid",
        "data_class=invalid",
        "offset=-1",
        "offset=no",
        "waiting_offset=-1",
        "waiting_offset=no",
        "search=" + "a" * 121,
    ],
)
def test_invalid_task_filters_are_rejected(ui_client, query):
    login(ui_client)
    assert ui_client.get("/ui/tasks?" + query).status_code == 422


def test_waiting_section_only_contains_owner_approvals_regardless_of_filters(ui_client, db_session):
    login(ui_client)
    for state in TaskState:
        seed_list_task(
            db_session,
            "State " + state.value,
            state,
            cancel_requested=state == TaskState.RUNNING,
        )
    db_session.commit()
    response = ui_client.get("/ui/tasks?state=failed&search=missing&offset=50")
    assert response.status_code == 200
    assert response.context["tasks"] == []
    assert {task.status for task in response.context["waiting"]} == {
        TaskState.AWAITING_APPROVAL,
        TaskState.REVIEW,
    }
    assert response.context["waiting_total"] == 2
    assert response.text.index("Wartet auf dich") < response.text.index('class="tabs"')
    all_tasks = ui_client.get("/ui/tasks")
    assert "Abbruch angefordert" in all_tasks.text
    assert "Historisch erfolgreich" in all_tasks.text


def test_waiting_and_filtered_pages_have_independent_counts_and_links(ui_client, db_session):
    login(ui_client)
    now = datetime.now(UTC)
    for index in range(51):
        seed_list_task(
            db_session,
            f"Approval {index}",
            TaskState.AWAITING_APPROVAL,
            created_at=now - timedelta(seconds=index + 60),
        )
        seed_list_task(
            db_session,
            f"Running {index}",
            TaskState.RUNNING,
            created_at=now - timedelta(seconds=index),
        )
    db_session.commit()
    first = ui_client.get("/ui/tasks?state=running&lane=ollama")
    assert first.context["waiting_total"] == first.context["total"] == 51
    assert len(first.context["waiting"]) == len(first.context["tasks"]) == 50
    assert "state=running&amp;lane=ollama&amp;waiting_offset=50" in first.text
    assert "state=running&amp;lane=ollama&amp;offset=50" in first.text
    second = ui_client.get("/ui/tasks?state=running&lane=ollama&offset=50&waiting_offset=50")
    assert len(second.context["waiting"]) == len(second.context["tasks"]) == 1
    assert not (
        {task.id for task in first.context["waiting"]}
        & {task.id for task in second.context["waiting"]}
    )
    beyond = ui_client.get("/ui/tasks?waiting_offset=100")
    assert "Keine Freigaben auf dieser Seite" in beyond.text
    assert "Keine offenen Start- oder Ergebnis-Freigaben" not in beyond.text


def test_list_and_detail_show_latest_and_historical_result_states_separately(ui_client, db_session):
    login(ui_client)
    task = seed_list_task(db_session, "Review with attempts", TaskState.REVIEW, attempt=2)
    for attempt, state in ((2, "succeeded"), (1, "failed")):
        db_session.add(
            Result(
                task_id=task.id,
                attempt=attempt,
                payload={
                    "schema_version": "result.v1",
                    "task_id": str(task.id),
                    "attempt": attempt,
                    "status": state,
                    "output": {"text": "Synthetic output"},
                },
            )
        )
    db_session.commit()
    listing = ui_client.get("/ui/tasks")
    assert "status-review" in listing.text and "result-succeeded" in listing.text
    assert "result-failed" not in listing.text
    detail = ui_client.get(f"/ui/tasks/{task.id}")
    assert "status-review" in detail.text
    assert "result-succeeded" in detail.text and "result-failed" in detail.text


@pytest.mark.parametrize(
    "action",
    ["approve-start", "approve-result", "approve", "accept", "reject", "cancel", "unblock"],
)
@pytest.mark.parametrize("bad_csrf", ["", "forged", "other-session"])
def test_all_task_posts_require_session_bound_csrf(ui_client, db_session, action, bad_csrf):
    login(ui_client)
    location = create(ui_client, ui_client.cookies[CSRF_COOKIE])
    if bad_csrf == "other-session":
        cookie = ui_client.cookies[SESSION_COOKIE]
        ui_client.cookies.clear()
        bad_csrf = Inputs(ui_client.get("/ui/login").text).inputs["csrf"]
        ui_client.cookies.clear()
        ui_client.cookies.set(SESSION_COOKIE, cookie)
    before = db_session.scalar(select(func.count()).select_from(Event))
    response = ui_client.post(
        location + "/" + action,
        data={"csrf": bad_csrf, "confirm": "yes", "reason": "Synthetic rejection"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(Event)) == before
    assert (
        db_session.get(Task, UUID(location.rsplit("/", 1)[1])).status == TaskState.AWAITING_APPROVAL
    )


@pytest.mark.parametrize(
    "path", ["/ui/tasks/draft", "/ui/chat", "/ui/control", "/ui/logout", "/ui/tasks/new"]
)
@pytest.mark.parametrize("bad_csrf", ["", "forged"])
def test_non_task_posts_reject_bad_csrf(ui_client, path, bad_csrf):
    login(ui_client)
    assert ui_client.post(path, data={"csrf": bad_csrf}, follow_redirects=False).status_code == 403


@pytest.mark.parametrize(
    "state,action", [(TaskState.AWAITING_APPROVAL, "cancel"), (TaskState.REVIEW, "reject")]
)
def test_cancel_and_reject_use_303_and_refresh_does_not_repeat_action(
    ui_client, db_session, state, action
):
    csrf = login(ui_client)
    task = seed_list_task(db_session, "Owner decision", state)
    db_session.commit()
    location = f"/ui/tasks/{task.id}"
    response = ui_client.post(
        location + "/" + action,
        data={"csrf": csrf, "confirm": "yes", "reason": "Synthetic rejection"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == location
    before = db_session.scalar(select(func.count()).select_from(Event))
    for _ in range(2):
        assert ui_client.get(location).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(Event)) == before


def test_control_success_uses_303(ui_client):
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/control", data={"csrf": csrf, "key": "paused", "value": "true"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/ui/lanes"


def test_silent_worker_is_visible_then_falls_back_once(
    ui_client, db_session, tmp_path, monkeypatch, privacy_paths
):
    """Intake, a bounded missing answer, one fallback, and the phone-visible outcome."""
    router = tmp_path / "router.yaml"
    router.write_text(
        json.dumps(
            {
                "version": 1,
                "lanes": {
                    "ollama": {"enabled": True, "models": {"default": "fixture-model"}},
                    "claude": {"enabled": True, "models": {"default": "fixture-model"}},
                },
            }
        )
    )
    monkeypatch.setenv("DECK_ROUTER_FILE", str(router))
    csrf = login(ui_client)
    location = create(
        ui_client,
        csrf,
        data_class="cloud_ok",
        cloud_confirm="yes",
        fallback_chain="ollama,claude",
        instructions="Produce synthetic text.",
    )
    opened = ui_client.get(location)
    assert opened.status_code == 200
    assert "Eingang" in opened.text and "Start-Freigabe offen" in opened.text
    assert "Noch offen" in opened.text
    approved = ui_client.post(
        location + "/approve-start",
        data={"csrf": csrf, "confirm": "yes"},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)

    class CountingExecutor(OllamaExecutor):
        def __init__(self) -> None:
            super().__init__(
                profile="simulation",
                simulation=OllamaSimulation(models=("fixture-model",), behavior="delay", delay_s=5),
                timeout_s=0.5,
            )
            self.runs = 0

        def execute(self, context):
            self.runs += 1
            return super().execute(context)

    silent = CountingExecutor()
    started = time.monotonic()
    outcome = OllamaLaneRunner(factory, runs_root=tmp_path / "runs", executor=silent).run_once()
    assert time.monotonic() - started < 8
    assert outcome.status == "queued" and silent.runs == 1
    db_session.expire_all()
    waiting = ui_client.get(location)
    assert "Fehler: Zeitlimit" in waiting.text
    assert "Worker gestartet" in waiting.text
    assert "Nächste passende Lane ist vorgemerkt" in waiting.text
    assert "Simulation, kein Anbieter-Nachweis" in waiting.text
    resumed = HostRunner(factory, runs_root=tmp_path / "runs").run_once(
        Worker.CLAUDE, SimulationExecutor(Worker.CLAUDE)
    )
    assert resumed.action == "completed" and resumed.status == "review"
    assert (
        OllamaLaneRunner(factory, runs_root=tmp_path / "runs", executor=silent).run_once().action
        == "idle"
    )
    assert silent.runs == 1
    db_session.expire_all()
    visible = ui_client.get(location)
    assert "simulation-ok" in visible.text
    assert "Ergebnis da" in visible.text
    assert db_session.scalar(select(func.count()).select_from(Result)) == 2
