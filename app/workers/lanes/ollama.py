"""Bounded Ollama CLI simulation; the unverified live CLI profile stays unavailable."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
from app.workers.process import ProcessResult, run_process

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_BEHAVIORS = frozenset(
    {
        "ok",
        "echo",
        "reference",
        "empty",
        "fail",
        "delay",
        "invalid_utf8",
        "stdout_limit",
        "stderr_limit",
        "incomplete_zero",
    }
)


def _supported_model(model: str) -> bool:
    return (
        bool(_MODEL.fullmatch(model))
        and ":cloud" not in model.lower()
        and not model.lower().endswith("-cloud")
    )


@dataclass(frozen=True)
class OllamaSimulation:
    """Explicit offline fixture settings; no executable or live transport can be selected."""

    models: tuple[str, ...]
    behavior: str = "ok"
    output_text: str = field(default="simulation-ok", repr=False)
    delay_s: float = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.models, tuple)
            or len(self.models) > 64
            or any(
                not isinstance(model, str) or not _supported_model(model) for model in self.models
            )
            or len(set(self.models)) != len(self.models)
            or self.behavior not in _BEHAVIORS
            or not isinstance(self.output_text, str)
            or "\0" in self.output_text
            or len(self.output_text.encode("utf-8")) > 65536
            or isinstance(self.delay_s, bool)
            or not isinstance(self.delay_s, (int, float))
            or not math.isfinite(self.delay_s)
            or not 0 <= self.delay_s <= 7200
        ):
            raise ValueError("Invalid Ollama simulation configuration")


@dataclass(frozen=True)
class OllamaAvailability:
    status: Literal["ok", "unavailable"]
    reason: str
    models: tuple[str, ...] = ()


class OllamaExecutor:
    """Run only the bundled fake CLI; unsupported real execution cannot be enabled.

    Official ``ollama run`` may pull a missing model, use daemon-side cloud aliases,
    and read prompt-mentioned files for multimodal models. Inventory checks or a
    client environment variable do not establish the required live boundaries.
    """

    lane = Worker.OLLAMA

    def __init__(
        self,
        *,
        profile: Literal["disabled", "simulation"] = "disabled",
        simulation: OllamaSimulation | None = None,
        timeout_s: float = 600,
        max_stdout_bytes: int = 4 * 1024 * 1024,
        max_stderr_bytes: int = 64 * 1024,
    ) -> None:
        if (
            profile not in {"disabled", "simulation"}
            or (profile == "simulation" and simulation is None)
            or (simulation is not None and not isinstance(simulation, OllamaSimulation))
            or isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 7200
            or any(
                type(limit) is not int or not 0 < limit <= 16 * 1024 * 1024
                for limit in (max_stdout_bytes, max_stderr_bytes)
            )
        ):
            raise ValueError("Invalid Ollama execution configuration")
        self.profile = profile
        self.simulation = simulation
        self.timeout_s = timeout_s
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes
        self.last_metadata: dict[str, object] = {}

    def _call(
        self,
        arguments: Sequence[str],
        *,
        payload: bytes = b"",
        cwd: Path,
        timeout_s: float,
        cancelled: Callable[[], bool],
    ) -> ProcessResult:
        assert self.profile == "simulation" and self.simulation is not None
        simulation = self.simulation
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DECK_SIMULATION_MODELS": json.dumps(simulation.models),
            "DECK_SIMULATION_BEHAVIOR": simulation.behavior,
            "DECK_SIMULATION_OUTPUT": simulation.output_text,
            "DECK_SIMULATION_DELAY": str(simulation.delay_s),
        }
        return run_process(
            (sys.executable, str(Path(__file__).with_name("_fake_ollama_cli.py")), *arguments),
            input_bytes=payload,
            cwd=cwd,
            env=environment,
            timeout_s=timeout_s,
            cancel_requested=cancelled,
            max_stdout_bytes=self.max_stdout_bytes,
            max_stderr_bytes=self.max_stderr_bytes,
        )

    def availability(self, models: Sequence[str]) -> OllamaAvailability:
        """Probe only the fake inventory; no DB transaction or live process is required."""
        return self._availability(
            models, timeout_s=min(5.0, self.timeout_s), cancelled=lambda: False
        )

    def _availability(
        self, models: Sequence[str], *, timeout_s: float, cancelled: Callable[[], bool]
    ) -> OllamaAvailability:
        if self.profile != "simulation":
            return OllamaAvailability("unavailable", "live_profile_unavailable")
        requested = tuple(dict.fromkeys(models))
        if not requested:
            return OllamaAvailability("unavailable", "model_not_configured")
        if any(not isinstance(model, str) or not _supported_model(model) for model in requested):
            return OllamaAvailability("unavailable", "model_unsupported")
        outcome = self._call(
            ("list",),
            cwd=Path(__file__).parent,
            timeout_s=timeout_s,
            cancelled=cancelled,
        )
        if outcome.status in {"cancelled", "timeout", "internal"}:
            return OllamaAvailability("unavailable", f"inventory_{outcome.status}")
        if outcome.status != "completed" or outcome.returncode != 0:
            return OllamaAvailability("unavailable", "inventory_unavailable")
        try:
            lines = outcome.stdout.decode("utf-8").splitlines()
            if not lines or lines[0].split() != ["NAME", "ID", "SIZE", "MODIFIED"]:
                raise ValueError("Invalid simulated inventory")
            installed = {line.split()[0] for line in lines[1:] if line.split()}
        except (UnicodeError, ValueError):
            return OllamaAvailability("unavailable", "inventory_invalid")
        if not set(requested).issubset(installed):
            return OllamaAvailability("unavailable", "model_not_available")
        return OllamaAvailability("ok", "simulation_ready", requested)

    def execute(self, context: AttemptContext) -> ResultV1:
        """Send exactly the archived payload to a single simulated run invocation."""
        started_at = datetime.now(UTC)
        started = time.monotonic()

        def finish(
            status: ResultStatus, *, code: ErrorCode | None = None, output: str = ""
        ) -> ResultV1:
            finished_at = max(started_at, datetime.now(UTC))
            return ResultV1(
                schema_version="result.v1",
                task_id=context.task_id,
                attempt=context.attempt,
                worker=Worker.OLLAMA,
                status=status,
                output=ResultOutput(text=output, files=[]),
                checks=[],
                usage=ResultUsage(wall_seconds=max(0.0, time.monotonic() - started)),
                timing=ResultTiming(started_at=started_at, finished_at=finished_at),
                error=ResultError(code=code, message="Ollama execution did not complete")
                if code
                else None,
                redaction=RedactionInfo(
                    applied=context.redaction_applied, placeholders=context.redaction_placeholders
                ),
                payload_hash=context.payload_sha256,
                log_ref=f"{context.task_id}/{context.attempt}/ollama.log",
            )

        if context.lane != Worker.OLLAMA:
            return finish(ResultStatus.FAILED, code=ErrorCode.INTERNAL)
        if self.profile != "simulation":
            return finish(ResultStatus.FAILED, code=ErrorCode.WORKER_UNAVAILABLE)
        try:
            if context.cancel_requested():
                return finish(ResultStatus.CANCELLED)
            payload = context.payload_text.encode("utf-8")
        except Exception:
            return finish(ResultStatus.FAILED, code=ErrorCode.INTERNAL)
        if hashlib.sha256(payload).hexdigest() != context.payload_sha256:
            return finish(ResultStatus.FAILED, code=ErrorCode.INTERNAL)
        if context.model is None:
            return finish(ResultStatus.FAILED, code=ErrorCode.WORKER_UNAVAILABLE)

        def remaining_budget() -> float:
            remaining = min(float(context.timeout_s), self.timeout_s) - (time.monotonic() - started)
            if context.deadline_at is not None:
                remaining = min(
                    remaining, (context.deadline_at - datetime.now(UTC)).total_seconds()
                )
            return remaining

        timeout = remaining_budget()
        if timeout <= 0:
            return finish(ResultStatus.FAILED, code=ErrorCode.TIMEOUT)
        available = self._availability(
            (context.model,), timeout_s=min(5.0, timeout), cancelled=context.cancel_requested
        )
        if available.reason == "inventory_cancelled":
            return finish(ResultStatus.CANCELLED)
        if available.reason == "inventory_timeout":
            return finish(ResultStatus.FAILED, code=ErrorCode.TIMEOUT)
        if available.reason == "inventory_internal":
            return finish(ResultStatus.FAILED, code=ErrorCode.INTERNAL)
        if available.status != "ok":
            return finish(ResultStatus.FAILED, code=ErrorCode.WORKER_UNAVAILABLE)
        timeout = remaining_budget()
        if timeout <= 0:
            return finish(ResultStatus.FAILED, code=ErrorCode.TIMEOUT)
        outcome = self._call(
            ("run", context.model),
            payload=payload,
            cwd=context.run_dir,
            timeout_s=timeout,
            cancelled=context.cancel_requested,
        )
        if outcome.status == "cancelled":
            return finish(ResultStatus.CANCELLED)
        if outcome.status == "timeout":
            return finish(ResultStatus.FAILED, code=ErrorCode.TIMEOUT)
        if outcome.status == "spawn_failed":
            return finish(ResultStatus.FAILED, code=ErrorCode.WORKER_UNAVAILABLE)
        if outcome.status != "completed":
            return finish(ResultStatus.FAILED, code=ErrorCode.INTERNAL)
        if outcome.returncode != 0:
            return finish(ResultStatus.FAILED, code=ErrorCode.WORKER_UNAVAILABLE)
        try:
            output = outcome.stdout.decode("utf-8")
        except UnicodeError:
            return finish(ResultStatus.FAILED, code=ErrorCode.INTERNAL)
        if not output.strip():
            return finish(ResultStatus.FAILED, code=ErrorCode.ACCEPTANCE_FAILED)
        return finish(ResultStatus.SUCCEEDED, output=output)
