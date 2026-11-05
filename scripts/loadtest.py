#!/usr/bin/env python3
"""Measure concurrent offline runners and verify the stage-18 database invariants."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import multiprocessing
import os
import signal
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from uuid import UUID, uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import ResultV1, TaskV1, Worker
from app.db.models import Event, LaneStatus, Lease, Result, Setting, Task
from app.db.repository import approve_task, create_task_from_v1
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.workers.contracts import AttemptContext
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.ollama_lane import OllamaLaneRunner

_ROOT = Path(__file__).resolve().parents[1]
_MODEL = "loadtest-fixture"
_LOCK = 0x4C4F414454455354
_PENDING = (TaskState.AWAITING_APPROVAL, TaskState.QUEUED, TaskState.LEASED, TaskState.RUNNING)


class LoadtestError(RuntimeError):
    """Expose only a fixed reason code, never connection or exception details."""


@dataclass(frozen=True)
class Options:
    tasks: int = 50
    runners: int = 3
    timeout_s: float = 120

    def __post_init__(self) -> None:
        if (
            type(self.tasks) is not int
            or not 1 <= self.tasks <= 10000
            or type(self.runners) is not int
            or not 1 <= self.runners <= 16
            or isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, int | float)
            or not math.isfinite(self.timeout_s)
            or not 0 < self.timeout_s <= 3600
        ):
            raise LoadtestError("invalid_loadtest_options")


@dataclass
class RunnerStats:
    index: int
    pid: int
    completed: int = 0
    idle: int = 0
    errors: int = 0
    lease_fencing_conflicts: int = 0
    exit_code: int | None = None


@dataclass(frozen=True)
class StoredResult:
    task_id: UUID
    attempt: int
    created_at: datetime
    valid_success: bool


@dataclass
class Evidence:
    tasks: dict[UUID, tuple[str, int]] = field(default_factory=dict)
    results: list[StoredResult] = field(default_factory=list)
    acquires: list[tuple[UUID, int | None]] = field(default_factory=list)
    approvals: dict[UUID, datetime] = field(default_factory=dict)
    leases: int = 0
    lease_expirations: int = 0
    foreign_tasks: int = 0


def validated_test_url(value: str) -> str:
    """Require an explicit PostgreSQL test database on a local socket or loopback literal.

    Reject libpq routing overrides, services, lists and implicit environment defaults.
    The URL is neither printed nor persisted in artifacts or process arguments.
    """
    try:
        url = make_url(value.strip())
        if (
            url.drivername != "postgresql+psycopg"
            or not (url.database or "").endswith("_test")
            or set(url.query) - {"host", "port"}
        ):
            raise ValueError
        socket = url.query.get("host")
        if url.host:
            if socket is not None or not ipaddress.ip_address(url.host).is_loopback:
                raise ValueError
        elif (
            not isinstance(socket, str)
            or not Path(socket).is_absolute()
            or ".." in Path(socket).parts
            or "," in socket
        ):
            raise ValueError
        port = url.query.get("port", url.port)
        if port is not None and (not isinstance(port, str | int) or not 1 <= int(port) <= 65535):
            raise ValueError
        if "port" in url.query and url.port is not None:
            raise ValueError
    except (ArgumentError, TypeError, ValueError):
        raise LoadtestError("invalid_test_database") from None
    return value.strip()


def synthetic_task(task_id: UUID, run_id: UUID) -> TaskV1:
    return TaskV1.model_validate(
        {
            "schema_version": "task.v1",
            "task_id": str(task_id),
            "parent_task_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "requested_by": "system",
            "kind": "custom",
            "title": "Synthetic runner load test",
            "instructions": "Return the fixture response.",
            "inputs": [],
            "context_refs": [],
            "data_class": "local_only",
            "policy": {
                "allowed_workers": ["ollama"],
                "forbidden_workers": ["claude", "codex"],
                "require_review": True,
                "max_attempts": 1,
                "timeout_s": 30,
            },
            "acceptance": [{"type": "must_contain", "value": "simulation-ok"}],
            "fallback_chain": ["ollama"],
            "labels": {"purpose": "loadtest", "loadtest_run": str(run_id)},
        }
    )


def _environment(url: str, directory: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "DECK_TEST_DATABASE_URL": url,
        "DECK_ROUTER_FILE": str(directory / "router.yaml"),
        "DECK_BLOCKLIST_PATH": str(directory / "blocklist.txt"),
        "DECK_INBOX_DIR": str(directory / "inbox"),
        "DECK_RUNS_DIR": str(directory / "runs"),
    }


def test_suite_environment() -> dict[str, str]:
    """Validate every selected test database before the broader test suite can connect."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("DECK_", "PG"))
    }
    environment["DECK_TEST_DATABASE_URL"] = validated_test_url(
        os.environ.get("DECK_TEST_DATABASE_URL", "")
    )
    for key in (
        "DECK_KB_TEST_DATABASE_URL",
        "DECK_RESTORE_TEST_DATABASE_URL",
        "DECK_KB_RESTORE_TEST_DATABASE_URL",
    ):
        if value := os.environ.get(key, "").strip():
            environment[key] = validated_test_url(value)
    return environment


