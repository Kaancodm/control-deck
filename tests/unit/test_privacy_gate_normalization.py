"""Normalization limits fail closed at the task boundary with safe errors."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.schemas import TaskV1
from app.privacy.config import PrivacySettings
from app.privacy.gate import PrivacyInputError, inspect_task
from tests.integration.test_database import _minimal_task_payload


def test_pathological_unicode_is_a_safe_input_error(tmp_path: Path) -> None:
    payload = _minimal_task_payload(uuid4())
    payload["instructions"] = "a" + "\u0301" * 1100
    with pytest.raises(PrivacyInputError) as caught:
        inspect_task(TaskV1.model_validate(payload), settings=PrivacySettings(inbox=tmp_path))
    assert caught.value.code == "unicode_too_complex"
    assert payload["instructions"] not in str(caught.value)
