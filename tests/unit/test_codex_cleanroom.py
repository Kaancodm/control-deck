"""Codex profile separation and shared cleanup with the offline container CLI."""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.core.schemas import Worker
from app.workers.sandbox import (
    SIMULATED_CLAUDE_IMAGE,
    SIMULATED_CODEX_IMAGE,
    CleanroomRequest,
    CleanroomSimulation,
    cleanroom_command,
    run_in_cleanroom,
)


def test_codex_uses_dedicated_profile_and_closed_network_contract():
    request = CleanroomRequest(Worker.CODEX, SIMULATED_CODEX_IMAGE, uuid4(), "fixture-model")
    command = cleanroom_command(request)
    assert "--network=none" in command
    assert "--env=HOME=/tmp/home" in command
    assert not any("sudo" in part for part in command)
    assert "--mount=type=volume,src=deck-codex-profile,dst=/profile" in command
    assert "--env=CODEX_HOME=/profile" in command
    assert "--read-only" in command
    assert "--user=10001:10001" in command
    assert not any("claude" in arg.lower() for arg in command)
    assert command[-1] == "-"
    assert len(command) + 2 <= 64
    claude = cleanroom_command(replace(request, lane=Worker.CLAUDE, image=SIMULATED_CLAUDE_IMAGE))
    assert "--mount=type=volume,src=deck-claude-profile,dst=/profile" in claude
    assert "--env=HOME=/tmp/home" in claude
    assert not any("sudo" in part for part in claude)
    assert not any("codex" in arg.lower() for arg in claude)


@pytest.mark.parametrize("model", ["fixture-model", "claude", "codex"])
def test_dispatcher_selects_provider_before_model_argument(model):
    outcome = run_in_cleanroom(
        CleanroomRequest(Worker.CODEX, SIMULATED_CODEX_IMAGE, uuid4(), model),
        input_bytes=b"synthetic payload",
        simulation=CleanroomSimulation(cli_version="0.166.0"),
        timeout_s=5,
        cancelled=lambda: False,
    )
    assert outcome.process.status == "completed"
    assert outcome.process.returncode == 0
    assert b'"thread.started"' in outcome.process.stdout
    assert outcome.cleanup_completed


@pytest.mark.parametrize("lane", ["codex", "claude", Worker.OLLAMA, None, 1])
def test_cleanroom_lane_is_explicit_enum(lane):
    with pytest.raises(ValueError, match="Invalid cleanroom request"):
        CleanroomRequest(lane, SIMULATED_CODEX_IMAGE, uuid4(), "fixture-model")