@contextmanager
def _isolated_environment(environment: dict[str, str]) -> Iterator[None]:
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _engine(url: str) -> Engine:
    return create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000 -c lock_timeout=2000",
        },
    )


class _StoppableSimulation(OllamaExecutor):
    def __init__(self, stop: ProcessEvent) -> None:
        super().__init__(
            profile="simulation",
            simulation=OllamaSimulation(models=(_MODEL,), behavior="delay", delay_s=0.02),
        )
        self.stop = stop

    def execute(self, context: AttemptContext) -> ResultV1:
        original = context.cancel_requested
        return super().execute(
            replace(context, cancel_requested=lambda: self.stop.is_set() or original())
        )


def _worker(
    index: int,
    environment: dict[str, str],
    directory: Path,
    start: ProcessEvent,
    stop: ProcessEvent,
    timeout_s: float,
) -> None:
    """One independent runner process; all adapter work still uses the bundled fake CLI."""
    stats = RunnerStats(index=index, pid=os.getpid())
    engine = None
    try:
        os.environ.clear()
        os.environ.update(environment)
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        engine = _engine(validated_test_url(environment["DECK_TEST_DATABASE_URL"]))
        runner = OllamaLaneRunner(
            sessionmaker(bind=engine, autoflush=False),
            runs_root=directory / "runs",
            executor=_StoppableSimulation(stop),
        )
        (directory / f"ready-{index}").touch()
        if not start.wait(timeout_s):
            raise LoadtestError("runner_start_timeout")
        while not stop.is_set():
            outcome = runner.run_once()
            if outcome.action == "completed":
                stats.completed += 1
                if outcome.status != TaskState.REVIEW.value:
                    stats.errors += 1
                    stop.set()
            elif outcome.action == "idle":
                stats.idle += 1
                stop.wait(0.01)
            else:
                if outcome.action == "lost_lease":
                    stats.lease_fencing_conflicts += 1
                else:
                    stats.errors += 1
                stop.set()
    except Exception:
        stats.errors += 1
        stop.set()
    finally:
        if engine is not None:
            engine.dispose()
        (directory / f"runner-{index}.json").write_text(json.dumps(asdict(stats)), encoding="utf-8")


def _check_database(session: Session, url: str) -> None:
    if session.scalar(select(func.current_database())) != make_url(url).database:
        raise LoadtestError("test_database_identity_mismatch")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head or list(session.scalars(text("SELECT version_num FROM alembic_version"))) != [head]:
        raise LoadtestError("test_database_not_at_head")
    # Workers sweep and claim globally. Refuse existing work before changing any setting.
    for model in (Task, Result, Event, Lease, Setting, LaneStatus):
        if session.scalar(select(func.count()).select_from(model)):
            raise LoadtestError("test_database_not_empty")


