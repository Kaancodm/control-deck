"""Versioned Claude CLI result handling with explicit offline execution only."""

from __future__ import annotations

import hashlib
import json
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
from app.workers.sandbox import (
    CLAUDE_VERSION,
    SIMULATED_CLAUDE_IMAGE,
    CleanroomRequest,
    CleanroomSimulation,
    run_in_cleanroom,
    valid_cloud_model,
)


@dataclass(frozen=True)
class ClaudeSimulation:
    models: tuple[str, ...]
    cleanroom: CleanroomSimulation = field(default_factory=CleanroomSimulation)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.models, tuple)
            or len(self.models) > 64
            or any(not valid_cloud_model(model) for model in self.models)
            or len(set(self.models)) != len(self.models)
            or not isinstance(self.cleanroom, CleanroomSimulation)
        ):
            raise ValueError("Invalid Claude simulation")


@dataclass(frozen=True)
class ClaudeAvailability:
    status: Literal["ok", "unavailable"]
    reason: str
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaudeResponse:
    text: str = field(default="", repr=False)
    error: ErrorCode | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_claude_response(raw: bytes, returncode: int | None) -> ClaudeResponse:
    """Accept only complete final responses; errors never contain provider text."""
    try:
        message = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            not isinstance(message, dict)
            or message.get("type") != "result"
            or type(message.get("is_error")) is not bool
            or message.get("permission_denials", []) != []
        ):
            return ClaudeResponse(error=ErrorCode.INTERNAL)
        if message["is_error"]:
            status = message.get("api_error_status")
            if type(status) is int:
                errors = {
                    400: ErrorCode.POLICY_BLOCKED,
                    401: ErrorCode.AUTH_REQUIRED,
                    403: ErrorCode.POLICY_BLOCKED,
                    429: ErrorCode.RATE_LIMITED,
                    500: ErrorCode.WORKER_UNAVAILABLE,
                    502: ErrorCode.WORKER_UNAVAILABLE,
                    503: ErrorCode.WORKER_UNAVAILABLE,
                    529: ErrorCode.WORKER_UNAVAILABLE,
                }
                return ClaudeResponse(error=errors.get(status, ErrorCode.INTERNAL))
            return ClaudeResponse(error=ErrorCode.INTERNAL)
        if (
            returncode != 0
            or message.get("subtype") != "success"
            or message.get("terminal_reason") != "completed"
            or type(message.get("num_turns")) is not int
            or message["num_turns"] != 1
            or "tool_use" in message
            or not isinstance(message.get("result"), str)
        ):
            return ClaudeResponse(error=ErrorCode.INTERNAL)
        output = message["result"]
        if not output.strip():
            return ClaudeResponse(error=ErrorCode.ACCEPTANCE_FAILED)
        usage = message.get("usage", {})
        if not isinstance(usage, dict):
            return ClaudeResponse(error=ErrorCode.INTERNAL)
        counts: list[int | None] = []
        for name in ("input_tokens", "output_tokens"):
            count = usage.get(name)
            if name in usage and (type(count) is not int or not 0 <= count <= 2**53 - 1):
                return ClaudeResponse(error=ErrorCode.INTERNAL)
            counts.append(count)
        return ClaudeResponse(output, input_tokens=counts[0], output_tokens=counts[1])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return ClaudeResponse(error=ErrorCode.INTERNAL)


class ClaudeExecutor:
    """Run one bundled Fake-CLI; there is no switch or executable override for live use."""

    lane = Worker.CLAUDE

    def __init__(
        self,
        *,
        profile: Literal["disabled", "simulation"] = "disabled",
        simulation: ClaudeSimulation | None = None,
        timeout_s: float = 600,
        max_stdout_bytes: int = 4 * 1024 * 1024,
        max_stderr_bytes: int = 64 * 1024,
    ) -> None:
        if (
            profile not in {"disabled", "simulation"}
            or (profile == "simulation" and simulation is None)
            or (simulation is not None and not isinstance(simulation, ClaudeSimulation))
            or isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 7200
            or any(
                type(n) is not int or not 0 < n <= 16 * 1024 * 1024
                for n in (max_stdout_bytes, max_stderr_bytes)
            )
        ):
            raise ValueError("Invalid Claude execution configuration")
        self.profile = profile
        self.simulation = simulation
        self.timeout_s = timeout_s
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes
        self.last_metadata: dict[str, object] = {}

    def availability(self, models: Sequence[str]) -> ClaudeAvailability:
        if self.profile != "simulation":
            return ClaudeAvailability("unavailable", "live_profile_unavailable")
        assert self.simulation is not None
        requested = tuple(dict.fromkeys(models))
        if not requested:
            return ClaudeAvailability("unavailable", "model_not_configured")
        if any(not valid_cloud_model(model) for model in requested):
            return ClaudeAvailability("unavailable", "model_unsupported")
        if not set(requested).issubset(self.simulation.models):
            return ClaudeAvailability("unavailable", "model_not_available")
        if not self.simulation.cleanroom.rootless:
            return ClaudeAvailability("unavailable", "rootless_required")
        if self.simulation.cleanroom.cli_version != CLAUDE_VERSION:
            return ClaudeAvailability("unavailable", "cli_version_unsupported")
        return ClaudeAvailability("ok", "simulation_ready", requested)

    def execute(self, context: AttemptContext) -> ResultV1:
        started_at = datetime.now(UTC)
        started = time.monotonic()

        def finish(status: ResultStatus, response: ClaudeResponse | None = None) -> ResultV1:
            response = response or ClaudeResponse()
            usage = ResultUsage(wall_seconds=max(0.0, time.monotonic() - started))
            if response.input_tokens is not None:
                usage.input_tokens = response.input_tokens
            if response.output_tokens is not None:
                usage.output_tokens = response.output_tokens
            return ResultV1(
                schema_version="result.v1",
                task_id=context.task_id,
                attempt=context.attempt,
                worker=Worker.CLAUDE,
                status=status,
                output=ResultOutput(text=response.text if response.error is None else "", files=[]),
                checks=[],
                usage=usage,
                timing=ResultTiming(
                    started_at=started_at, finished_at=max(started_at, datetime.now(UTC))
                ),
                error=ResultError(code=response.error, message="Claude execution did not complete")
                if response.error
                else None,
                redaction=RedactionInfo(
                    applied=context.redaction_applied, placeholders=context.redaction_placeholders
                ),
                payload_hash=context.payload_sha256,
                log_ref=f"{context.task_id}/{context.attempt}/claude.log",
            )

        def fail(code: ErrorCode) -> ResultV1:
            return finish(ResultStatus.FAILED, ClaudeResponse(error=code))

        if context.lane is not Worker.CLAUDE:
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
                CleanroomRequest(Worker.CLAUDE, SIMULATED_CLAUDE_IMAGE, uuid4(), context.model),
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
            response = parse_claude_response(process.stdout, process.returncode)
            return finish(
                ResultStatus.FAILED if response.error else ResultStatus.SUCCEEDED, response
            )
        except Exception:
            return fail(ErrorCode.INTERNAL)
