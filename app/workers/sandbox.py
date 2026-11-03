"""Validated cleanroom commands and an offline lifecycle simulator, never a live launcher."""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from app.core.schemas import Worker
from app.workers.lanes.codex_protocol import codex_command
from app.workers.process import ProcessResult, run_process

_IMAGE = re.compile(r"[a-z0-9][a-z0-9./_-]{0,200}@sha256:[a-f0-9]{64}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
SIMULATED_CLAUDE_IMAGE = "simulation/claude@sha256:" + "0" * 64
SIMULATED_CODEX_IMAGE = "simulation/codex@sha256:" + "0" * 64
CLAUDE_VERSION = "2.1.317"


def valid_cloud_model(model: object) -> bool:
    return isinstance(model, str) and bool(_MODEL.fullmatch(model))


@dataclass(frozen=True)
class CleanroomLimits:
    memory_mib: int = 2048
    cpus: int = 2
    pids: int = 64
    temporary_mib: int = 64

    def __post_init__(self) -> None:
        for value, minimum, maximum in (
            (self.memory_mib, 256, 8192),
            (self.cpus, 1, 8),
            (self.pids, 16, 256),
            (self.temporary_mib, 16, 256),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("Invalid cleanroom limits")


@dataclass(frozen=True)
class CleanroomRequest:
    lane: Worker
    image: str
    container_id: UUID
    model: str
    limits: CleanroomLimits = field(default_factory=CleanroomLimits)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.lane, Worker)
            or self.lane not in {Worker.CLAUDE, Worker.CODEX}
            or not isinstance(self.image, str)
            or not _IMAGE.fullmatch(self.image)
            or not isinstance(self.container_id, UUID)
            or not valid_cloud_model(self.model)
            or not isinstance(self.limits, CleanroomLimits)
        ):
            raise ValueError("Invalid cleanroom request")

    @property
    def container_name(self) -> str:
        return "deck-attempt-" + self.container_id.hex


def claude_command(model: str) -> tuple[str, ...]:
    """Describe the pinned CLI contract without interpreting payloads as arguments."""
    if not valid_cloud_model(model):
        raise ValueError("Invalid model identifier")
    return (
        "claude",
        "-p",
        "--restricted",
        "--safe-mode",
        "--tools",
        "",
        "--disallowedTools",
        "*",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "--model",
        model,
    )


def cleanroom_command(request: CleanroomRequest) -> tuple[str, ...]:
    """Build a closed network contract; it is not sufficient to activate a live provider.

    No caller-controlled mount, environment, executable, network or privilege flags
    exist. Payload bytes cross stdin, so there is no host payload path to race.
    """
    if not isinstance(request, CleanroomRequest):
        raise ValueError("Invalid cleanroom request")
    limits = request.limits
    profile: tuple[str, ...]
    if request.lane == Worker.CODEX:
        profile = (
            "--mount=type=volume,src=deck-codex-profile,dst=/profile",
            "--env=HOME=/tmp/home",
            "--env=CODEX_HOME=/profile",
        )
        command = codex_command(request.model)
    else:
        profile = (
            "--mount=type=volume,src=deck-claude-profile,dst=/profile",
            "--env=HOME=/tmp/home",
            "--env=CLAUDE_CONFIG_DIR=/profile",
            "--env=CLAUDE_CODE_MAX_RETRIES=0",
            "--env=DISABLE_UPDATES=1",
            "--env=CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            "--env=CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1",
            "--env=CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1",
            "--env=CLAUDE_CODE_DISABLE_FAST_MODE=1",
        )
        command = claude_command(request.model)
    return (
        "run",
        "--rm",
        "--interactive",
        "--name",
        request.container_name,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={limits.pids}",
        f"--memory={limits.memory_mib}m",
        f"--cpus={limits.cpus}",
        "--user=10001:10001",
        "--network=none",
        "--workdir=/work",
        f"--tmpfs=/work:rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size={limits.temporary_mib}m",
        f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size={limits.temporary_mib}m",
        *profile,
        request.image,
        *command,
    )


