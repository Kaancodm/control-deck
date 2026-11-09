"""In-memory dialogue turn jobs for non-blocking UI (bounded concurrency)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.assistant.dialogue import DialogueReply, reply_to_owner, store_turn
from app.assistant.errors import AssistantError

TurnState = Literal["queued", "running", "done", "error"]
_MAX_TURNS = 64
_TURN_TTL_S = 600.0


@dataclass
class DialogueTurnJob:
    turn_id: str
    state: TurnState = "queued"
    answer: str | None = None
    error_code: str | None = None
    reply: DialogueReply | None = None
    partial: str = ""
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    _waiters: list[threading.Condition] = field(default_factory=list, repr=False)


_lock = threading.Lock()
_turns: dict[str, DialogueTurnJob] = {}
_worker_gate = threading.Semaphore(1)


def _prune_locked(now: float) -> None:
    stale = [
        key
        for key, job in _turns.items()
        if now - job.created_at > _TURN_TTL_S and job.state in {"done", "error"}
    ]
    for key in stale:
        _turns.pop(key, None)
    if len(_turns) > _MAX_TURNS:
        ordered = sorted(_turns.items(), key=lambda item: item[1].created_at)
        for key, _job in ordered[: len(_turns) - _MAX_TURNS]:
            _turns.pop(key, None)


def get_turn(turn_id: str) -> DialogueTurnJob | None:
    with _lock:
        return _turns.get(turn_id)


def turn_snapshot(job: DialogueTurnJob) -> dict[str, object]:
    payload: dict[str, object] = {
        "turn_id": job.turn_id,
        "state": job.state,
        "partial": job.partial,
    }
    if job.reply is not None:
        payload.update(
            {
                "answer": job.reply.answer,
                "origin": job.reply.origin,
                "evidence_status": job.reply.evidence_status,
                "data_class": job.reply.data_class,
                "persisted": job.reply.persisted,
            }
        )
    if job.error_code is not None:
        payload["error"] = job.error_code
    return payload


def start_dialogue_turn(
    session_factory: sessionmaker[Session],
    *,
    message: str,
    data_class: str,
    profile_notes: str | None,
    history: Sequence[tuple[str, str]],
    persist: bool,
) -> str:
    turn_id = str(uuid.uuid4())
    job = DialogueTurnJob(turn_id=turn_id)
    with _lock:
        _prune_locked(time.monotonic())
        _turns[turn_id] = job

    def _execute() -> None:
        _worker_gate.acquire()
        try:
            _set_state(job, "running")
            session = session_factory()
            try:

                def on_chunk(text: str) -> None:
                    with _lock:
                        job.partial += text
                        job.updated_at = time.monotonic()
                        for waiter in job._waiters:
                            waiter.notify_all()

                reply = reply_to_owner(
                    message,
                    data_class=data_class,
                    profile_notes=profile_notes,
                    history=history,
                    on_chunk=on_chunk,
                )
                if persist:
                    reply = store_turn(session, reply, owner_text=message.strip())
                    session.commit()
                else:
                    session.rollback()
                with _lock:
                    job.reply = reply
                    job.answer = reply.answer
                    job.state = "done"
                    job.updated_at = time.monotonic()
                    for waiter in job._waiters:
                        waiter.notify_all()
            except AssistantError as exc:
                session.rollback()
                with _lock:
                    job.state = "error"
                    job.error_code = exc.code
                    job.updated_at = time.monotonic()
                    for waiter in job._waiters:
                        waiter.notify_all()
            finally:
                session.close()
        finally:
            _worker_gate.release()

    threading.Thread(target=_execute, daemon=True, name="assistant-dialogue-turn").start()
    return turn_id


def _set_state(job: DialogueTurnJob, state: TurnState) -> None:
    with _lock:
        job.state = state
        job.updated_at = time.monotonic()
        for waiter in job._waiters:
            waiter.notify_all()


def stream_sse_events(
    turn_id: str,
    *,
    poll_timeout_s: float = 85.0,
) -> Iterator[str]:
    """Yield SSE frames until done, error, or timeout."""
    started = time.monotonic()
    last_partial = ""
    sent_state: TurnState | None = None
    while True:
        if time.monotonic() - started > poll_timeout_s:
            yield _format_sse("failed", {"error": "assistant_model_read_timeout"})
            return
        job = get_turn(turn_id)
        if job is None:
            yield _format_sse("failed", {"error": "assistant_input"})
            return
        with _lock:
            state = job.state
            partial = job.partial
            snapshot = turn_snapshot(job) if state in {"done", "error"} else None
        if state != sent_state:
            sent_state = state
            yield _format_sse("status", {"state": state})
        if partial != last_partial:
            delta = partial[len(last_partial) :]
            last_partial = partial
            if delta:
                yield _format_sse("chunk", {"text": delta})
        if state == "done" and snapshot is not None:
            yield _format_sse("done", snapshot)
            return
        if state == "error" and snapshot is not None:
            yield _format_sse("failed", snapshot)
            return
        waiter = threading.Condition()
        with _lock:
            job = get_turn(turn_id)
            if job is not None:
                job._waiters.append(waiter)
        try:
            with waiter:
                waiter.wait(timeout=1.0)
        finally:
            with _lock:
                job = get_turn(turn_id)
                if job is not None and waiter in job._waiters:
                    job._waiters.remove(waiter)


def _format_sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
