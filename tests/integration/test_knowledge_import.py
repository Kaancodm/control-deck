"""The simulation import uses the bounded inbox reader and an explicit class."""

import os
import subprocess
import sys
from uuid import UUID

from app.core.schemas import DataClass
from tests.integration.test_knowledge_store import kb_engine as kb_engine
from tests.integration.test_knowledge_store import knowledge_store as knowledge_store


def run_import(tmp_path, *arguments):
    return subprocess.run(
        [sys.executable, "scripts/kb_import.py", *arguments],
        env=dict(os.environ) | {"DECK_INBOX_DIR": str(tmp_path), "DECK_KB_PROFILE": "simulation"},
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_import_roundtrip_and_explicit_class(knowledge_store, tmp_path):
    content = "Imported e\u0301\r\nexact bytes"
    (tmp_path / "input.md").write_bytes(content.encode())
    result = run_import(
        tmp_path, "input.md", "--title", "Imported", "--data-class", "local_only", "--simulate"
    )
    assert result.returncode == 0, result.stderr
    reference = str(UUID(result.stdout.strip()))
    assert knowledge_store.resolve((reference,), task_class=DataClass.LOCAL_ONLY)[0].text.endswith(
        content
    )


def test_import_rejects_symlink_without_echoing_path(knowledge_store, tmp_path):
    original = tmp_path / "source.md"
    original.write_text("Text")
    (tmp_path / "link.md").symlink_to(original)
    result = run_import(
        tmp_path, "link.md", "--title", "Imported", "--data-class", "local_only", "--simulate"
    )
    assert result.returncode == 1
    assert str(tmp_path) not in result.stderr and "Traceback" not in result.stderr


def test_import_requires_class_before_reading_any_input(tmp_path):
    result = run_import(tmp_path, "missing.md", "--title", "Imported", "--simulate")
    assert result.returncode == 2
    assert "--data-class" in result.stderr
