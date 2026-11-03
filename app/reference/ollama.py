"""Run reference transport through the bundled Ollama fake CLI only."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.core.schemas import ResultStatus, TaskV1, Worker
from app.redaction.artifacts import PayloadArtifactError, write_payload_sent
from app.redaction.payload import PayloadBuild
from app.reference.runner import CaseNotApplicable, ExecutionResult, FakeExecutor
from app.workers.contracts import AttemptContext
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation


@dataclass(kw_only=True)
class OllamaReferenceExecutor(FakeExecutor):
    """Share reference routing and gates, then execute one explicitly simulated model."""

    model: str
    _executor: OllamaExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._executor = OllamaExecutor(
            profile="simulation",
            simulation=OllamaSimulation(
                models=(self.model,), output_text="reference-ok: Simulation completed."
            ),
        )

    def execute(self, task: TaskV1) -> ExecutionResult:
        started = time.monotonic()
        result = super().execute(task)
        return replace(result, wall_seconds=max(0.0, time.monotonic() - started))

    def _execute_prepared(
        self, task: TaskV1, payload: PayloadBuild, worker: Worker
    ) -> ExecutionResult:
        if worker != Worker.OLLAMA:
            # Routing and the privacy gate already passed; the Ollama lane is local and
            # never substitutes for a cloud lane, so the case is not applicable here.
            raise CaseNotApplicable(
                f"routed to {worker.value}; the Ollama lane does not execute cloud cases"
            )

        try:
            with TemporaryDirectory(prefix="control-deck-reference-") as directory:
                runs_root = Path(directory) / "runs"
                artifact = write_payload_sent(
                    runs_root, task_id=task.task_id, attempt=1, text=payload.text
                )
                context = AttemptContext(
                    task_id=task.task_id,
                    attempt=1,
                    lease_id=uuid4(),
                    lane=worker,
                    kind=task.kind,
                    model=self.model,
                    timeout_s=task.policy.timeout_s,
                    deadline_at=None,
                    run_dir=(runs_root / artifact.relative_path).parent,
                    payload_text=payload.text,
                    payload_sha256=artifact.sha256,
                    redaction_applied=payload.applied,
                    redaction_placeholders=payload.placeholders,
                    cancel_requested=lambda: False,
                )
                result = self._executor.execute(context)
        except (OSError, PayloadArtifactError):
            return ExecutionResult(ResultStatus.FAILED, "", worker, 0.0)

        if result.worker != worker or result.task_id != task.task_id or result.attempt != 1:
            return ExecutionResult(ResultStatus.FAILED, "", worker, 0.0)
        return ExecutionResult(
            result.status,
            result.output.text or "" if result.status == ResultStatus.SUCCEEDED else "",
            worker,
            0.0,
        )
