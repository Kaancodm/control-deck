"""Only supported privacy findings may enter the host redaction path."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.schemas import TaskV1, Worker
from app.privacy.config import PrivacySettings
from app.privacy.gate import inspect_task
from app.redaction.payload import build_payload
from tests.integration.test_database import _minimal_task_payload


def _task(data_class: str, text: str) -> TaskV1:
    payload = _minimal_task_payload(uuid4())
    payload.update(data_class=data_class, instructions=text)
    return TaskV1.model_validate(payload)


@pytest.mark.parametrize(
    "text",
    [
        "synthetic" + "@" + "example.invalid",
        "+41 " + "99 000 00 00",
        "CH00" + "0" * 17,
        "756." + "0000.0000.00",
        ".".join(map(str, (10, 1, 2, 3))),
        "SYNTHETIC RESTRICTED",
    ],
)
def test_supported_cloud_findings_require_the_redaction_class(tmp_path: Path, text: str) -> None:
    settings = PrivacySettings(
        inbox=tmp_path, blocklist=("SYNTHETIC RESTRICTED",), blocklist_configured=True
    )
    assert inspect_task(_task("cloud_ok", text), settings=settings).blocked
    report = inspect_task(_task("cloud_redacted", text), settings=settings)
    assert report.findings
    assert not report.blocked
    transport = build_payload(_task("cloud_redacted", text), lane=Worker.CLAUDE, settings=settings)
    assert text not in transport.text
    assert transport.applied


@pytest.mark.parametrize("text", ["/home/synthetic/private", "synthetic.internal"])
def test_unsupported_findings_stay_blocked_before_redaction(tmp_path: Path, text: str) -> None:
    settings = PrivacySettings(inbox=tmp_path, blocklist_configured=True)
    assert inspect_task(_task("cloud_redacted", text), settings=settings).blocked


def test_unsupported_later_field_cannot_hide_behind_supported_finding_cap(tmp_path: Path) -> None:
    payload = _minimal_task_payload(uuid4())
    payload.update(
        data_class="cloud_redacted",
        instructions=" ".join("synthetic" + str(i) + "@example.invalid" for i in range(128)),
        inputs=[{"type": "text", "name": "brief", "value": "synthetic.internal"}],
    )
    report = inspect_task(
        TaskV1.model_validate(payload),
        settings=PrivacySettings(inbox=tmp_path, blocklist_configured=True),
    )
    assert report.blocked


@pytest.mark.parametrize("data_class", ["local_only", "cloud_ok", "cloud_redacted"])
def test_redaction_admission_never_permits_credentials(tmp_path: Path, data_class: str) -> None:
    task = _task(data_class, "DECK_OWNER_TOKEN=" + "synthetic-credential-value")
    report = inspect_task(task, settings=PrivacySettings(inbox=tmp_path, blocklist_configured=True))
    assert report.blocked and report.has_secrets
