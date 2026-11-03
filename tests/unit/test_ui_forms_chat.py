"""Drafts are explicit validated data, never owner actions or automatic context."""

import json
from pathlib import Path

import pytest

from app.ui.chat import ChatError, ChatRequest, SimulatedChat, ask_local, parse_reply
from app.ui.files import Upload
from app.ui.forms import FormError, form_from_draft, task_from_form, validate_draft


def values():
    return {
        "title": "Example",
        "instructions": "Summarize the supplied text.",
        "data_class": "local_only",
        "fallback_chain": "ollama",
        "require_review": "yes",
    }


def draft_payload():
    return json.loads(Path("tests/unit/examples/task_valid_01.json").read_text())


def test_form_keeps_class_explicit_and_generates_identity():
    data = values()
    first = task_from_form(data)
    second = task_from_form(data)
    assert first.task_id != second.task_id
    assert first.requested_by.value == "ui"
    assert first.policy.require_review
    del data["data_class"]
    with pytest.raises(FormError):
        task_from_form(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("fallback_chain", "claude,claude"),
        ("max_attempts", "6"),
        ("timeout_s", "5"),
        ("acceptance", "not json"),
        ("title", ""),
    ],
)
def test_invalid_task_form(field, value):
    with pytest.raises(FormError):
        task_from_form({**values(), field: value})


def test_cloud_form_needs_explicit_confirmation():
    with pytest.raises(FormError, match="check") as failure:
        task_from_form({**values(), "data_class": "cloud_ok"})
    assert failure.value.code == "cloud_confirmation_required"


def test_secret_upload_rejected_before_writer():
    with pytest.raises(FormError):
        task_from_form(values(), [Upload("input.txt", b"DECK_OWNER_TOKEN=fake-secret-value")])


@pytest.mark.parametrize(
    "change",
    [
        {"data_class": "cloud_ok"},
        {"context_refs": [{"source": "knowledge_db", "ref": "unselected"}]},
        {"inputs": [{"name": "file", "type": "file", "path": "unselected.txt"}]},
        {"action": "approve"},
    ],
)
def test_draft_cannot_choose_cloud_files_context_or_actions(change):
    with pytest.raises(FormError):
        validate_draft(json.dumps({**draft_payload(), **change}))


def test_draft_prefills_fields_but_not_its_identity_or_transcript():
    draft = validate_draft(json.dumps(draft_payload()))
    form = form_from_draft(draft)
    result = task_from_form(form)
    assert result.instructions == draft.instructions
    assert result.task_id != draft.task_id
    assert "task_id" not in form and "context_refs" not in form


def test_fake_cli_returns_detached_validated_draft():
    reply = ask_local(SimulatedChat(), ChatRequest("DRAFT Produce an example."))
    assert reply.simulation and reply.draft is not None
    assert reply.draft.data_class.value == "local_only"
    assert reply.draft.policy.require_review


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"{}",
        b"not json",
        b'{"answer":"ok","action":"approve"}',
        b'{"answer":"DECK_SECRET_KEY=fake-secret-value"}',
    ],
)
def test_invalid_chat_reply(raw):
    with pytest.raises(ChatError):
        parse_reply(raw)


def test_nonlocal_chat_adapter_is_never_called():
    class Remote:
        lane = "claude"

        def reply(self, request):
            pytest.fail("a remote adapter received local chat")

    with pytest.raises(ChatError) as error:
        ask_local(Remote(), ChatRequest("example"))
    assert error.value.code == "chat_lane_forbidden"


@pytest.mark.parametrize(
    "prompt,context",
    [
        ("", ()),
        ("a" * 32769, ()),
        ("example", ("x",) * 5),
        ("DECK_OWNER_TOKEN=fake-secret-value", ()),
    ],
)
def test_chat_request_bounds(prompt, context):
    with pytest.raises(ChatError):
        ChatRequest(prompt, context)
