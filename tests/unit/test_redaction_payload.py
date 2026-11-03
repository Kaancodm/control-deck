"""The outgoing payload is complete, rechecked, and contains no local mapping."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.schemas import TaskV1, Worker
from app.privacy import gate
from app.privacy.config import PrivacyConfigurationError, PrivacySettings
from app.privacy.gate import PrivacyInputError
from app.privacy.scanner import scan_text
from app.redaction import payload as payload_module
from app.redaction.engine import RedactionError, RedactionOutcome
from app.redaction.payload import LaneNotAllowedError, build_payload, render_payload
from tests.integration.test_database import _minimal_task_payload


def _email(name: str = "synthetic") -> str:
    return name + "@" + "example.invalid"


def _task(**changes: object) -> TaskV1:
    payload = _minimal_task_payload(uuid4())
    payload["data_class"] = "cloud_redacted"
    payload.update(changes)
    return TaskV1.model_validate(payload)


@pytest.fixture
def settings(tmp_path: Path) -> PrivacySettings:
    return PrivacySettings(inbox=tmp_path, blocklist_configured=True)


@pytest.mark.parametrize(
    "value,kind",
    [
        (_email(), "EMAIL"),
        ("+41 " + "99 000 00 00", "PHONE"),
        ("CH00" + "0" * 17, "IBAN"),
        ("756." + "0000.0000.00", "AHV"),
        (".".join(map(str, (10, 1, 2, 3))), "IP"),
        ("SYNTHETIC-RESTRICTED-TERM", "TERM"),
    ],
)
def test_supported_kinds_are_removed_from_complete_transport(
    settings: PrivacySettings, value: str, kind: str
) -> None:
    configured = PrivacySettings(
        inbox=settings.inbox, blocklist=("SYNTHETIC-RESTRICTED-TERM",), blocklist_configured=True
    )
    built = build_payload(_task(instructions=value), lane=Worker.CLAUDE, settings=configured)

    assert value not in built.text
    assert "_" + kind + "_" in built.text
    assert built.applied is True and built.placeholders == 1
    assert built.sha256 == hashlib.sha256(built.text.encode("utf-8")).hexdigest()
    assert scan_text(built.text, source="transport", blocklist=configured.blocklist) == []
    assert not hasattr(built, "mapping")
    assert value not in repr(built)


def test_every_metadata_field_and_attachment_is_in_the_redacted_transport(
    settings: PrivacySettings,
) -> None:
    values = [_email("synthetic" + str(index)) for index in range(7)]
    (settings.inbox / "brief.txt").write_text(values[6], encoding="utf-8")
    task = _task(
        title=values[0],
        instructions=values[1],
        inputs=[
            {"type": "text", "name": values[2], "value": values[3]},
            {"type": "file", "name": "attachment", "path": "brief.txt", "mime": "text/plain"},
        ],
        acceptance=[{"type": "must_contain", "value": values[4]}],
        labels={values[5]: "ordinary label"},
    )

    built = build_payload(task, lane=Worker.CODEX, settings=settings)

    assert built.placeholders == len(values)
    assert all(value not in built.text for value in values)
    assert "ordinary label" in built.text
    assert "text/plain" in built.text
    assert "brief.txt" in built.text


@pytest.mark.parametrize("data_class", ["local_only", "cloud_redacted", "cloud_ok"])
@pytest.mark.parametrize(
    "credential",
    ["DECK_OWNER_TOKEN=" + "synthetic-value", "POSTGRES_PASSWORD:\n" + "synthetic-value"],
)
def test_secret_intake_is_blocked_for_every_class_including_multiline_assignment(
    settings: PrivacySettings, data_class: str, credential: str
) -> None:
    lane = Worker.OLLAMA if data_class == "local_only" else Worker.CLAUDE
    with pytest.raises(PrivacyInputError) as caught:
        build_payload(
            _task(data_class=data_class, instructions=credential), lane=lane, settings=settings
        )
    assert credential not in str(caught.value)
    assert "synthetic-value" not in str(caught.value)


@pytest.mark.parametrize("lane", [Worker.CLAUDE, Worker.CODEX])
def test_local_only_never_builds_for_a_cloud_lane(settings: PrivacySettings, lane: Worker) -> None:
    with pytest.raises(LaneNotAllowedError):
        build_payload(_task(data_class="local_only"), lane=lane, settings=settings)


def test_local_text_is_preserved_and_never_needs_a_map(settings: PrivacySettings) -> None:
    original = _email()
    built = build_payload(
        _task(data_class="local_only", instructions=original), lane=Worker.OLLAMA, settings=settings
    )
    assert original in built.text
    assert built.applied is False and built.placeholders == 0
    assert original not in repr(built)


def test_cloud_ok_cannot_bypass_personal_data_findings(settings: PrivacySettings) -> None:
    with pytest.raises(PrivacyInputError):
        build_payload(
            _task(data_class="cloud_ok", instructions=_email()),
            lane=Worker.CLAUDE,
            settings=settings,
        )


def test_cloud_ok_clean_text_keeps_its_bytes(settings: PrivacySettings) -> None:
    task = _task(data_class="cloud_ok", instructions="ordinary text\nsecond line")
    built = build_payload(task, lane=Worker.CLAUDE, settings=settings)
    assert "ordinary text\nsecond line" in built.text
    assert built.applied is False and built.placeholders == 0
    assert built.sha256 == hashlib.sha256(built.text.encode()).hexdigest()


@pytest.mark.parametrize(
    "policy",
    [
        {"allowed_workers": ["ollama"]},
        {"forbidden_workers": ["claude"]},
    ],
)
def test_worker_policy_is_enforced_before_rendering(
    settings: PrivacySettings, policy: dict[str, list[str]]
) -> None:
    with pytest.raises(LaneNotAllowedError):
        build_payload(_task(policy=policy), lane=Worker.CLAUDE, settings=settings)


def test_unresolved_context_is_rejected(settings: PrivacySettings) -> None:
    task = _task(context_refs=[{"source": "knowledge_db", "ref": "synthetic-reference"}])
    with pytest.raises(PrivacyInputError) as caught:
        build_payload(task, lane=Worker.CLAUDE, settings=settings)
    assert caught.value.code == "context_unavailable"


def test_cloud_requires_explicit_privacy_configuration(tmp_path: Path) -> None:
    with pytest.raises(PrivacyConfigurationError):
        build_payload(_task(), lane=Worker.CLAUDE, settings=PrivacySettings(inbox=tmp_path))


def test_attachment_text_is_taken_from_one_exact_scan_read(
    settings: PrivacySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _email("scanned")
    replacement = "sk-" + "TEST-CHANGED-AFTER-READ"
    path = settings.inbox / "brief.txt"
    path.write_text(original, encoding="utf-8")
    actual_read = gate.read_text_input
    reads: list[str] = []

    def read_once(*args, **kwargs):
        text = actual_read(*args, **kwargs)
        reads.append(text)
        path.write_text(replacement, encoding="utf-8")
        return text

    monkeypatch.setattr(gate, "read_text_input", read_once)
    task = _task(inputs=[{"type": "file", "name": "brief", "path": "brief.txt"}])
    built = build_payload(task, lane=Worker.CLAUDE, settings=settings)

    assert reads == [original]
    assert original not in built.text and replacement not in built.text
    assert built.placeholders == 1


def test_render_preserves_unicode_and_line_endings_before_redaction(
    settings: PrivacySettings,
) -> None:
    content = "\ufeffsynthetic cafe\u0301\r\nsecond line"
    (settings.inbox / "brief.txt").write_bytes(content.encode("utf-8"))
    task = _task(inputs=[{"type": "file", "name": "brief", "path": "brief.txt"}])
    assert content in render_payload(task, settings=settings)


def test_unsupported_protected_identifier_does_not_leave_redaction(
    settings: PrivacySettings,
) -> None:
    identifier = "synthetic" + ".internal"
    with pytest.raises(RedactionError) as caught:
        build_payload(_task(instructions=identifier), lane=Worker.CLAUDE, settings=settings)
    assert identifier not in str(caught.value)


def test_final_transport_limit_counts_utf8_bytes_after_placeholder_expansion(
    settings: PrivacySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    oversized = "ü" * (5 * 1024 * 1024 // 2 + 1)
    monkeypatch.setattr(
        payload_module,
        "redact",
        lambda *args, **kwargs: RedactionOutcome(oversized, {}, {}, False),
    )
    with pytest.raises(PrivacyInputError) as caught:
        build_payload(_task(), lane=Worker.CLAUDE, settings=settings)
    assert caught.value.code == "payload_too_large"


@pytest.mark.parametrize("data_class", ["local_only", "cloud_redacted", "cloud_ok"])
def test_final_rendered_text_cannot_introduce_an_unscanned_secret(
    settings: PrivacySettings, monkeypatch: pytest.MonkeyPatch, data_class: str
) -> None:
    credential = "DECK_OWNER_TOKEN=" + "synthetic-secret"
    monkeypatch.setattr(payload_module, "render_payload", lambda *args, **kwargs: credential)
    lane = Worker.OLLAMA if data_class == "local_only" else Worker.CLAUDE
    with pytest.raises((PrivacyInputError, RedactionError)) as caught:
        build_payload(_task(data_class=data_class), lane=lane, settings=settings)
    assert credential not in str(caught.value)
