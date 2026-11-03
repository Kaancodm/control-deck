"""Export may never publish a task under a lower (more public) data class."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.api.routes.knowledge import knowledge_problem
from app.core.schemas import DataClass
from app.db.state_machine import TaskState
from app.kb.contracts import KnowledgeError
from app.kb.export import export_task


def _session(task_class: DataClass) -> MagicMock:
    session = MagicMock()
    session.new, session.dirty, session.deleted = [], [], []
    session.scalar.return_value = SimpleNamespace(status=TaskState.DONE, data_class=task_class)
    return session


@pytest.mark.parametrize(
    ("task_class", "export_class"),
    [
        (DataClass.LOCAL_ONLY, DataClass.CLOUD_OK),
        (DataClass.LOCAL_ONLY, DataClass.CLOUD_REDACTED),
        (DataClass.CLOUD_REDACTED, DataClass.CLOUD_OK),
    ],
)
def test_export_rejects_downgrade_before_any_write(task_class, export_class) -> None:
    store = MagicMock()
    with pytest.raises(KnowledgeError) as error:
        export_task(_session(task_class), uuid4(), data_class=export_class, store=store)
    assert error.value.code == "export_class_forbidden"
    store.put_document.assert_not_called()


def test_downgrade_maps_to_conflict_problem() -> None:
    assert knowledge_problem(KnowledgeError("export_class_forbidden")).status_code == 409
