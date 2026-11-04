#!/usr/bin/env python3
"""Bounded queue load test (stage 18, decision D2).

Runs synthetic tasks through parallel simulated host-runner processes and proves
the queue invariants: exactly one result per attempt, exactly one ``task.acquire``
event per attempt, and no hanging leases after the run.

Safety boundaries:
- Uses only ``DECK_TEST_DATABASE_URL``; the database name must end in ``_test``.
- The executor is the bundled deterministic simulation. No provider process,
  model download, or network call happens.
- The codex lane is intentionally excluded (no active account); ollama and
  claude remain available.

Usage:
    uv run python scripts/loadtest.py                 # 50 tasks, 3 runners, ollama
    uv run python scripts/loadtest.py --worker claude --tasks 50 --runners 3
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from alembic.config import Config
from sqlalchemy import func, select

from alembic import command
from app.core.schemas import TaskV1, Worker
from app.db.config import DatabaseConfigurationError, get_test_database_url
from app.db.models import Event, Lease, Result, Task
from app.db.repository import approve_task, create_task_from_v1
from app.db.session import get_session_factory
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TASKS = 50
_DEFAULT_RUNNERS = 3
_MAX_TASKS = 500
_MAX_RUNNERS = 8
_IDLE_ROUNDS = 12
_POLL_SECONDS = 0.05
_FINISHED_STATES = (TaskState.REVIEW, TaskState.DONE)

# The load test owns its routing view: ollama and claude are enabled, codex stays
# disabled. Models are irrelevant for the deterministic simulation executor.
_ROUTER_TEMPLATE = """\
version: 1
lanes:
  ollama:
    enabled: true
    daily_attempt_limit: null
    models: {}
  claude:
    enabled: true
    daily_attempt_limit: null
    models: {}
  codex:
    enabled: false
    daily_attempt_limit: null
    models: {}
rules: []
"""


def _task_payload(index: int, run_id: str, worker: Worker) -> dict[str, object]:
    """Synthetic, secret-free task content; cloud lanes use ``cloud_ok``."""
    data_class = "local_only" if worker is Worker.OLLAMA else "cloud_ok"
    return {
        "schema_version": "task.v1",
        "task_id": str(uuid4()),
        "parent_task_id": None,
        "created_at": datetime.now(UTC).isoformat(),
        "requested_by": "system",
        "kind": "custom",
        "title": f"loadtest-{index:04d}",
        "instructions": f"synthetic load test task {index} for queue invariants",
        "inputs": [],
        "context_refs": [],
        "data_class": data_class,
        "policy": {"forbidden_workers": [], "max_attempts": 3, "require_review": True},
        "acceptance": [],
        "fallback_chain": [worker.value],
        "labels": {"suite": "loadtest", "run": run_id},
    }


def _worker_main(worker: Worker, runs_root: Path, deadline_s: float) -> int:
    """One runner process: claim and simulate attempts until the queue drains."""
    factory = get_session_factory(for_tests=True)
    runner = HostRunner(
        factory,
        runs_root=runs_root,
        lease_ttl=timedelta(seconds=30),
        heartbeat_interval_s=1.0,
    )
    executor = SimulationExecutor(worker)
    deadline = time.monotonic() + deadline_s
    completed = 0
    idle_streak = 0
    failures: list[str] = []
    while time.monotonic() < deadline and idle_streak < _IDLE_ROUNDS:
        outcome = runner.run_once(worker, executor)
        if outcome.action == "completed":
            completed += 1
            idle_streak = 0
        elif outcome.action == "idle":
            idle_streak += 1
            time.sleep(_POLL_SECONDS)
        else:
            failures.append(outcome.error_code or outcome.action)
            break
    print(
        json.dumps(
            {
                "role": "worker",
                "worker": worker.value,
                "completed": completed,
                "failures": failures,
            }
        ),
        flush=True,
    )
    return 1 if failures else 0


def _terminate_group(process: subprocess.Popen[str]) -> None:
    """Stop a runner and its children without leaving orphaned attempts."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _migrate_test_database() -> None:
    os.chdir(_REPO_ROOT)
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(config, "head")