def _seed(
    factory: sessionmaker[Session], task_ids: Sequence[UUID], run_id: UUID, runners: int
) -> None:
    with factory() as session:
        update_settings(session, {"max_parallel": runners})
        for task_id in task_ids:
            task = create_task_from_v1(session, synthetic_task(task_id, run_id))
            if task.status != TaskState.AWAITING_APPROVAL:
                raise LoadtestError("synthetic_submission_rejected")
            approve_task(session, task)
            if task.status != TaskState.QUEUED or task.approved_at is None:
                raise LoadtestError("synthetic_approval_rejected")
        session.commit()


def _collect(factory: sessionmaker[Session], task_ids: Sequence[UUID]) -> Evidence:
    evidence = Evidence()
    with factory() as session:
        evidence.tasks = {
            task.id: (task.status.value, task.attempt)
            for task in session.scalars(select(Task).where(Task.id.in_(task_ids)))
        }
        for row in session.scalars(select(Result).where(Result.task_id.in_(task_ids))):
            valid = False
            try:
                result = ResultV1.model_validate(row.payload)
                acceptance = [check for check in result.checks if check.type != "execution_profile"]
                valid = (
                    row.execution_profile == "simulation"
                    and result.task_id == row.task_id
                    and result.attempt == row.attempt
                    and result.status.value == "succeeded"
                    and result.worker == Worker.OLLAMA
                    and result.output.text == "simulation-ok"
                    and bool(acceptance)
                    and all(check.passed for check in acceptance)
                    and any(
                        check.type == "execution_profile"
                        and not check.passed
                        and check.detail == "simulation"
                        for check in result.checks
                    )
                )
            except ValueError:
                pass
            evidence.results.append(StoredResult(row.task_id, row.attempt, row.created_at, valid))
        for event in session.scalars(select(Event).where(Event.task_id.in_(task_ids))):
            if event.event_type == "task.acquire":
                evidence.acquires.append((event.task_id, event.attempt))
            elif event.event_type == "task.approve":
                evidence.approvals[event.task_id] = event.created_at
            elif event.event_type == "task.lease_expired":
                evidence.lease_expirations += 1
        evidence.leases = session.scalar(select(func.count()).select_from(Lease)) or 0
        evidence.foreign_tasks = (
            session.scalar(select(func.count()).select_from(Task).where(Task.id.not_in(task_ids)))
            or 0
        )
    return evidence


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Use nearest-rank percentiles; an empty measurement remains unavailable."""
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def analyse(
    task_ids: Sequence[UUID],
    evidence: Evidence,
    stats: Sequence[RunnerStats],
    *,
    options: Options,
    elapsed_s: float,
    run_error: str | None = None,
) -> dict[str, object]:
    expected = {(task_id, 1) for task_id in task_ids}
    results = Counter((row.task_id, row.attempt) for row in evidence.results)
    acquires = Counter(evidence.acquires)
    successful = {
        row.task_id
        for row in evidence.results
        if row.valid_success
        and evidence.tasks.get(row.task_id) == ("review", 1)
        and results[(row.task_id, row.attempt)] == acquires[(row.task_id, row.attempt)] == 1
        and (row.task_id, row.attempt) in expected
    }
    latencies = [
        (row.created_at - evidence.approvals[row.task_id]).total_seconds()
        for row in evidence.results
        if row.task_id in successful and row.task_id in evidence.approvals
    ]
    counters = {
        "missing_tasks": len(set(task_ids) - evidence.tasks.keys()),
        "foreign_tasks": evidence.foreign_tasks,
        "missing_results": len(expected - results.keys()),
        "duplicate_results": sum(max(0, count - 1) for count in results.values()),
        "unexpected_results": sum(count for key, count in results.items() if key not in expected),
        "missing_acquires": len(expected - acquires.keys()),
        "duplicate_acquires": sum(max(0, count - 1) for count in acquires.values()),
        "unexpected_acquires": sum(count for key, count in acquires.items() if key not in expected),
        "unsuccessful_tasks": options.tasks - len(successful),
        "hanging_leases": evidence.leases,
        "lease_fencing_conflicts": evidence.lease_expirations
        + sum(worker.lease_fencing_conflicts for worker in stats),
        "runner_errors": sum(worker.errors + int(worker.exit_code != 0) for worker in stats),
    }
    violations = [name for name, count in counters.items() if count]
    if run_error:
        violations.append(run_error)
    if (
        len(stats) != options.runners
        or len({worker.pid for worker in stats if worker.pid > 0}) != options.runners
    ):
        violations.append("runner_process_count")
    if sum(worker.completed for worker in stats) != len(evidence.results):
        violations.append("runner_result_count")
    if len(latencies) != len(successful) or any(value < 0 for value in latencies):
        violations.append("invalid_latency_evidence")
    return {
        "schema_version": "loadtest.v1",
        "status": "FAIL" if violations else "PASS",
        "execution_profile": "simulation",
        "tasks": options.tasks,
        "runners": options.runners,
        "completed_tasks": len(successful),
        "elapsed_seconds": round(elapsed_s, 6),
        "throughput_tasks_per_second": round(len(successful) / elapsed_s, 6)
        if elapsed_s > 0
        else 0,
        "latency_seconds": {
            "definition": "approval_event_to_persisted_result",
            "method": "nearest_rank",
            "samples": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "error_rate": counters["unsuccessful_tasks"] / options.tasks,
        "counters": counters,
        "runner_processes": [asdict(worker) for worker in stats],
        "violations": violations,
    }


def _run_workers(
    factory: sessionmaker[Session],
    environment: dict[str, str],
    directory: Path,
    task_ids: Sequence[UUID],
    run_id: UUID,
    options: Options,
) -> tuple[list[RunnerStats], float, str | None]:
    context = multiprocessing.get_context("spawn")
    start, stop = context.Event(), context.Event()
    processes = []
    error = None
    started = time.monotonic()
    deadline = started + options.timeout_s
    try:
        for index in range(options.runners):
            process = context.Process(
                target=_worker,
                args=(index, environment, directory, start, stop, options.timeout_s),
            )
            process.start()
            processes.append(process)
        while not all((directory / f"ready-{index}").exists() for index in range(options.runners)):
            if stop.is_set() or any(not process.is_alive() for process in processes):
                raise LoadtestError("runner_start_failed")
            if time.monotonic() >= deadline:
                raise LoadtestError("loadtest_timeout")
            time.sleep(0.01)
        started = time.monotonic()
        _seed(factory, task_ids, run_id, options.runners)
        start.set()
        while True:
            with factory() as session:
                pending = session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.id.in_(task_ids), Task.status.in_(_PENDING))
                )
            if not pending:
                break
            if stop.is_set() or any(not process.is_alive() for process in processes):
                raise LoadtestError("runner_stopped_early")
            if time.monotonic() >= deadline:
                raise LoadtestError("loadtest_timeout")
            time.sleep(0.02)
    except LoadtestError as exc:
        error = str(exc)
    except Exception:
        error = "loadtest_execution_error"
    finally:
        stop.set()
        start.set()
        shutdown_deadline = time.monotonic() + 8
        for process in processes:
            process.join(timeout=max(0, shutdown_deadline - time.monotonic()))
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
                error = error or "runner_forced_stop"
    stats = []
    for index, process in enumerate(processes):
        try:
            row = RunnerStats(**json.loads((directory / f"runner-{index}.json").read_text()))
        except (OSError, ValueError, TypeError):
            row = RunnerStats(index=index, pid=process.pid or 0, errors=1)
        row.exit_code = process.exitcode
        stats.append(row)
        process.close()
    return stats, time.monotonic() - started, error


def run_loadtest(options: Options) -> dict[str, object]:
    url = validated_test_url(os.environ.get("DECK_TEST_DATABASE_URL", ""))
    run_id = uuid4()
    task_ids = [uuid4() for _ in range(options.tasks)]
    with tempfile.TemporaryDirectory(prefix="deck-loadtest-") as temporary:
        directory = Path(temporary)
        (directory / "router.yaml").write_text(
            json.dumps(
                {
                    "version": 1,
                    "lanes": {
                        "ollama": {"enabled": True, "models": {"default": _MODEL}},
                        "claude": {"enabled": False},
                        "codex": {"enabled": False},
                    },
                }
            ),
            encoding="utf-8",
        )
        (directory / "blocklist.txt").write_text("", encoding="utf-8")
        environment = _environment(url, directory)
        with _isolated_environment(environment):
            engine = _engine(url)
            try:
                with engine.connect() as connection:
                    if not connection.scalar(select(func.pg_try_advisory_lock(_LOCK))):
                        raise LoadtestError("loadtest_already_running")
                    try:
                        with Session(bind=connection) as session:
                            _check_database(session, url)
                        connection.commit()
                        factory = sessionmaker(bind=engine, autoflush=False)
                        stats, elapsed, error = _run_workers(
                            factory, environment, directory, task_ids, run_id, options
                        )
                        report = analyse(
                            task_ids,
                            _collect(factory, task_ids),
                            stats,
                            options=options,
                            elapsed_s=elapsed,
                            run_error=error,
                        )
                        report["run_id"] = str(run_id)
                        return report
                    finally:
                        connection.rollback()
                        connection.execute(select(func.pg_advisory_unlock(_LOCK)))
                        connection.commit()
            finally:
                engine.dispose()


def format_table(report: dict[str, object]) -> str:
    fields = (
        "status",
        "execution_profile",
        "tasks",
        "runners",
        "completed_tasks",
        "elapsed_seconds",
        "throughput_tasks_per_second",
        "error_rate",
    )
    lines = ["Metric | Value", "--- | ---"]
    lines.extend(f"{name} | {report.get(name, 'unavailable')}" for name in fields)
    latency = report.get("latency_seconds", {})
    if isinstance(latency, dict):
        lines.extend(f"latency_{key}_seconds | {latency.get(key)}" for key in ("p50", "p95"))
    counters = report.get("counters", {})
    if isinstance(counters, dict):
        lines.extend(f"{name} | {value}" for name, value in counters.items())
    lines.append(f"violations | {', '.join(report.get('violations', [])) or 'none'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline stage-18 load test on a fresh local test DB"
    )
    parser.add_argument("--tasks", type=int, default=50)
    parser.add_argument("--runners", type=int, default=3)
    parser.add_argument(
        "--test-server",
        action="store_true",
        help="Run make test first, with isolated local test URLs",
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="Harness deadline, not a performance SLA"
    )
    parser.add_argument(
        "--json-output", type=Path, help="Also create a new JSON report; never overwrite"
    )
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        options = Options(args.tasks, args.runners, args.timeout)
        if args.test_server:
            suite = subprocess.run(
                ["make", "test"], cwd=_ROOT, env=test_suite_environment(), check=False
            )
            if suite.returncode:
                return suite.returncode
        report = run_loadtest(options)
        exit_code = 0 if report["status"] == "PASS" else 1
    except LoadtestError as exc:
        report = {"schema_version": "loadtest.v1", "status": "FAIL", "violations": [str(exc)]}
        exit_code = 2
    except Exception:
        report = {
            "schema_version": "loadtest.v1",
            "status": "FAIL",
            "violations": ["loadtest_unavailable"],
        }
        exit_code = 2
    encoded = json.dumps(report, sort_keys=True, allow_nan=False)
    if args.json_output:
        try:
            with args.json_output.open("x", encoding="utf-8") as output:
                output.write(encoded + "\n")
        except OSError:
            report["status"] = "FAIL"
            report["violations"] = [*report["violations"], "report_write_failed"]
            encoded = json.dumps(report, sort_keys=True, allow_nan=False)
            exit_code = 2
    print(format_table(report))
    print(encoded)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
