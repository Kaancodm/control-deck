"""Codex text-only execution through the offline cleanroom simulator."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

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
from app.workers.contracts import AttemptContext
from app.workers.lanes.codex_protocol import CODEX_VERSION, CodexResponse, parse_codex_stream
from app.workers.sandbox import (
    SIMULATED_CODEX_IMAGE,
    CleanroomRequest,
    CleanroomSimulation,
    run_in_cleanroom,
    valid_cloud_model,
)


@dataclass(frozen=True)
class CodexSimulation:
    models: tuple[str, ...]
    cleanroom: CleanroomSimulation = field(
        default_factory=lambda: CleanroomSimulation(cli_version=CODEX_VERSION)
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.models, tuple)
            or len(self.models) > 64
            or any(not valid_cloud_model(model) for model in self.models)
            or len(set(self.models)) != len(self.models)
            or not isinstance(self.cleanroom, CleanroomSimulation)
        ):
            raise ValueError("Invalid Codex simulation")


@dataclass(frozen=True)
class CodexAvailability:
    status: Literal["ok", "unavailable"]
    reason: str
    models: tuple[str, ...] = ()


class CodexExecutor:
    """Run one bundled Fake-CLI; there is no switch or executable override for live use."""

    lane = Worker.CODEX

    def __init__(
        self,
        *,
        profile: Literal["disabled", "simulation"] = "disabled",
        simulation: CodexSimulation | None = None,
        timeout_s: float = 600,
        max_stdout_bytes: int = 4 * 1024 * 1024,
        max_stderr_bytes: int = 64 * 1024,
    ) -> None:
        if (
            profile not in {"disabled", "simulation"}
            or (profile == "simulation" and simulation is None)
            or (simulation is not None and not isinstance(simulation, CodexSimulation))
            or isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 7200
            or any(
                type(n) is not int or not 0 < n <= 16 * 1024 * 1024
                for n in (max_stdout_bytes, max_stderr_bytes)
            )
        ):
            raise ValueError("Invalid Codex execution configuration")
        self.profile = profile
        self.simulation = simulation
        self.timeout_s = timeout_s
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes

    def availability(self, models: Sequence[str]) -> CodexAvailability:
        if self.profile != "simulation":
            return CodexAvailability("unavailable", "live_profile_unavailable")
        assert self.simulation is not None
        requested = tuple(dict.fromkeys(models))
        if not requested:
            return CodexAvailability("unavailable", "model_not_configured")
        if any(not valid_cloud_model(model) for model in requested):
            return CodexAvailability("unavailable", "model_unsupported")
        if not set(requested).issubset(self.simulation.models):
            return CodexAvailability("unavailable", "model_not_available")
        if not self.simulation.cleanroom.rootless:
            return CodexAvailability("unavailable", "rootless_required")
        if self.simulation.cleanroom.cli_version != CODEX_VERSION:
            return CodexAvailability("unavailable", "cli_version_unsupported")
        return CodexAvailability("ok", "simulation_ready", requested)

    def execute(self, context: AttemptContext) -> ResultV1:
        started_at = datetime.now(UTC)
        started = time.monotonic()

        def finish(status: ResultStatus, response: CodexResponse | None = None) -> ResultV1:
            response = response or CodexResponse()
            usage = ResultUsage(wall_seconds=max(0.0, time.monotonic() - started))
            if response.error is None:
                usage.input_tokens = response.input_tokens
            if response.error is None:
                usage.output_tokens = response.output_tokens
            return ResultV1(
                schema_version="result.v1",
                task_id=context.task_id,
                attempt=context.attempt,
                worker=Worker.CODEX,
                status=status,
                output=ResultOutput(text=response.text if response.error is None else "", files=[]),
                checks=[],
                usage=usage,
                timing=ResultTiming(
                    started_at=started_at, finished_at=max(started_at, datetime.now(UTC))
                ),
                error=ResultError(code=response.error, message="Codex execution did not complete")
                if response.error
                else None,
                redaction=RedactionInfo(
                    applied=context.redaction_applied, placeholders=context.redaction_placeholders
                ),
                payload_hash=context.payload_sha256,
                log_ref=f"{context.task_id}/{context.attempt}/codex.log",
            )

        def fail(code: ErrorCode) -> ResultV1:
            return finish(ResultStatus.FAILED, CodexResponse(error=code))

        if context.lane is not Worker.CODEX:
            return fail(ErrorCode.INTERNAL)
        if self.profile != "simulation":
            return fail(ErrorCode.WORKER_UNAVAILABLE)
        assert self.simulation is not None
        try:
            if context.cancel_requested():
                return finish(ResultStatus.CANCELLED)
            payload = context.payload_text.encode("utf-8")
            if hashlib.sha256(payload).hexdigest() != context.payload_sha256:
                return fail(ErrorCode.INTERNAL)
            if context.model is None or self.availability((context.model,)).status != "ok":
                return fail(ErrorCode.WORKER_UNAVAILABLE)
            timeout = min(float(context.timeout_s), self.timeout_s)
            if context.deadline_at is not None:
                timeout = min(timeout, (context.deadline_at - datetime.now(UTC)).total_seconds())
            if timeout <= 0:
                return fail(ErrorCode.TIMEOUT)
            outcome = run_in_cleanroom(
                CleanroomRequest(Worker.CODEX, SIMULATED_CODEX_IMAGE, uuid4(), context.model),
                input_bytes=payload,
                simulation=self.simulation.cleanroom,
                timeout_s=timeout,
                cancelled=context.cancel_requested,
                max_stdout_bytes=self.max_stdout_bytes,
                max_stderr_bytes=self.max_stderr_bytes,
            )
            process = outcome.process
            if process.status == "cancelled":
                return finish(ResultStatus.CANCELLED)
            if process.status == "timeout":
                return fail(ErrorCode.TIMEOUT)
            if process.status == "spawn_failed":
                return fail(ErrorCode.WORKER_UNAVAILABLE)
            if process.status != "completed":
                return fail(ErrorCode.INTERNAL)
            response = parse_codex_stream(
                process.stdout,
                returncode=process.returncode if process.returncode is not None else -1,
            )
            return finish(
                ResultStatus.FAILED if response.error else ResultStatus.SUCCEEDED, response
            )
        except Exception:
            return fail(ErrorCode.INTERNAL)
