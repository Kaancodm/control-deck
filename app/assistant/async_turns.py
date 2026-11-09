"""In-memory dialogue turn jobs for non-blocking UI (bounded concurrency)."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.assistant.dialogue import DialogueReply, reply_to_owner, store_turn
from app.assistant.errors import AssistantError

_log = logging.getLogger(__name__)

TurnState = Literal["queued", "running", "done", "error"]
_MAX_FINISHED = 64
_MAX_QUEUED = 16
_TURN_TTL_S = 600.0
_QUEUE_FULL_CODE = "assistant_model_unavailable"


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


_lock = threading.Lock()
_cond = threading.Condition(_lock)
_turns: dict[str, DialogueTurnJob] = {}
_work_queue: queue.Queue[_DialogueWorkItem | None] = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False


@dataclass(frozen=True)
class _DialogueWorkItem:
    job: DialogueTurnJob
    session_factory: sessionmaker[Session]
    message: str
    data_class: str
    profile_notes: str | None
    history: tuple[tuple[str, str], ...]
    persist: bool


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="assistant-dialogue-worker",
        )
        thread.start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        item = _work_queue.get()
        if item is None:
            return
        try:
            _run_turn(item)
        finally:
            _work_queue.task_done()


def _notify_all() -> None:
    _cond.notify_all()


def _expire_stale_queued_locked(now: float) -> None:
    for key, job in list(_turns.items()):
        if job.state == "queued" and now - job.created_at > _TURN_TTL_S:
            _turns.pop(key, None)


def _prune_finished_locked(now: float) -> None:
    stale = [
        key
        for key, job in _turns.items()
        if now - job.created_at > _TURN_TTL_S and job.state in {"done", "error"}
    ]
    for key in stale:
        _turns.pop(key, None)
    finished = [key for key, job in _turns.items() if job.state in {"done", "error"}]
    if len(finished) > _MAX_FINISHED:
        ordered = sorted(
            ((key, _turns[key]) for key in finished),
            key=lambda item: item[1].created_at,
        )
        for key, _job in ordered[: len(finished) - _MAX_FINISHED]:
            _turns.pop(key, None)


def get_turn(turn_id: str) -> DialogueTurnJob | None:
    with _cond:
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
                "evidence_label": job.reply.evidence_label,
                "data_class": job.reply.data_class,
                "persisted": job.reply.persisted,
            }
        )
    if job.error_code is not None:
        payload["error"] = job.error_code
    return payload


def _set_job_error(job: DialogueTurnJob, code: str) -> None:
    job.state = "error"
    job.error_code = code
    job.updated_at = time.monotonic()


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
    with _cond:
        now = time.monotonic()
        _expire_stale_queued_locked(now)
        _prune_finished_locked(now)
        queued = sum(1 for entry in _turns.values() if entry.state == "queued")
        if queued >= _MAX_QUEUED:
            raise AssistantError(_QUEUE_FULL_CODE)
        _turns[turn_id] = job
        _notify_all()
    _ensure_worker()
    _work_queue.put(
        _DialogueWorkItem(
            job=job,
            session_factory=session_factory,
            message=message,
            data_class=data_class,
            profile_notes=profile_notes,
            history=tuple(history),
            persist=persist,
        )
    )
    return turn_id


def _run_turn(item: _DialogueWorkItem) -> None:
    job = item.job
    with _cond:
        job.state = "running"
        job.updated_at = time.monotonic()
        _notify_all()
    session = item.session_factory()
    try:

        def on_chunk(text: str) -> None:
            with _cond:
                job.partial += text
                job.updated_at = time.monotonic()
                _notify_all()

        reply = reply_to_owner(
            item.message,
            data_class=item.data_class,
            profile_notes=item.profile_notes,
            history=item.history,
            on_chunk=on_chunk,
        )
        if item.persist:
            reply = store_turn(session, reply, owner_text=item.message.strip())
            session.commit()
        else:
            session.rollback()
        with _cond:
            job.reply = reply
            job.answer = reply.answer
            job.state = "done"
            job.updated_at = time.monotonic()
            _notify_all()
    except AssistantError as exc:
        session.rollback()
        with _cond:
            _set_job_error(job, exc.code)
            _notify_all()
    except Exception:
        session.rollback()
        _log.exception("assistant_dialogue_turn_failed")
        with _cond:
            _set_job_error(job, "assistant_model_unavailable")
            _notify_all()
    finally:
        session.close()


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
        with _cond:
            job = _turns.get(turn_id)
            if job is None:
                yield _format_sse("failed", {"error": "assistant_input"})
                return
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
        with _cond:
            if not _cond.wait(timeout=1.0):
                continue


def _format_sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