def _seed_tasks(count: int, runners: int, worker: Worker) -> tuple[str, list[UUID]]:
    run_id = uuid4().hex
    factory = get_session_factory(for_tests=True)
    ids: list[UUID] = []
    with factory() as session:
        update_settings(
            session,
            {
                "paused": False,
                "stop_requested": False,
                "max_parallel": runners,
                "lanes.ollama.paused": False,
                "lanes.claude.paused": False,
                "lanes.codex.paused": False,
            },
        )
        session.commit()
    with factory() as session:
        for index in range(count):
            payload = _task_payload(index, run_id, worker)
            task = create_task_from_v1(session, TaskV1.model_validate(payload))
            approve_task(session, task)
            ids.append(task.id)
        session.commit()
    return run_id, ids


def _check_invariants(ids: list[UUID]) -> tuple[list[tuple[str, bool, str]], dict[str, int]]:
    """Prove D2: one result and one acquire per attempt, no hanging leases."""
    factory = get_session_factory(for_tests=True)
    checks: list[tuple[str, bool, str]] = []
    with factory() as session:
        tasks = session.scalars(select(Task).where(Task.id.in_(ids))).all()
        attempts = {task.id: task.attempt for task in tasks}
        unfinished = [task.id for task in tasks if task.status not in _FINISHED_STATES]
        checks.append(
            (
                "tasks_finished",
                bool(tasks) and len(tasks) == len(ids) and not unfinished,
                f"finished={len(tasks) - len(unfinished)}/{len(ids)}",
            )
        )
        single_attempt = all(attempt == 1 for attempt in attempts.values())
        checks.append(
            (
                "single_attempt_per_task",
                bool(attempts) and single_attempt,
                f"tasks_with_one_attempt={sum(a == 1 for a in attempts.values())}/{len(ids)}",
            )
        )

        result_rows = session.execute(
            select(Result.task_id, Result.attempt).where(Result.task_id.in_(ids))
        ).all()
        result_counts = Counter(row.task_id for row in result_rows)
        result_pairs = Counter((row.task_id, row.attempt) for row in result_rows)
        results_ok = (
            len(result_rows) == len(ids)
            and all(result_counts.get(task_id) == attempts.get(task_id) for task_id in ids)
            and all(count == 1 for count in result_pairs.values())
        )
        checks.append(
            (
                "one_result_per_attempt",
                results_ok,
                f"results={len(result_rows)} duplicate_pairs="
                f"{sum(count > 1 for count in result_pairs.values())}",
            )
        )

        acquire_rows = session.execute(
            select(Event.task_id, Event.attempt).where(
                Event.task_id.in_(ids), Event.event_type == "task.acquire"
            )
        ).all()
        acquire_counts = Counter(row.task_id for row in acquire_rows)
        acquire_pairs = Counter((row.task_id, row.attempt) for row in acquire_rows)
        acquire_ok = (
            len(acquire_rows) == len(ids)
            and all(acquire_counts.get(task_id) == attempts.get(task_id) for task_id in ids)
            and all(count == 1 for count in acquire_pairs.values())
        )
        checks.append(
            (
                "one_acquire_per_attempt",
                acquire_ok,
                f"acquires={len(acquire_rows)} duplicate_pairs="
                f"{sum(count > 1 for count in acquire_pairs.values())}",
            )
        )

        hanging = (
            session.scalar(select(func.count()).select_from(Lease).where(Lease.task_id.in_(ids)))
            or 0
        )
        checks.append(("no_hanging_leases", hanging == 0, f"leases_remaining={hanging}"))
    totals = {
        "results": len(result_rows),
        "acquire_events": len(acquire_rows),
        "leases_remaining": hanging,
    }
    return checks, totals


