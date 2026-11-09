"""Non-blocking assistant talk job and SSE helpers."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

from app.assistant.async_turns import get_turn, start_dialogue_turn, stream_sse_events, turn_snapshot
from app.assistant.dialogue import DialogueReply
class _FakeSession:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def _fake_session_factory():
    class _Factory:
        def __call__(self) -> _FakeSession:
            return _FakeSession()

    return _Factory()


def test_start_dialogue_turn_finishes_and_snapshot() -> None:
    def fake_reply(*_args: object, **_kwargs: object) -> DialogueReply:
        on_chunk = _kwargs.get("on_chunk")
        if callable(on_chunk):
            on_chunk("Teil")
        return DialogueReply(
            answer="Loopback, Identität nicht belegt. ok",
            origin="ollama_loopback",
            evidence_status="loopback_unverified",
            data_class="local_only",
            persisted=False,
            profile_written=False,
        )

    with patch("app.assistant.async_turns.reply_to_owner", side_effect=fake_reply):
        turn_id = start_dialogue_turn(
            _fake_session_factory(),
            message="Hallo",
            data_class="local_only",
            profile_notes=None,
            history=(),
            persist=False,
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            job = get_turn(turn_id)
            assert job is not None
            if job.state == "done":
                break
            time.sleep(0.02)
    job = get_turn(turn_id)
    assert job is not None
    assert job.state == "done"
    assert job.partial == "Teil"
    snap = turn_snapshot(job)
    assert snap["answer"].startswith("Loopback")


def test_stream_sse_events_emits_done() -> None:
    def fake_reply(*_args: object, **_kwargs: object) -> DialogueReply:
        return DialogueReply(
            answer="Loopback, Identität nicht belegt. fertig",
            origin="mock",
            evidence_status="mock",
            data_class="local_only",
            persisted=False,
            profile_written=False,
        )

    with patch("app.assistant.async_turns.reply_to_owner", side_effect=fake_reply):
        turn_id = start_dialogue_turn(
            _fake_session_factory(),
            message="x",
            data_class="local_only",
            profile_notes=None,
            history=(),
            persist=False,
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            job = get_turn(turn_id)
            if job is not None and job.state == "done":
                break
            time.sleep(0.02)
    frames = list(stream_sse_events(turn_id, poll_timeout_s=2.0))
    assert any(frame.startswith("event: done") for frame in frames)
    payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames if "data: " in frame]
    assert any(item.get("state") == "done" or "answer" in item for item in payloads)
