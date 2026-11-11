"""Non-blocking assistant talk job, SSE, and streaming safety."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from app.assistant import async_turns
from app.assistant.async_turns import (
    _MAX_QUEUED,
    get_turn,
    start_dialogue_turn,
    stream_sse_events,
)
from app.assistant.dialogue import DialogueReply
from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import LOOPBACK_NOTICE, StreamingReplySanitizer


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


def _reset_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    with async_turns._cond:
        async_turns._turns.clear()
    monkeypatch.setattr(async_turns, "_ensure_worker", lambda: None)


def test_streaming_sanitizer_hides_thinking_until_closed() -> None:
    sanitizer = StreamingReplySanitizer()
    assert sanitizer.feed("<think>geheim") == ""
    assert sanitizer.feed("</think>sicher") == "sicher"


def test_streaming_sanitizer_holds_partial_tag_across_chunks() -> None:
    sanitizer = StreamingReplySanitizer()
    assert sanitizer.feed("<th") == ""
    assert sanitizer.feed("ink>...</think>answer") == "answer"


def test_streaming_sanitizer_rejects_secret_split_across_chunks() -> None:
    secret = "sk-TEST-" + "NOT-A-REAL-CREDENTIAL"
    cut = 11
    sanitizer = StreamingReplySanitizer()
    with pytest.raises(AssistantError) as refused:
        sanitizer.feed(secret[:cut])
        sanitizer.feed(secret[cut:])
    assert refused.value.code == "assistant_secret"


def test_start_dialogue_turn_finishes_and_snapshot() -> None:
    def fake_reply(*_args: object, **_kwargs: object) -> DialogueReply:
        on_chunk = _kwargs.get("on_chunk")
        if callable(on_chunk):
            on_chunk("Teil")
        return DialogueReply(
            answer="ok",
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
        async_turns._run_turn(
            async_turns._work_queue.get(),
        )
    job = get_turn(turn_id)
    assert job is not None
    assert job.state == "done"
    assert job.partial == "Teil"
    snapshot = async_turns.turn_snapshot(job)
    assert snapshot["answer"] == "ok"
    assert snapshot["evidence_status"] == "loopback_unverified"
    assert snapshot["evidence_label"] == LOOPBACK_NOTICE


def test_stream_sse_events_receives_chunks_without_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_turns(monkeypatch)

    def slow_reply(*_args: object, **_kwargs: object) -> DialogueReply:
        on_chunk = _kwargs.get("on_chunk")
        assert callable(on_chunk)
        on_chunk("Alpha")
        time.sleep(0.15)
        on_chunk("Beta")
        return DialogueReply(
            answer="AlphaBeta",
            origin="ollama_loopback",
            evidence_status="loopback_unverified",
            data_class="local_only",
            persisted=False,
            profile_written=False,
        )

    with patch("app.assistant.async_turns.reply_to_owner", side_effect=slow_reply):
        turn_id = start_dialogue_turn(
            _fake_session_factory(),
            message="Hallo",
            data_class="local_only",
            profile_notes=None,
            history=(),
            persist=False,
        )

        def worker() -> None:
            async_turns._run_turn(async_turns._work_queue.get())

        threading.Thread(target=worker, daemon=True).start()
        events: list[str] = []

        def collect() -> None:
            events.extend(stream_sse_events(turn_id, poll_timeout_s=3.0))

        collector = threading.Thread(target=collect, daemon=True)
        collector.start()
        collector.join(timeout=4.0)
        assert not collector.is_alive()
    joined = "".join(events)
    assert "event: chunk" in joined
    assert "Alpha" in joined or "Beta" in joined
    assert "event: done" in joined


def test_unexpected_exception_marks_turn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_turns(monkeypatch)

    def boom(*_args: object, **_kwargs: object) -> DialogueReply:
        raise RuntimeError("synthetic")

    with patch("app.assistant.async_turns.reply_to_owner", side_effect=boom):
        turn_id = start_dialogue_turn(
            _fake_session_factory(),
            message="x",
            data_class="local_only",
            profile_notes=None,
            history=(),
            persist=False,
        )
        async_turns._run_turn(async_turns._work_queue.get())
    job = get_turn(turn_id)
    assert job is not None
    assert job.state == "error"
    assert job.error_code == "assistant_model_unavailable"


def test_queue_full_rejects_new_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_turns(monkeypatch)
    with async_turns._cond:
        for index in range(_MAX_QUEUED):
            async_turns._turns[f"queued-{index}"] = async_turns.DialogueTurnJob(
                turn_id=f"queued-{index}",
                state="queued",
            )
    with pytest.raises(AssistantError) as refused:
        start_dialogue_turn(
            _fake_session_factory(),
            message="x",
            data_class="local_only",
            profile_notes=None,
            history=(),
            persist=False,
        )
    assert refused.value.code == async_turns._QUEUE_FULL_CODE
    _reset_turns(monkeypatch)


def test_prune_drops_only_finished_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_turns(monkeypatch)
    with async_turns._cond:
        for index in range(70):
            async_turns._turns[f"done-{index}"] = async_turns.DialogueTurnJob(
                turn_id=f"done-{index}",
                state="done",
                created_at=time.monotonic() - 1000.0,
            )
        async_turns._turns["queued-1"] = async_turns.DialogueTurnJob(
            turn_id="queued-1",
            state="queued",
        )
        async_turns._turns["running-1"] = async_turns.DialogueTurnJob(
            turn_id="running-1",
            state="running",
        )
        async_turns._prune_finished_locked(time.monotonic())
        assert "queued-1" in async_turns._turns
        assert "running-1" in async_turns._turns
        assert len(async_turns._turns) <= async_turns._MAX_FINISHED + 2
    _reset_turns(monkeypatch)


def test_secret_chunk_is_rejected_before_stream() -> None:
    secret = "sk-TEST-" + "NOT-A-REAL-CREDENTIAL"
    sanitizer = StreamingReplySanitizer()
    with pytest.raises(AssistantError) as refused:
        sanitizer.feed(f"prefix {secret}")
    assert refused.value.code == "assistant_secret"
