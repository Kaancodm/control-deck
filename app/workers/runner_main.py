"""Run one explicitly selected simulation or permitted live Ollama attempt."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from app.core.schemas import (
    RedactionInfo,
    ResultOutput,
    ResultStatus,
    ResultTiming,
    ResultUsage,
    ResultV1,
    Worker,
)
from app.db.session import get_session_factory
from app.routing.config import get_router_config
from app.workers.claude_lane import ClaudeLaneRunner
from app.workers.codex_lane import CodexLaneRunner
from app.workers.contracts import AttemptContext, DeclaredProfile
from app.workers.lanes.claude import ClaudeExecutor, ClaudeSimulation
from app.workers.lanes.codex import CodexExecutor, CodexSimulation
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.live_config import LiveConfigurationError, load_live_config
from app.workers.ollama_lane import OllamaLaneRunner
from app.workers.runner import HostRunner


class SimulationExecutor:
    """A deterministic local adapter for checking orchestration without provider calls."""

    profile: DeclaredProfile = "simulation"

    def __init__(self, lane: Worker) -> None:
        if not isinstance(lane, Worker):
            raise ValueError("Executor lane must be a Worker")
        self.lane = lane
        self.last_metadata: dict[str, object] = {}

    def execute(self, context: AttemptContext) -> ResultV1:
        now = datetime.now(UTC)
        cancelled = context.cancel_requested()
        return ResultV1(
            schema_version="result.v1",
            task_id=context.task_id,
            attempt=context.attempt,
            worker=context.lane,
            status=ResultStatus.CANCELLED if cancelled else ResultStatus.SUCCEEDED,
            output=ResultOutput(text="" if cancelled else "simulation-ok", files=[]),
            checks=[],
            usage=ResultUsage(wall_seconds=0.0),
            timing=ResultTiming(started_at=now, finished_at=now),
            error=None,
            redaction=RedactionInfo(
                applied=context.redaction_applied,
                placeholders=context.redaction_placeholders,
            ),
            payload_hash=context.payload_sha256,
            log_ref=f"{context.task_id}/{context.attempt}/{context.lane.value}.log",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one explicitly selected host attempt")
    parser.add_argument("--worker", required=True, choices=[worker.value for worker in Worker])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--once", required=True, action="store_true")
    parser.add_argument(
        "--executor",
        choices=[
            "simulation",
            "ollama-fake-cli",
            "claude-fake-cli",
            "codex-fake-cli",
            "ollama-live",
        ],
        default="simulation",
    )
    args = parser.parse_args()
    worker = Worker(args.worker)
    if args.executor == "ollama-fake-cli" and worker != Worker.OLLAMA:
        parser.error("The Ollama fake CLI can only serve the ollama lane")
    if args.executor == "claude-fake-cli" and worker != Worker.CLAUDE:
        parser.error("The Claude fake CLI can only serve the claude lane")
    if args.executor == "codex-fake-cli" and worker != Worker.CODEX:
        parser.error("The Codex fake CLI can only serve the codex lane")
    endpoint = None
    if args.live:
        try:
            endpoint = load_live_config(worker)
        except LiveConfigurationError as exc:
            print(json.dumps({"action": "unavailable", "error_code": exc.code}))
            return 78
        if args.executor != "ollama-live":
            parser.error("Live mode requires the ollama-live executor")
    elif args.executor == "ollama-live":
        parser.error("The ollama-live executor requires live mode")
    factory = get_session_factory()
    runs_root = Path(os.environ.get("DECK_RUNS_DIR", "runs"))
    if args.executor == "ollama-live":
        assert endpoint is not None
        live_executor = OllamaExecutor(profile="live", url=endpoint.url)
        outcome = OllamaLaneRunner(factory, runs_root=runs_root, executor=live_executor).run_once()
    elif args.executor == "codex-fake-cli":
        configuration = get_router_config()
        models = tuple(sorted(set(configuration.lanes[Worker.CODEX].models.values())))
        codex_executor = CodexExecutor(
            profile="simulation", simulation=CodexSimulation(models=models)
        )
        outcome = CodexLaneRunner(factory, runs_root=runs_root, executor=codex_executor).run_once()
    elif args.executor == "claude-fake-cli":
        configuration = get_router_config()
        models = tuple(sorted(set(configuration.lanes[Worker.CLAUDE].models.values())))
        claude_executor = ClaudeExecutor(
            profile="simulation", simulation=ClaudeSimulation(models=models)
        )
        outcome = ClaudeLaneRunner(
            factory, runs_root=runs_root, executor=claude_executor
        ).run_once()
    elif args.executor == "ollama-fake-cli":
        configuration = get_router_config()
        models = tuple(sorted(set(configuration.lanes[Worker.OLLAMA].models.values())))
        executor = OllamaExecutor(profile="simulation", simulation=OllamaSimulation(models=models))
        outcome = OllamaLaneRunner(factory, runs_root=runs_root, executor=executor).run_once()
    else:
        runner = HostRunner(factory, runs_root=runs_root)
        outcome = runner.run_once(worker, SimulationExecutor(worker))

    print(
        json.dumps(
            {"action": outcome.action, "status": outcome.status, "error_code": outcome.error_code}
        )
    )
    return (
        1 if outcome.action == "error" or (outcome.action == "unavailable" and not args.live) else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
