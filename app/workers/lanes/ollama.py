"""Bounded offline simulation and explicitly configured loopback HTTP execution."""

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
    ResultCheck,
    ResultError,
    ResultOutput,
    ResultStatus,
    ResultTiming,
    ResultUsage,
    ResultV1,
    Worker,
)
from app.workers.contracts import AttemptContext
from app.workers.lanes.ollama_http import OllamaHTTPClient, OllamaHTTPError
from app.workers.process import ProcessResult, run_process

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
SUPPORTED_OLLAMA_VERSION = "0.40.14"
ADAPTER_VERSION = "1"
_NUM_CTX = 4096
_CONTEXT_RESERVE = 1024
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
    """Use a bounded text-only transport; simulation always stays offline."""

    lane = Worker.OLLAMA

    def __init__(
        self,
        *,
        profile: Literal["disabled", "simulation", "live"] = "disabled",
        url: str | None = None,
        simulation: OllamaSimulation | None = None,
        timeout_s: float = 600,
        max_stdout_bytes: int = 4 * 1024 * 1024,
        max_stderr_bytes: int = 64 * 1024,
    ) -> None:
        if (
            profile not in {"disabled", "simulation", "live"}
            or (profile == "live" and (url is None or simulation is not None))
            or (profile != "live" and url is not None)
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
        self.client = (
            OllamaHTTPClient(url, max_output_bytes=max_stdout_bytes)
            if profile == "live" and url is not None
            else None
        )
        self.last_metadata: dict[str, object] = {}
        self._ollama_version: str | None = None

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
        """Probe inventory outside a database transaction, with a bounded total budget."""
        return self._availability(
            models, timeout_s=min(5.0, self.timeout_s), cancelled=lambda: False
        )

    def _availability(
        self, models: Sequence[str], *, timeout_s: float, cancelled: Callable[[], bool]
    ) -> OllamaAvailability:
        if self.profile == "live":
            return self._live_availability(models, timeout_s=timeout_s, cancelled=cancelled)
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

    def _live_availability(
        self, models: Sequence[str], *, timeout_s: float, cancelled: Callable[[], bool]
    ) -> OllamaAvailability:
        assert self.client is not None
        requested = tuple(dict.fromkeys(models))
        self._ollama_version = None
        if not requested:
            return OllamaAvailability("unavailable", "model_not_configured")
        if any(
            not isinstance(model, str)
            or not _supported_model(model)
            or model.count(":") != 1
            or not all(model.split(":"))
            or "-cloud" in model.lower()
            or model.lower().endswith(":local")
            for model in requested
        ):
            return OllamaAvailability("unavailable", "model_unsupported")
        deadline = time.monotonic() + timeout_s

        def budget() -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OllamaHTTPError("timeout")
            return remaining

        try:
            version = self.client.version(timeout_s=budget(), cancelled=cancelled)
            if version.get("version") != SUPPORTED_OLLAMA_VERSION:
                return OllamaAvailability("unavailable", "version_unsupported")
            self._ollama_version = SUPPORTED_OLLAMA_VERSION
            inventory = self.client.tags(timeout_s=budget(), cancelled=cancelled)
            entries = inventory.get("models")
            if not isinstance(entries, list) or any(not isinstance(v, dict) for v in entries):
                return OllamaAvailability("unavailable", "inventory_invalid")
            installed = {
                entry.get("name") for entry in entries if isinstance(entry.get("name"), str)
            }
            if not set(requested).issubset(installed):
                return OllamaAvailability("unavailable", "model_not_available")
            for model in requested:
                info = self.client.show(f"{model}:local", timeout_s=budget(), cancelled=cancelled)
                if info.get("remote_host") or info.get("remote_model"):
                    return OllamaAvailability("unavailable", "model_unsupported")
                capabilities = info.get("capabilities")
                if (
                    not isinstance(capabilities, list)
                    or not all(isinstance(value, str) for value in capabilities)
                    or "completion" not in capabilities
                    or any(
                        value not in {"completion", "thinking", "tools"} for value in capabilities
                    )
                ):
                    return OllamaAvailability("unavailable", "model_capability_unsupported")
        except OllamaHTTPError as exc:
            return OllamaAvailability("unavailable", f"inventory_{exc.reason}")
        return OllamaAvailability("ok", "live_ready", requested)

    def execute(self, context: AttemptContext) -> ResultV1:
        """Send exactly the archived text once; discard incomplete or unsafe output."""
        started_at = datetime.now(UTC)
        started = time.monotonic()
        self.last_metadata = {}
        if self.profile == "live":
            self.last_metadata = {
                "transport": "ollama_http",
                "adapter_version": ADAPTER_VERSION,
                "payload_sha256": context.payload_sha256,
                "log_ref": f"{context.task_id}/{context.attempt}/ollama.log",
            }
        input_tokens: int | None = None
        output_tokens: int | None = None

        def finish(
            status: ResultStatus,
            *,
            code: ErrorCode | None = None,
            output: str = "",
            checks: list[ResultCheck] | None = None,
        ) -> ResultV1:
            finished_at = max(started_at, datetime.now(UTC))
            usage = ResultUsage(wall_seconds=max(0.0, time.monotonic() - started))
            usage.input_tokens = input_tokens
            usage.output_tokens = output_tokens
            if self.profile == "live":
                self.last_metadata["duration_ms"] = int(usage.wall_seconds * 1000)
            return ResultV1(
                schema_version="result.v1",
                task_id=context.task_id,
                attempt=context.attempt,
                worker=Worker.OLLAMA,
                status=status,
                output=ResultOutput(text=output, files=[]),
                checks=checks or [],
                usage=usage,
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
        if self.profile not in {"simulation", "live"}:
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
        if self.profile == "live":
            assert self.client is not None
            self.last_metadata.update(
                {"model": context.model, "ollama_version": self._ollama_version}
            )
            # A conservative byte estimate reserves space for template and output.
            # Exact tokenizer/template accounting still requires owner validation.
            if len(payload) + _CONTEXT_RESERVE > _NUM_CTX:
                return finish(
                    ResultStatus.FAILED,
                    code=ErrorCode.ACCEPTANCE_FAILED,
                    checks=[
                        ResultCheck(
                            type="context_limit_exceeded",
                            passed=False,
                            detail="Payload exceeds the conservative context budget.",
                        )
                    ],
                )
            try:
                response = self.client.chat(
                    model=f"{context.model}:local",
                    payload_text=context.payload_text,
                    timeout_s=timeout,
                    cancelled=context.cancel_requested,
                    format_json=context.json_format,
                    num_ctx=_NUM_CTX,
                    keep_alive="1m",
                )
            except OllamaHTTPError as exc:
                if exc.status_code is not None:
                    self.last_metadata["http_status"] = exc.status_code
                if exc.reason == "cancelled":
                    return finish(ResultStatus.CANCELLED)
                error = {
                    "timeout": ErrorCode.TIMEOUT,
                    "unavailable": ErrorCode.WORKER_UNAVAILABLE,
                    "internal": ErrorCode.INTERNAL,
                }[exc.reason]
                return finish(ResultStatus.FAILED, code=error)
            input_tokens, output_tokens = response.prompt_eval_count, response.eval_count
            self.last_metadata.update(
                {
                    "http_status": response.status_code,
                    "done_reason": response.done_reason,
                    "output_sha256": response.output_sha256,
                }
            )
            if input_tokens is not None:
                self.last_metadata["prompt_eval_count"] = input_tokens
            if output_tokens is not None:
                self.last_metadata["eval_count"] = output_tokens
            if response.done_reason == "length":
                return finish(
                    ResultStatus.FAILED,
                    code=ErrorCode.ACCEPTANCE_FAILED,
                    checks=[
                        ResultCheck(
                            type="completion",
                            passed=False,
                            detail="Generation stopped at the output limit.",
                        )
                    ],
                )
            if not response.content.strip():
                return finish(ResultStatus.FAILED, code=ErrorCode.ACCEPTANCE_FAILED)
            return finish(ResultStatus.SUCCEEDED, output=response.content)
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
