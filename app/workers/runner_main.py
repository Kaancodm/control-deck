"""Explicit simulated host-runner command; no provider process or model is invoked."""

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
from app.workers.contracts import AttemptContext
from app.workers.runner import HostRunner


class SimulationExecutor:
    """A deterministic local adapter for checking orchestration without provider calls."""

    def __init__(self, lane: Worker) -> None:
        if not isinstance(lane, Worker):
            raise ValueError("Executor lane must be a Worker")
        self.lane = lane

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
    parser = argparse.ArgumentParser(description="Run one explicitly simulated host attempt")
    parser.add_argument("--worker", required=True, choices=[worker.value for worker in Worker])
    parser.add_argument("--simulate", required=True, action="store_true")
    parser.add_argument("--once", required=True, action="store_true")
    args = parser.parse_args()
    runner = HostRunner(
        get_session_factory(),
        runs_root=Path(os.environ.get("DECK_RUNS_DIR", "runs")),
    )
    worker = Worker(args.worker)
    outcome = runner.run_once(worker, SimulationExecutor(worker))
    print(
        json.dumps(
            {"action": outcome.action, "status": outcome.status, "error_code": outcome.error_code}
        )
    )
    return 1 if outcome.action == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
