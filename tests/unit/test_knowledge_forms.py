"""Only explicit owner form selections may attach classified knowledge."""

from uuid import uuid4

import pytest

from app.core.schemas import DataClass
from app.kb.contracts import ContextBlock, digest_text
from app.ui.forms import FormError, task_from_form
from tests.unit.test_ui_forms_chat import values


def test_form_preserves_explicit_context_order(monkeypatch):
    references = (str(uuid4()), str(uuid4()))
    blocks = tuple(
        ContextBlock(ref, DataClass.LOCAL_ONLY, "Safe context", digest_text("Safe context"))
        for ref in references
    )
    monkeypatch.setattr("app.ui.forms.resolve_submission_contexts", lambda task: blocks)
    task = task_from_form(values() | {"context_refs": "\n".join(references)})
    assert tuple(item.ref for item in task.context_refs) == references


@pytest.mark.parametrize(
    "references", ["not-a-uuid", "../document", "https://example.invalid", "x" * 1025]
)
def test_invalid_form_reference_rejected_before_resolution(monkeypatch, references):
    def forbidden(task):
        pytest.fail("Malformed references must not reach the adapter")

    monkeypatch.setattr("app.ui.forms.resolve_submission_contexts", forbidden)
    with pytest.raises(FormError):
        task_from_form(values() | {"context_refs": references})


def test_form_secret_context_rejected_before_upload_storage(monkeypatch):
    reference = str(uuid4())
    secret = "DECK_OWNER_TOKEN=fake-secret-credential-value"
    block = ContextBlock(reference, DataClass.LOCAL_ONLY, secret, digest_text(secret))
    monkeypatch.setattr("app.ui.forms.resolve_submission_contexts", lambda task: (block,))
    with pytest.raises(FormError):
        task_from_form(values() | {"context_refs": reference})


def test_form_rejects_local_context_even_if_resolver_is_faulty(monkeypatch, tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    reference = str(uuid4())
    block = ContextBlock(
        reference, DataClass.LOCAL_ONLY, "Safe context", digest_text("Safe context")
    )
    monkeypatch.setattr("app.ui.forms.resolve_submission_contexts", lambda task: (block,))
    with pytest.raises(FormError):
        task_from_form(
            values() | {"context_refs": reference, "data_class": "cloud_ok", "cloud_confirm": "yes"}
        )