def run_loadtest(tasks: int, runners: int, worker: Worker, timeout_s: float) -> int:
    started = time.monotonic()
    test_url = get_test_database_url()  # refuses any database not ending in _test

    with tempfile.TemporaryDirectory(prefix="deck-loadtest-") as scratch:
        scratch_path = Path(scratch)
        router_file = scratch_path / "router.yaml"
        router_file.write_text(_ROUTER_TEMPLATE, encoding="utf-8")
        runs_root = scratch_path / "runs"
        runs_root.mkdir(mode=0o700)
        # Cloud data classes require an explicit external blocklist; the synthetic
        # entry never matches the generated task text.
        blocklist_file = scratch_path / "blocklist.txt"
        blocklist_file.write_text("# synthetic loadtest blocklist\nloadtest-never-matches\n")
        os.environ["DECK_ROUTER_FILE"] = str(router_file)
        os.environ["DECK_BLOCKLIST_PATH"] = str(blocklist_file)

        _migrate_test_database()
        run_id, ids = _seed_tasks(tasks, runners, worker)

        environment = {
            "PATH": os.defpath,
            "DECK_TEST_DATABASE_URL": test_url,
            "DECK_ROUTER_FILE": str(router_file),
            "DECK_BLOCKLIST_PATH": str(blocklist_file),
        }
        if os.environ.get("PYTHONPATH"):
            environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
        command_line = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker-process",
            worker.value,
            "--runs-dir",
            str(runs_root),
            "--deadline-s",
            str(timeout_s),
        ]
        processes = [
            subprocess.Popen(
                command_line,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            for _ in range(runners)
        ]
        deadline = time.monotonic() + timeout_s
        worker_summaries: list[dict[str, object]] = []
        runner_exit_codes: list[int] = []
        timed_out = False
        for process in processes:
            remaining = max(0.5, deadline - time.monotonic())
            try:
                stdout, _stderr = process.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_group(process)
                stdout, _stderr = process.communicate()
            runner_exit_codes.append(process.returncode)
            for line in stdout.splitlines():
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and parsed.get("role") == "worker":
                    worker_summaries.append(parsed)

    checks, totals = _check_invariants(ids)
    checks.append(
        (
            "runners_clean_exit",
            not timed_out and all(code == 0 for code in runner_exit_codes),
            f"exit_codes={runner_exit_codes} timed_out={timed_out}",
        )
    )
    failures = [name for name, ok, _detail in checks if not ok]
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    duration = round(time.monotonic() - started, 3)
    print(
        json.dumps(
            {
                "status": "fail" if failures else "pass",
                "run": run_id,
                "worker": worker.value,
                "tasks": tasks,
                "runners": runners,
                "workers_reported": len(worker_summaries),
                "attempts_completed": sum(
                    int(summary.get("completed", 0)) for summary in worker_summaries
                ),
                "duration_s": duration,
                "failed_checks": failures,
                **totals,
            },
            sort_keys=True,
        )
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Queue load test with simulated runners (test database only)",
        epilog="The codex lane is excluded: no active account. Use ollama or claude.",
    )
    parser.add_argument("--tasks", type=int, default=_DEFAULT_TASKS)
    parser.add_argument("--runners", type=int, default=_DEFAULT_RUNNERS)
    parser.add_argument("--worker", choices=("ollama", "claude"), default="ollama")
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--worker-process", choices=("ollama", "claude"), help=argparse.SUPPRESS)
    parser.add_argument("--runs-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--deadline-s", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker_process is not None:
        if args.runs_dir is None or args.deadline_s is None:
            parser.error("--worker-process requires --runs-dir and --deadline-s")
        if not 1 <= args.deadline_s <= 3600:
            parser.error("--deadline-s must be between 1 and 3600")
        return _worker_main(Worker(args.worker_process), args.runs_dir, args.deadline_s)

    if not 1 <= args.tasks <= _MAX_TASKS:
        parser.error(f"--tasks must be between 1 and {_MAX_TASKS}")
    if not 1 <= args.runners <= _MAX_RUNNERS:
        parser.error(f"--runners must be between 1 and {_MAX_RUNNERS}")
    if not 10 <= args.timeout_s <= 1800:
        parser.error("--timeout-s must be between 10 and 1800")
    try:
        get_test_database_url()
    except DatabaseConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return run_loadtest(args.tasks, args.runners, Worker(args.worker), args.timeout_s)


if __name__ == "__main__":
    raise SystemExit(main())
