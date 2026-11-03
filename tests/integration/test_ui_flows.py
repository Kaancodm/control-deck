"""Owner UI flows exercise the real business services and PostgreSQL state."""

from __future__ import annotations

import json
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
from app.db.models import Event, Task
from app.db.state_machine import TaskState
from app.db.ui_auth import UiSession
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE
from app.ui.chat import SimulatedChat
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor

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
    assert (
        ui_client.post(location + "/approve", data={"csrf": csrf, "confirm": "yes"}).status_code
        == 200
    )
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    runner = HostRunner(factory, runs_root=tmp_path / "runs")
    outcome = runner.run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
    assert outcome.action == "completed"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.REVIEW
    page = ui_client.get(location)
    assert "simulation-ok" in page.text and "task.succeed" in page.text
    artifact = ui_client.get(location + "/payload/1")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("text/plain")
    assert artifact.headers["cache-control"] == "no-store"
    assert (
        ui_client.post(location + "/accept", data={"csrf": csrf, "confirm": "yes"}).status_code
        == 200
    )
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
        "/ui/chat", data={"csrf": csrf, "prompt": "DRAFT Produce synthetic text."}
    )
    assert response.status_code == 200 and "Simulation" in response.text
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == 0
    draft = Inputs(response.text).inputs["draft"]
    preview = ui_client.post("/ui/tasks/draft", data={"csrf": csrf, "draft": draft})
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


def test_blocked_task_has_no_unblock_action(ui_client, db_session, tmp_path, monkeypatch):
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
    task = db_session.get(Task, UUID(location.rsplit("/", 1)[1]))
    assert task.status == TaskState.BLOCKED
    assert "kann nicht entsperrt werden" in ui_client.get(location).text
    assert (
        ui_client.post(location + "/unblock", data={"csrf": csrf, "confirm": "yes"}).status_code
        == 404
    )
    assert (
        ui_client.post(location + "/approve", data={"csrf": csrf, "confirm": "yes"}).status_code
        == 409
    )
    assert task.status == TaskState.BLOCKED


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
