#!/usr/bin/env python3
"""Submit an explicit local canary or inspect an existing canary without writes."""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import time
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.schemas import ResultStatus, ResultV1, TaskV1, Worker
from app.db.models import Event, LaneStatus, Result, Task
from app.db.repository import approve_task, create_task_from_v1
from app.db.session import get_session_factory
from app.db.state_machine import TaskState
from app.workers.live_config import load_live_config

CANARY_TEXT = "CANARY-OK"
_MAX_LOG_BYTES = 65536


def canary_task(task_id: UUID) -> TaskV1:
    """Build a synthetic local-only task; it cannot select a cloud fallback."""
    return TaskV1.model_validate(
        {
            "schema_version": "task.v1",
            "task_id": str(task_id),
            "parent_task_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "requested_by": "system",
            "kind": "custom",
            "title": "Live adapter canary",
            "instructions": "Antworte ausschliesslich mit dem Wort CANARY-OK.",
            "inputs": [{"name": "canary", "type": "text", "value": "Local adapter check."}],
            "context_refs": [],
            "data_class": "local_only",
            "policy": {
                "allowed_workers": ["ollama"],
                "forbidden_workers": ["claude", "codex"],
                "require_review": True,
                "max_attempts": 1,
                "timeout_s": 300,
            },
            "acceptance": [{"type": "must_contain", "value": CANARY_TEXT}],
            "fallback_chain": ["ollama"],
            "labels": {"purpose": "live_canary"},
        }
    )


def _read_log(root: Path, task_id: UUID, attempt: int) -> bytes:
    """Read a bounded regular artifact through directory descriptors without symlinks."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root = root.absolute()
    if ".." in root.parts or not 2 <= len(root.parts) <= 64 or attempt < 1:
        raise ValueError("Invalid artifact")
    with ExitStack() as stack:
        descriptor = os.open(root.anchor, directory_flags)
        stack.callback(os.close, descriptor)
        for component in (*root.parts[1:], str(task_id), str(attempt)):
            descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        descriptor = os.open(
            "ollama.log",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=descriptor,
        )
        stack.callback(os.close, descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= _MAX_LOG_BYTES:
            raise ValueError("Invalid artifact")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(_MAX_LOG_BYTES + 1)
        if len(data) != metadata.st_size:
            raise ValueError("Invalid artifact")
        return data


def check_canary(
    factory: Callable[[], Session],
    *,
    task_id: UUID,
    runs_root: Path,
    minimum_completed: int = 1,
) -> bool | None:
    """Return None while pending; a completed simulation always fails the live check."""
    with factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        task = session.get(Task, task_id)
        if task is None:
            return False
        expected = canary_task(task_id).model_dump(mode="json")
        if any(
            task.payload.get(key) != expected[key]
            for key in (
                "instructions",
                "inputs",
                "data_class",
                "policy",
                "acceptance",
                "fallback_chain",
                "labels",
            )
        ):
            return False
        if task.status in {
            TaskState.AWAITING_APPROVAL,
            TaskState.QUEUED,
            TaskState.LEASED,
            TaskState.RUNNING,
        }:
            return None
        if task.status != TaskState.REVIEW or task.attempt != 1:
            return False
        result = session.scalar(
            select(Result).where(Result.task_id == task_id, Result.attempt == 1)
        )
        lane = session.get(LaneStatus, Worker.OLLAMA.value)
        event = session.scalar(
            select(Event).where(
                Event.task_id == task_id, Event.attempt == 1, Event.event_type == "task.succeed"
            )
        )
        if result is None or lane is None or event is None or result.execution_profile != "live":
            return False
        payload = ResultV1.model_validate(result.payload)
        acceptance = [check for check in payload.checks if check.type != "execution_profile"]
        if (
            payload.status != ResultStatus.SUCCEEDED
            or payload.worker != Worker.OLLAMA
            or CANARY_TEXT not in payload.output.text
            or not acceptance
            or not all(check.passed for check in acceptance)
            or not any(
                check.type == "execution_profile" and check.passed and check.detail == "live"
                for check in payload.checks
            )
            or event.detail.get("execution_profile") != "live"
            or lane.reason != "live_ready"
            or lane.status != "ok"
            or lane.completed_attempts < minimum_completed
            or lane.last_attempt_success_at is None
            or lane.last_attempt_success_at < payload.timing.started_at
        ):
            return False
        log = _read_log(runs_root, task_id, 1)
        if CANARY_TEXT.encode() in log or payload.output.text.encode() in log:
            return False
        records = [json.loads(line) for line in log.splitlines()]
        if not records or not all(isinstance(record, dict) for record in records):
            return False
        return any(record.get("execution_profile") == "live" for record in records)


def run_canary(
    factory: Callable[[], Session],
    *,
    task_id: UUID,
    runs_root: Path,
    submit: bool = False,
    timeout_s: float = 330,
    poll_s: float = 0.25,
) -> bool:
    """Only --submit creates and approves; no review is accepted or result rewritten."""
    if not all(math.isfinite(value) and value > 0 for value in (timeout_s, poll_s)):
        return False
    minimum_completed = 1
    if submit:
        load_live_config(Worker.OLLAMA)
        with factory() as session:
            if session.get(Task, task_id) is not None:
                return False
            lane = session.get(LaneStatus, Worker.OLLAMA.value)
            minimum_completed = (lane.completed_attempts if lane is not None else 0) + 1
            task = create_task_from_v1(session, canary_task(task_id))
            if task.status != TaskState.AWAITING_APPROVAL:
                session.rollback()
                return False
            approve_task(session, task)
            if task.status != TaskState.QUEUED:
                session.rollback()
                return False
            session.commit()
    deadline = time.monotonic() + timeout_s
    while True:
        result = check_canary(
            factory, task_id=task_id, runs_root=runs_root, minimum_completed=minimum_completed
        )
        if result is not None:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_s, remaining))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or explicitly submit a live canary")
    parser.add_argument("--task-id", type=UUID)
    parser.add_argument(
        "--submit", action="store_true", help="Create and approve one synthetic task"
    )
    parser.add_argument("--timeout", type=float, default=330)
    args = parser.parse_args(argv)
    passed = False
    try:
        if args.submit or args.task_id is not None:
            passed = run_canary(
                get_session_factory(),
                task_id=args.task_id or uuid4(),
                runs_root=Path(os.environ.get("DECK_RUNS_DIR", "runs")),
                submit=args.submit,
                timeout_s=args.timeout,
            )
    except Exception:
        # SQL/provider/path errors may contain sensitive material. Print only the verdict.
        passed = False
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