@dataclass(frozen=True)
class CleanroomSimulation:
    """Only bundled fake programs can run; this configuration cannot select Docker."""

    rootless: bool = True
    cleanup_fails: bool = False
    cli_version: str = CLAUDE_VERSION
    behavior: str = "ok"
    output_text: str = field(default="simulation-ok", repr=False)
    delay_s: float = 0

    def __post_init__(self) -> None:
        if (
            type(self.rootless) is not bool
            or type(self.cleanup_fails) is not bool
            or not isinstance(self.cli_version, str)
            or not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", self.cli_version)
            or self.behavior
            not in {
                "ok",
                "echo",
                "empty",
                "auth",
                "policy",
                "policy_400",
                "rate_limit",
                "unavailable",
                "internal",
                "delay",
                "invalid_json",
                "invalid_utf8",
                "incomplete_zero",
                "error_zero",
                "permission_denied",
                "stdout_limit",
                "stderr_limit",
                "negative_usage",
                "boolean_usage",
                "wrong_type",
                "duplicate_keys",
            }
            or not isinstance(self.output_text, str)
            or "\0" in self.output_text
            or len(self.output_text.encode("utf-8")) > 65536
            or isinstance(self.delay_s, bool)
            or not isinstance(self.delay_s, (int, float))
            or not math.isfinite(self.delay_s)
            or not 0 <= self.delay_s <= 7200
        ):
            raise ValueError("Invalid cleanroom simulation")


@dataclass(frozen=True)
class CleanroomResult:
    process: ProcessResult
    reason: str
    cleanup_completed: bool


def run_in_cleanroom(
    request: CleanroomRequest,
    *,
    input_bytes: bytes,
    simulation: CleanroomSimulation,
    timeout_s: float,
    cancelled: Callable[[], bool],
    max_stdout_bytes: int = 4 * 1024 * 1024,
    max_stderr_bytes: int = 64 * 1024,
) -> CleanroomResult:
    """Exercise preflight, one attempt and independent cleanup using fake CLIs only.

    Cleanup has its own two-second budget and ignores the task cancellation flag.
    A real daemon-side container needs this separate cleanup path; killing its
    client alone would not stop the container. Fake execution is not a sandbox.
    """
    command = cleanroom_command(request)
    if not isinstance(simulation, CleanroomSimulation):
        raise ValueError("An explicit offline simulation is required")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or not 0 < timeout_s <= 7200
        or not isinstance(input_bytes, bytes)
        or len(input_bytes) > 16 * 1024 * 1024
        or any(
            type(n) is not int or not 0 < n <= 16 * 1024 * 1024
            for n in (max_stdout_bytes, max_stderr_bytes)
        )
    ):
        raise ValueError("Invalid cleanroom execution limits")
    started = time.monotonic()
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "DECK_SIMULATION_ROOTLESS": str(int(simulation.rootless)),
        "DECK_SIMULATION_CLEANUP_FAILS": str(int(simulation.cleanup_fails)),
        "DECK_SIMULATION_CLI_VERSION": simulation.cli_version,
        "DECK_SIMULATION_BEHAVIOR": simulation.behavior,
        "DECK_SIMULATION_OUTPUT": simulation.output_text,
        "DECK_SIMULATION_DELAY": str(simulation.delay_s),
    }
    program = (sys.executable, str(Path(__file__).with_name("_fake_cleanroom_cli.py")))
    with tempfile.TemporaryDirectory(prefix="deck-cleanroom-simulation-") as temporary:

        def call(
            arguments: tuple[str, ...], payload: bytes = b"", *, cleanup: bool = False
        ) -> ProcessResult:
            budget = 2.0 if cleanup else timeout_s - (time.monotonic() - started)
            if budget <= 0:
                return ProcessResult("timeout", None, b"", b"", time.monotonic() - started)
            return run_process(
                (*program, *arguments),
                input_bytes=payload,
                cwd=Path(temporary),
                env=environment,
                timeout_s=budget,
                cancel_requested=(lambda: False) if cleanup else cancelled,
                max_stdout_bytes=max_stdout_bytes,
                max_stderr_bytes=max_stderr_bytes,
            )

        probe = call(("info", "--format", "{{json .SecurityOptions}}"))
        if probe.status != "completed" or probe.returncode != 0:
            return CleanroomResult(probe, "cleanroom_probe_failed", True)
        try:
            security_options = json.loads(probe.stdout)
            rootless = (
                isinstance(security_options, list)
                and all(isinstance(option, str) for option in security_options)
                and "name=rootless" in security_options
            )
        except (ValueError, TypeError, UnicodeError):
            rootless = False
        if not rootless:
            return CleanroomResult(
                ProcessResult("spawn_failed", None, b"", b"", 0), "rootless_required", True
            )
        try:
            outcome = call(command, input_bytes)
        except Exception:
            outcome = ProcessResult("internal", None, b"", b"", time.monotonic() - started)
        finally:
            cleanup = call(("rm", "--force", request.container_name), cleanup=True)
        cleaned = cleanup.status == "completed" and cleanup.returncode == 0
        if not cleaned:
            return CleanroomResult(
                ProcessResult("internal", None, b"", b"", time.monotonic() - started),
                "cleanroom_cleanup_failed",
                False,
            )
        return CleanroomResult(outcome, "simulation_completed", True)
