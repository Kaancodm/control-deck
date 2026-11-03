"""Host-owned one-attempt runner with committed control and cooperative cancellation."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.schemas import (
    ErrorCode,
    RedactionInfo,
    ResultError,
    ResultOutput,
    ResultStatus,
    ResultTiming,
    ResultUsage,
    ResultV1,
    Worker,
)
from app.db.models import Result
from app.db.queue import reap_expired_leases
from app.db.repository import result_payload_for_storage
from app.db.worker_ops import (
    TaskDeadlineElapsed,
    acknowledge_worker_cancel,
    control_attempt,
    start_worker_attempt,
    submit_worker_result,
)
from app.workers.artifacts import initialize_attempt_output, write_worker_artifact
from app.workers.contracts import AttemptContext, ControlAction, LaneExecutor, PreparedAttempt
from app.workers.preparation import claim_prepared_attempt


@dataclass(frozen=True)
class RunOutcome:
    """Safe status metadata for CLI output and callers."""

    action: str
    task_id: UUID | None = None
    attempt: int | None = None
    status: str | None = None
    error_code: str | None = None


def _host_result(
    prepared: PreparedAttempt,
    *,
    started_at: datetime,
    status: ResultStatus,
    code: ErrorCode | None = None,
) -> ResultV1:
    now = datetime.now(UTC)
    return ResultV1(
        schema_version="result.v1",
        task_id=prepared.task_id,
        attempt=prepared.attempt,
        worker=prepared.lane,
        status=status,
        output=ResultOutput(text="", files=[]),
        checks=[],
        usage=ResultUsage(wall_seconds=max(0.0, (now - started_at).total_seconds())),
        timing=ResultTiming(started_at=started_at, finished_at=now),
        error=ResultError(code=code, message="Host execution did not complete") if code else None,
        redaction=RedactionInfo(
            applied=prepared.redaction_applied,
            placeholders=prepared.redaction_placeholders,
        ),
        payload_hash=prepared.payload_sha256,
        log_ref=f"{prepared.task_id}/{prepared.attempt}/{prepared.lane.value}.log",
    )


class HostRunner:
    """Execute one committed claim using a trusted, cancellation-aware lane adapter.

    The runner never performs hidden retries. Adapters must honour the cancellation
    callback and their own transport timeout; arbitrary Python cannot be forcibly
    terminated safely by this orchestration layer.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        runs_root: Path,
        lease_ttl: timedelta = timedelta(seconds=90),
        heartbeat_interval_s: float = 5.0,
    ) -> None:
        if (
            lease_ttl.total_seconds() <= 0
            or not math.isfinite(heartbeat_interval_s)
            or heartbeat_interval_s <= 0
        ):
            raise ValueError("Runner intervals must be positive")
        if heartbeat_interval_s >= lease_ttl.total_seconds() / 2:
            raise ValueError("Heartbeat interval must be shorter than half the lease TTL")
        self.session_factory = session_factory
        self.runs_root = runs_root
        self.lease_ttl = lease_ttl
        self.heartbeat_interval_s = heartbeat_interval_s

    def _control(
        self,
        prepared: PreparedAttempt,
        *,
        heartbeat: bool = False,
        lease_expiry: list[datetime] | None = None,
    ) -> ControlAction:
        with self.session_factory() as session:
            session.execute(text("SET LOCAL lock_timeout = '2s'"))
            session.execute(text("SET LOCAL statement_timeout = '3s'"))
            decision = control_attempt(
                session,
                task_id=prepared.task_id,
                lease_id=prepared.lease_id,
                attempt=prepared.attempt,
                worker=prepared.lane,
                extend_by=self.lease_ttl if heartbeat else None,
            )
            # Normal stop outcomes are values: deadline changes commit before the
            # adapter is told to stop, unlike an exception that rolls them back.
            session.commit()
            if lease_expiry is not None and decision.expires_at is not None:
                lease_expiry[0] = decision.expires_at
            return decision.action

    def _cancel(self, prepared: PreparedAttempt) -> RunOutcome:
        with self.session_factory() as session:
            decision = acknowledge_worker_cancel(
                session,
                task_id=prepared.task_id,
                lease_id=prepared.lease_id,
                attempt=prepared.attempt,
                worker=prepared.lane,
            )
            session.commit()
        return RunOutcome(decision.action.value, prepared.task_id, prepared.attempt)

    def run_once(self, worker: Worker, executor: LaneExecutor) -> RunOutcome:
        """Claim, prepare, start and execute once, closing each DB session before execution."""
        try:
            # Sweep in a separate committed transaction so task/lease locks are
            # released before the admission lock is acquired for a new claim.
            with self.session_factory() as session:
                reap_expired_leases(session)
                session.commit()
            with self.session_factory() as session:
                prepared = claim_prepared_attempt(
                    session,
                    worker=worker,
                    lease_ttl=self.lease_ttl,
                    runs_root=self.runs_root,
                    holder="host-runner",
                )
                session.commit()
        except Exception:
            # Error text can contain SQL parameters or paths. Keep only a stable code.
            return RunOutcome("error", error_code="preparation_failed")
        if prepared is None:
            return RunOutcome("idle")
        try:
            run_dir = initialize_attempt_output(
                self.runs_root,
                task_id=prepared.task_id,
                attempt=prepared.attempt,
            )
            with self.session_factory() as session:
                decision = start_worker_attempt(
                    session,
                    task_id=prepared.task_id,
                    lease_id=prepared.lease_id,
                    attempt=prepared.attempt,
                    worker=prepared.lane,
                )
                session.commit()
            if decision.action == ControlAction.CANCEL:
                return self._cancel(prepared)
            if decision.action != ControlAction.CONTINUE:
                return RunOutcome(decision.action.value, prepared.task_id, prepared.attempt)
        except Exception:
            return RunOutcome(
                "error", prepared.task_id, prepared.attempt, error_code="start_failed"
            )

        stop_monitor = threading.Event()
        cancel_execution = threading.Event()
        monitor_outcome: list[str] = []
        callback_reason: list[str] = []
        lease_expiry = [prepared.lease_expires_at]
        started_at = datetime.now(UTC)
        started_clock = time.monotonic()

        def monitor() -> None:
            while True:
                delay = self.heartbeat_interval_s
                if prepared.deadline_at is not None:
                    remaining = (prepared.deadline_at - datetime.now(UTC)).total_seconds()
                    delay = min(delay, max(0.01, remaining))
                if stop_monitor.wait(delay):
                    return
                if time.monotonic() - started_clock >= prepared.timeout_s:
                    monitor_outcome.append("timeout")
                    cancel_execution.set()
                    return
                try:
                    action = self._control(prepared, heartbeat=True, lease_expiry=lease_expiry)
                except Exception:
                    monitor_outcome.append("control_failed")
                    cancel_execution.set()
                    return
                if action != ControlAction.CONTINUE:
                    monitor_outcome.append(action.value)
                    cancel_execution.set()
                    return

        def cancellation_requested() -> bool:
            # The callback remains bounded even when the monitor is waiting for
            # a failed database connection. Adapters must poll it while waiting.
            now = datetime.now(UTC)
            if now >= lease_expiry[0]:
                if not callback_reason:
                    callback_reason.append("lost_lease")
                cancel_execution.set()
            if time.monotonic() - started_clock >= prepared.timeout_s:
                cancel_execution.set()
            return cancel_execution.is_set()

        context = AttemptContext(
            task_id=prepared.task_id,
            attempt=prepared.attempt,
            lease_id=prepared.lease_id,
            lane=prepared.lane,
            kind=prepared.kind,
            model=prepared.model,
            timeout_s=prepared.timeout_s,
            deadline_at=prepared.deadline_at,
            run_dir=run_dir,
            payload_text=prepared.payload_text,
            payload_sha256=prepared.payload_sha256,
            redaction_applied=prepared.redaction_applied,
            redaction_placeholders=prepared.redaction_placeholders,
            cancel_requested=cancellation_requested,
        )
        thread = threading.Thread(target=monitor, name="deck-heartbeat", daemon=True)
        thread.start()
        try:
            try:
                returned = executor.execute(context)
                result = ResultV1.model_validate(result_payload_for_storage(returned))
                if (
                    bool(result.output.files)
                    or result.task_id != prepared.task_id
                    or result.attempt != prepared.attempt
                    or result.worker != prepared.lane
                    or result.payload_hash != prepared.payload_sha256
                    or result.redaction.applied != prepared.redaction_applied
                    or result.redaction.placeholders != prepared.redaction_placeholders
                ):
                    raise ValueError("Result does not identify the prepared attempt")
            except Exception:
                result = _host_result(
                    prepared,
                    started_at=started_at,
                    status=ResultStatus.FAILED,
                    code=ErrorCode.INTERNAL,
                )
        finally:
            stop_monitor.set()
            thread.join(timeout=4.0)
            if thread.is_alive():
                monitor_outcome.append("control_failed")
                cancel_execution.set()
        try:
            if monitor_outcome and monitor_outcome[0] == "control_failed":
                return RunOutcome(
                    "error", prepared.task_id, prepared.attempt, error_code="control_failed"
                )
            action = self._control(prepared)
            if action == ControlAction.CANCEL:
                return self._cancel(prepared)
            if action != ControlAction.CONTINUE:
                return RunOutcome(action.value, prepared.task_id, prepared.attempt)
            if callback_reason:
                return RunOutcome(callback_reason[0], prepared.task_id, prepared.attempt)
            if time.monotonic() - started_clock >= prepared.timeout_s:
                result = _host_result(
                    prepared,
                    started_at=started_at,
                    status=ResultStatus.FAILED,
                    code=ErrorCode.TIMEOUT,
                )
            with self.session_factory() as session:
                try:
                    task = submit_worker_result(
                        session,
                        lease_id=prepared.lease_id,
                        result_payload=result_payload_for_storage(result),
                    )
                except TaskDeadlineElapsed:
                    session.commit()
                    return RunOutcome("expired", prepared.task_id, prepared.attempt)
                stored = session.scalar(
                    select(Result.payload).where(
                        Result.task_id == prepared.task_id,
                        Result.attempt == prepared.attempt,
                    )
                )
                if stored is None:
                    raise ValueError("No authoritative result was stored")
                status = task.status.value
                encoded = json.dumps(stored, ensure_ascii=False, sort_keys=True).encode("utf-8")
                write_worker_artifact(
                    self.runs_root,
                    task_id=prepared.task_id,
                    attempt=prepared.attempt,
                    name="result.json",
                    data=encoded,
                )
                write_worker_artifact(
                    self.runs_root,
                    task_id=prepared.task_id,
                    attempt=prepared.attempt,
                    name=f"{prepared.lane.value}.log",
                    data=f"attempt={prepared.attempt} status={status}\n".encode("ascii"),
                )
                session.commit()
            return RunOutcome("completed", prepared.task_id, prepared.attempt, status)
        except Exception:
            return RunOutcome(
                "error", prepared.task_id, prepared.attempt, error_code="result_persistence_failed"
            )
