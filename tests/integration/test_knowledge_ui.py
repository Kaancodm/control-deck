"""Owner knowledge UI enforces authentication, CSRF and separate export decisions."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.schemas import DataClass, Worker
from app.db.models import Task
from app.kb.contracts import EmbeddingSpec, KnowledgeError
from app.kb.state import KnowledgeExport
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_knowledge_store import kb_engine as kb_engine
from tests.integration.test_knowledge_store import knowledge_store as knowledge_store
from tests.integration.test_ui_flows import create, login
from tests.integration.test_ui_flows import password_hash as password_hash
from tests.integration.test_ui_flows import ui_client as ui_client
from tests.support.kb import use_contract


@pytest.fixture(autouse=True)
def simulation_profile(monkeypatch):
    monkeypatch.setenv("DECK_KB_PROFILE", "simulation")


def example_document(store, content="Selected local knowledge"):
    identifier = uuid4()
    store.put_document(
        document_id=identifier, title="Knowledge", content=content, data_class=DataClass.LOCAL_ONLY
    )
    return str(identifier)


def done_task(client, session, tmp_path, csrf):
    path = create(client, csrf)
    response = client.post(path + "/approve", data={"csrf": csrf, "confirm": "yes"})
    assert response.status_code == 200
    factory = sessionmaker(bind=session.get_bind(), autoflush=False)
    assert (
        HostRunner(factory, runs_root=tmp_path / "runs")
        .run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
        .action
        == "completed"
    )
    assert client.post(path + "/accept", data={"csrf": csrf, "confirm": "yes"}).status_code == 200
    return path


def test_knowledge_ui_requires_login_and_csrf(ui_client, knowledge_store):
    response = ui_client.get("/ui/knowledge", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/ui/login"
    login(ui_client)
    assert (
        ui_client.post(
            "/ui/knowledge", data={"query": "text", "data_class": "local_only"}
        ).status_code
        == 403
    )


def test_search_context_is_escaped_and_selection_prefills_explicit_form(ui_client, knowledge_store):
    reference = example_document(knowledge_store, "<script>window.knowledgeAttack=true</script>")
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/knowledge", data={"csrf": csrf, "query": "text", "data_class": "local_only"}
    )
    assert response.status_code == 200
    assert (
        "&lt;script&gt;" in response.text and "<script>window.knowledgeAttack" not in response.text
    )
    selected = ui_client.get(f"/ui/tasks/new?context_ref={reference}&data_class=local_only")
    assert reference in selected.text
    path = create(ui_client, csrf, context_refs=reference)
    detail = ui_client.get(path)
    assert "Ausgewählter Wissens-Kontext" in detail.text
    assert "&lt;script&gt;" in detail.text


def test_search_for_cloud_never_displays_local_context(ui_client, knowledge_store):
    example_document(knowledge_store, "Local-only hidden content")
    csrf = login(ui_client)
    response = ui_client.post(
        "/ui/knowledge", data={"csrf": csrf, "query": "text", "data_class": "cloud_ok"}
    )
    assert response.status_code == 200
    assert "Local-only hidden content" not in response.text
    assert "Keine erlaubten Einträge" in response.text


def test_search_separates_input_faults_from_unavailable_knowledge(ui_client, knowledge_store):
    csrf = login(ui_client)
    secret = ui_client.post(
        "/ui/knowledge",
        data={
            "csrf": csrf,
            "query": "DECK_OWNER_TOKEN=fake-secret-credential-value",
            "data_class": "local_only",
        },
    )
    assert secret.status_code == 422
    assert "fake-secret-credential-value" not in secret.text
    use_contract(knowledge_store.engine, EmbeddingSpec("ollama", "qwen3-embedding:0.6b", 1024))
    mismatch = ui_client.post(
        "/ui/knowledge", data={"csrf": csrf, "query": "text", "data_class": "local_only"}
    )
    assert mismatch.status_code == 503
    assert "knowledge_contract_mismatch" in mismatch.text


@pytest.mark.parametrize("missing", ["csrf", "confirm", "data_class"])
def test_export_form_requires_each_explicit_owner_field(
    ui_client, db_session, knowledge_store, tmp_path, missing
):
    csrf = login(ui_client)
    path = done_task(ui_client, db_session, tmp_path, csrf)
    values = {"csrf": csrf, "confirm": "yes", "data_class": "local_only"}
    del values[missing]
    response = ui_client.post(path + "/export", data=values)
    assert response.status_code in {403, 422}
    assert db_session.scalar(select(func.count()).select_from(KnowledgeExport)) == 0


def test_export_form_writes_only_after_separate_owner_submit(
    ui_client, db_session, knowledge_store, tmp_path
):
    csrf = login(ui_client)
    path = done_task(ui_client, db_session, tmp_path, csrf)
    assert db_session.scalar(select(func.count()).select_from(KnowledgeExport)) == 0
    response = ui_client.post(
        path + "/export", data={"csrf": csrf, "confirm": "yes", "data_class": "local_only"}
    )
    assert response.status_code == 200, response.text
    assert "Export abgeschlossen" in response.text
    assert db_session.get(Task, UUID(path.rsplit("/", 1)[-1])).status.value == "done"
    assert db_session.scalar(select(func.count()).select_from(KnowledgeExport)) == 1


@pytest.mark.parametrize(
    "code,expected_status",
    [
        ("export_class_forbidden", 409),
        ("knowledge_export_conflict", 409),
        ("secret_detected", 422),
        ("embedding_input_too_long", 422),
        ("embedding_timeout", 503),
        ("embedding_unavailable", 503),
        ("embedding_model_missing", 503),
        ("knowledge_contract_mismatch", 503),
    ],
)
def test_export_form_aligns_knowledge_error_statuses_with_api(
    ui_client, db_session, knowledge_store, tmp_path, monkeypatch, code, expected_status
):
    csrf = login(ui_client)
    path = done_task(ui_client, db_session, tmp_path, csrf)

    def fail_export(*_args, **_kwargs):
        raise KnowledgeError(code)

    monkeypatch.setattr("app.ui.knowledge.export_task", fail_export)
    response = ui_client.post(
        path + "/export", data={"csrf": csrf, "confirm": "yes", "data_class": "local_only"}
    )
    assert response.status_code == expected_status
    assert code in response.text
