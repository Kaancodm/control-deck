"""Security-sensitive command constraints and simulated container recovery."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from app.core.schemas import Worker
from app.workers import sandbox
from app.workers.process import ProcessResult
from app.workers.sandbox import (
    SIMULATED_CLAUDE_IMAGE,
    CleanroomLimits,
    CleanroomRequest,
    CleanroomSimulation,
    claude_command,
    cleanroom_command,
    run_in_cleanroom,
)


@pytest.fixture
def request_spec():
    return CleanroomRequest(
        Worker.CLAUDE,
        SIMULATED_CLAUDE_IMAGE,
        UUID("00000000-0000-4000-8000-000000000001"),
        "fixture-model",
    )


def test_cleanroom_grants_only_private_temporary_storage_and_named_profile(request_spec):
    command = cleanroom_command(request_spec)
    assert command[:3] == ("run", "--rm", "--interactive")
    assert {
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=10001:10001",
        "--network=none",
    }.issubset(command)
    mounts = [arg for arg in command if arg.startswith("--mount")]
    assert mounts == ["--mount=type=volume,src=deck-claude-profile,dst=/profile"]
    assert not any(
        "type=bind" in arg or "docker.sock" in arg or "--privileged" in arg for arg in command
    )
    tmpfs = [arg for arg in command if arg.startswith("--tmpfs")]
    assert len(tmpfs) == 2 and all("noexec,nosuid,nodev" in arg for arg in tmpfs)
    assert not any(arg.startswith("--env=DECK_") for arg in command)
    assert command[-1] == "fixture-model"


def test_subscription_contract_disables_tools_without_bare_mode():
    command = claude_command("fixture-model")
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--disallowedTools") + 1] == "*"
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert command[command.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in command
    assert "--bare" not in command and "--dangerously-skip-permissions" not in command


@pytest.mark.parametrize(
    "changes",
    [
        {"image": "image:latest"},
        {"image": "image@sha256:" + "a" * 63},
        {"image": "image@sha256:" + "A" * 64},
        {"image": "--network=host"},
        {"lane": Worker.OLLAMA},
        {"lane": "claude"},
        {"container_id": "arbitrary-name"},
        {"model": "--help"},
        {"model": "model\n--tools=default"},
        {"model": "../model"},
        {"limits": {}},
    ],
)
def test_unsafe_request_configuration_is_rejected(request_spec, changes):
    with pytest.raises(ValueError, match="Invalid cleanroom request"):
        replace(request_spec, **changes)


@pytest.mark.parametrize(
    "configuration",
    [
        {"memory_mib": True},
        {"memory_mib": 255},
        {"memory_mib": 8193},
        {"cpus": 0},
        {"cpus": 1.5},
        {"pids": 257},
        {"temporary_mib": 257},
    ],
)
def test_resource_limits_are_bounded(configuration):
    with pytest.raises(ValueError, match="Invalid cleanroom limits"):
        CleanroomLimits(**configuration)


@pytest.mark.parametrize(
    "configuration",
    [
        {"rootless": 1},
        {"cleanup_fails": "yes"},
        {"cli_version": "latest"},
        {"behavior": "live"},
        {"output_text": "x" * 65537},
        {"output_text": "nul\0byte"},
        {"delay_s": -1},
        {"delay_s": float("nan")},
        {"delay_s": True},
    ],
)
def test_simulation_cannot_be_used_as_arbitrary_program_configuration(configuration):
    with pytest.raises(ValueError, match="Invalid cleanroom simulation"):
        CleanroomSimulation(**configuration)


@pytest.mark.parametrize(
    "probe_body", [b'"name=rootless"', b"{}", b'[true,"name=rootless"]', b"invalid", b"[]"]
)
def test_rootless_probe_must_be_a_valid_security_options_array(
    request_spec, monkeypatch, probe_body
):
    calls = []

    def fake(command, **kwargs):
        calls.append(command)
        return ProcessResult("completed", 0, probe_body, b"", 0)

    monkeypatch.setattr(sandbox, "run_process", fake)
    result = run_in_cleanroom(
        request_spec,
        input_bytes=b"synthetic",
        simulation=CleanroomSimulation(),
        timeout_s=1,
        cancelled=lambda: False,
    )
    assert result.reason == "rootless_required"
    assert len(calls) == 1


def test_independent_cleanup_runs_when_client_execution_raises(request_spec, monkeypatch):
    calls = []

    def fake(command, **kwargs):
        calls.append(command)
        if command[2] == "run":
            raise OSError("synthetic private failure")
        return ProcessResult("completed", 0, b'["name=rootless"]', b"", 0)

    monkeypatch.setattr(sandbox, "run_process", fake)
    result = run_in_cleanroom(
        request_spec,
        input_bytes=b"synthetic",
        simulation=CleanroomSimulation(),
        timeout_s=1,
        cancelled=lambda: False,
    )
    assert result.process.status == "internal"
    assert result.cleanup_completed
    assert [command[2] for command in calls] == ["info", "run", "rm"]
    assert "synthetic private failure" not in repr(result)


def test_real_rootless_daemon_is_never_probed(request_spec, monkeypatch):
    calls = []
    original = sandbox.run_process

    def observe(command, **kwargs):
        calls.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(sandbox, "run_process", observe)
    result = run_in_cleanroom(
        request_spec,
        input_bytes=b"synthetic",
        simulation=CleanroomSimulation(rootless=False),
        timeout_s=2,
        cancelled=lambda: False,
    )
    assert result.reason == "rootless_required"
    assert len(calls) == 1
    assert Path(calls[0][1]).name == "_fake_cleanroom_cli.py"


def test_image_recipe_is_pinned_and_final_stage_has_no_shell_or_package_manager():
    recipe = Path("docker/lane-claude/Dockerfile").read_text()
    stages = [line for line in recipe.splitlines() if line.startswith("FROM ")]
    assert len(stages) == 2 and all("@sha256:" in line for line in stages)
    assert "ADD --checksum=sha256:" in recipe
    final = recipe.split(stages[-1])[1]
    assert "USER 10001:10001" in final
    assert "COPY --from=unpack --chmod=0555 /tmp/claude" in final
    assert "RUN " not in final and "busybox" not in final
