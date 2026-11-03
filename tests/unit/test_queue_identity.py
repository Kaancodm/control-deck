"""Queue admission never invents a caller's worker identity."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.db.queue import dequeue_next_queued_task


def test_queue_requires_explicit_worker_before_database_access() -> None:
    session = Mock(spec=Session)
    with pytest.raises(TypeError, match="worker"):
        dequeue_next_queued_task(session, lease_ttl=timedelta(seconds=30))
    assert session.mock_calls == []


@pytest.mark.parametrize("worker", [None, "ollama", "claude", "unknown"])
def test_queue_rejects_untyped_worker_before_database_access(worker: object) -> None:
    session = Mock(spec=Session)
    with pytest.raises(ValueError, match="known worker"):
        dequeue_next_queued_task(session, lease_ttl=timedelta(seconds=30), worker=worker)
    assert session.mock_calls == []
