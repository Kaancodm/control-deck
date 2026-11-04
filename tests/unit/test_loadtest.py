"""The offline load-test gate uses independent expectations and local-only inputs."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import loadtest


def _url(*, database="loadtest_test", host=None, query=""):
    host = host if host is not None else str(ipaddress.IPv4Address(0x7F000001))
    return f"postgresql+psycopg:///{database}?host={host}{query}"


def _loopback_url(database="loadtest_test"):
    host = str(ipaddress.IPv4Address(0x7F000001))
    return f"postgresql+psycopg://{host}/{database}"


def _successful_evidence():
    task_ids = [uuid4(), uuid4()]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    evidence = loadtest.Evidence(
        tasks={task_id: ("review", 1) for task_id in task_ids},
        results=[
            loadtest.StoredResult(task_ids[0], 1, base + timedelta(seconds=1), True),
            loadtest.StoredResult(task_ids[1], 1, base + timedelta(seconds=3), True),
        ],
        acquires=[(task_id, 1) for task_id in task_ids],
        approvals=dict.fromkeys(task_ids, base),
    )
    workers = [
        loadtest.RunnerStats(0, pid=10, completed=1, exit_code=0),
        loadtest.RunnerStats(1, pid=11, completed=1, exit_code=0),
    ]
    return task_ids, evidence, workers


def _analyse(task_ids, evidence, workers, **kwargs):
    return loadtest.analyse(
        task_ids,
        evidence,
        workers,
        options=loadtest.Options(tasks=2, runners=2),
        elapsed_s=4,
        **kwargs,
    )


def test_defaults_match_stage_18_and_fixture_cannot_select_live_or_cloud():
    assert loadtest.Options() == loadtest.Options(tasks=50, runners=3, timeout_s=120)
    task = loadtest.synthetic_task(uuid4(), uuid4())
    assert task.data_class.value == "local_only"
    assert [worker.value for worker in task.fallback_chain] == ["ollama"]
    assert task.policy.max_attempts == 1
    assert task.policy.require_review is True
    assert task.context_refs == []
    assert task.inputs == []


@pytest.mark.parametrize(
    "changes",
    [
        {"tasks": 0},
        {"tasks": True},
        {"tasks": 10001},
        {"runners": 0},
        {"runners": 17},
        {"runners": True},
        {"timeout_s": 0},
        {"timeout_s": True},
        {"timeout_s": None},
        {"timeout_s": "1"},
        {"timeout_s": float("nan")},
        {"timeout_s": float("inf")},
    ],
)
def test_invalid_load_options_fail_closed(changes):
    with pytest.raises(loadtest.LoadtestError, match="^invalid_loadtest_options$"):
        loadtest.Options(**changes)


def test_database_guard_accepts_only_explicit_local_test_endpoints(tmp_path):
    loopback = _loopback_url()
    socket = _url(host=str(tmp_path), query="&port=55432")
    assert loadtest.validated_test_url(loopback) == loopback
    assert loadtest.validated_test_url(socket) == socket


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not a database URL",
        "sqlite:///loadtest_test",
        "postgresql+psycopg:///loadtest_test",
        _loopback_url("runtime"),
        _url(),  # A TCP endpoint must not be smuggled in through the socket query.
        _loopback_url() + "?hostaddr=" + str(ipaddress.IPv4Address(0xC0000201)),
        _loopback_url() + "?service=fixture",
        _loopback_url() + "?dbname=runtime",
        _loopback_url() + "?host=/tmp/fixture",
        _url(host="relative"),
        _url(host="/tmp/../fixture"),
        _url(host="/tmp/fixture", query="&port=0"),
        _url(host="/tmp/fixture", query="&port=65536"),
        _url(host="/tmp/fixture", query="&host=/tmp/second"),
        f"postgresql+psycopg://{ipaddress.IPv4Address(0xC0000201)}/loadtest_test",
    ],
)
def test_unsafe_or_ambiguous_database_urls_are_rejected_without_echo(value):
    with pytest.raises(loadtest.LoadtestError, match="^invalid_test_database$"):
        loadtest.validated_test_url(value)


def test_child_environment_ignores_live_provider_and_libpq_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("DECK_DATABASE_URL", "sensitive-runtime-value")
    monkeypatch.setenv("PGSERVICE", "untrusted-service")
    monkeypatch.setenv("DECK_OLLAMA_URL", "sensitive-provider-value")
    original = dict(loadtest.os.environ)
    environment = loadtest._environment(_loopback_url(), tmp_path)
    with loadtest._isolated_environment(environment):
        assert dict(loadtest.os.environ) == environment
        assert "DECK_LIVE_ENABLED" not in environment
        assert "DECK_DATABASE_URL" not in environment
        assert "DECK_OLLAMA_URL" not in environment
        assert "PGSERVICE" not in environment
    assert dict(loadtest.os.environ) == original


def test_suite_environment_validates_all_databases_and_removes_routing_overrides(monkeypatch):
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", _loopback_url())
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", _loopback_url("knowledge_test"))
    monkeypatch.setenv("DECK_RESTORE_TEST_DATABASE_URL", _loopback_url("restore_test"))
    monkeypatch.setenv("DECK_KB_RESTORE_TEST_DATABASE_URL", _loopback_url("kb_restore_test"))
    monkeypatch.setenv("DECK_DATABASE_URL", "unrelated-runtime-value")
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("PGHOSTADDR", str(ipaddress.IPv4Address(0xC0000201)))
    monkeypatch.setenv("PGSERVICE", "untrusted-service")
    monkeypatch.setenv("PGOPTIONS", "untrusted-options")
    monkeypatch.setenv("PGDATABASE", "unrelated-runtime-database")
    environment = loadtest.test_suite_environment()
    assert not any(key.startswith("PG") for key in environment)
    assert {key for key in environment if key.startswith("DECK_")} == {
        "DECK_TEST_DATABASE_URL",
        "DECK_KB_TEST_DATABASE_URL",
        "DECK_RESTORE_TEST_DATABASE_URL",
        "DECK_KB_RESTORE_TEST_DATABASE_URL",
    }
    assert environment["DECK_KB_TEST_DATABASE_URL"] == _loopback_url("knowledge_test")


@pytest.mark.parametrize(
    "key",
    [
        "DECK_TEST_DATABASE_URL",
        "DECK_KB_TEST_DATABASE_URL",
        "DECK_RESTORE_TEST_DATABASE_URL",
        "DECK_KB_RESTORE_TEST_DATABASE_URL",
    ],
)
def test_suite_rejects_each_external_test_database_before_starting_tests(monkeypatch, capsys, key):
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", _loopback_url())
    monkeypatch.setenv(
        key, f"postgresql+psycopg://{ipaddress.IPv4Address(0xC0000201)}/external_test"
    )
    monkeypatch.setattr(
        loadtest.subprocess, "run", lambda *_, **__: pytest.fail("No process allowed")
    )
    monkeypatch.setattr(loadtest, "run_loadtest", lambda _: pytest.fail("No load test allowed"))
    assert loadtest.main(["--test-server"]) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["violations"] == [
        "invalid_test_database"
    ]


def test_suite_and_loadtest_are_sequential_and_receive_safe_environment(monkeypatch, capsys):
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", _loopback_url())
    monkeypatch.setenv("PGHOSTADDR", str(ipaddress.IPv4Address(0xC0000201)))
    calls = []

    def suite(command, **kwargs):
        assert command == ["make", "test"]
        assert "PGHOSTADDR" not in kwargs["env"]
        calls.append("suite")
        return SimpleNamespace(returncode=0)

    def run(options):
        assert calls == ["suite"]
        assert options.tasks == 7 and options.runners == 2
        calls.append("loadtest")
        return {"status": "PASS", "violations": []}

    monkeypatch.setattr(loadtest.subprocess, "run", suite)
    monkeypatch.setattr(loadtest, "run_loadtest", run)
    assert loadtest.main(["--test-server", "--tasks", "7", "--runners", "2"]) == 0
    assert calls == ["suite", "loadtest"]
    capsys.readouterr()


def test_failing_suite_prevents_the_loadtest(monkeypatch):
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", _loopback_url())
    monkeypatch.setattr(loadtest.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=3))
    monkeypatch.setattr(loadtest, "run_loadtest", lambda _: pytest.fail("A prior gate failed"))
    assert loadtest.main(["--test-server"]) == 3


def test_nearest_rank_latency_and_throughput_have_known_oracles():
    report = _analyse(*_successful_evidence())
    assert report["status"] == "PASS"
    assert report["completed_tasks"] == 2
    assert report["error_rate"] == 0
    assert report["throughput_tasks_per_second"] == 0.5
    assert report["latency_seconds"] == {
        "definition": "approval_event_to_persisted_result",
        "method": "nearest_rank",
        "samples": 2,
        "p50": 1,
        "p95": 3,
    }
    assert loadtest.percentile(list(range(1, 51)), 0.95) == 48
    assert loadtest.percentile([7], 0.5) == 7
    assert loadtest.percentile([], 0.95) is None


@pytest.mark.parametrize(
    "defect, reason",
    [
        ("missing_task", "missing_tasks"),
        ("foreign_task", "foreign_tasks"),
        ("missing_result", "missing_results"),
        ("duplicate_result", "duplicate_results"),
        ("unexpected_result_attempt", "unexpected_results"),
        ("missing_acquire", "missing_acquires"),
        ("duplicate_acquire", "duplicate_acquires"),
        ("unexpected_acquire_attempt", "unexpected_acquires"),
        ("wrong_provenance_or_failure", "unsuccessful_tasks"),
        ("unfinished_task", "unsuccessful_tasks"),
        ("hanging_lease", "hanging_leases"),
        ("expired_lease", "lease_fencing_conflicts"),
        ("lost_lease", "lease_fencing_conflicts"),
        ("runner_error", "runner_errors"),
        ("runner_exit", "runner_errors"),
        ("missing_runner", "runner_process_count"),
        ("same_runner_pid", "runner_process_count"),
        ("unreported_result", "runner_result_count"),
        ("missing_timing", "invalid_latency_evidence"),
        ("negative_timing", "invalid_latency_evidence"),
    ],
)
def test_each_spec_violation_independently_fails_the_gate(defect, reason):
    task_ids, evidence, workers = _successful_evidence()
    if defect == "missing_task":
        del evidence.tasks[task_ids[0]]
    elif defect == "foreign_task":
        evidence.foreign_tasks = 1
    elif defect == "missing_result":
        evidence.results.pop()
    elif defect == "duplicate_result":
        evidence.results.append(evidence.results[0])
    elif defect == "unexpected_result_attempt":
        evidence.results[0] = replace(evidence.results[0], attempt=2)
    elif defect == "missing_acquire":
        evidence.acquires.pop()
    elif defect == "duplicate_acquire":
        evidence.acquires.append(evidence.acquires[0])
    elif defect == "unexpected_acquire_attempt":
        evidence.acquires[0] = (task_ids[0], 2)
    elif defect == "wrong_provenance_or_failure":
        evidence.results[0] = replace(evidence.results[0], valid_success=False)
    elif defect == "unfinished_task":
        evidence.tasks[task_ids[0]] = ("running", 1)
    elif defect == "hanging_lease":
        evidence.leases = 1
    elif defect == "expired_lease":
        evidence.lease_expirations = 1
    elif defect == "lost_lease":
        workers[0].lease_fencing_conflicts = 1
    elif defect == "runner_error":
        workers[0].errors = 1
    elif defect == "runner_exit":
        workers[0].exit_code = 2
    elif defect == "missing_runner":
        workers.pop()
    elif defect == "same_runner_pid":
        workers[0].pid = workers[1].pid
    elif defect == "unreported_result":
        workers[0].completed = 0
    elif defect == "missing_timing":
        del evidence.approvals[task_ids[0]]
    elif defect == "negative_timing":
        evidence.results[0] = replace(
            evidence.results[0], created_at=evidence.approvals[task_ids[0]] - timedelta(seconds=1)
        )
    report = _analyse(task_ids, evidence, workers)
    assert report["status"] == "FAIL"
    assert reason in report["violations"]


def test_time_limit_is_a_harness_failure_without_an_invented_performance_sla():
    task_ids, evidence, workers = _successful_evidence()
    report = loadtest.analyse(
        task_ids,
        evidence,
        workers,
        options=loadtest.Options(tasks=2, runners=2),
        elapsed_s=10000,
    )
    assert report["status"] == "PASS"
    timed_out = _analyse(task_ids, evidence, workers, run_error="loadtest_timeout")
    assert timed_out["status"] == "FAIL"
    assert "loadtest_timeout" in timed_out["violations"]


def test_cli_prints_table_and_json_and_saves_a_new_report(monkeypatch, tmp_path, capsys):
    report = _analyse(*_successful_evidence())
    selected = []

    def run(options):
        selected.append(options)
        return report

    monkeypatch.setattr(loadtest, "run_loadtest", run)
    destination = tmp_path / "result.json"
    assert loadtest.main(["--tasks", "2", "--runners", "2", "--json-output", str(destination)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("Metric | Value\n")
    assert json.loads(output.splitlines()[-1]) == report
    assert json.loads(destination.read_text()) == report
    assert selected == [loadtest.Options(tasks=2, runners=2)]


def test_cli_defaults_are_not_silently_changed(monkeypatch, capsys):
    selected = []

    def run(options):
        selected.append(options)
        return {"status": "PASS", "violations": []}

    monkeypatch.setattr(loadtest, "run_loadtest", run)
    assert loadtest.main([]) == 0
    assert selected == [loadtest.Options(tasks=50, runners=3)]
    capsys.readouterr()


def test_cli_never_overwrites_an_existing_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(loadtest, "run_loadtest", lambda _: _analyse(*_successful_evidence()))
    destination = tmp_path / "result.json"
    destination.write_text("existing report")
    assert loadtest.main(["--json-output", str(destination)]) == 2
    assert destination.read_text() == "existing report"
    assert (
        "report_write_failed" in json.loads(capsys.readouterr().out.splitlines()[-1])["violations"]
    )


@pytest.mark.parametrize("status, expected_exit", [("PASS", 0), ("FAIL", 1)])
def test_cli_exit_status_matches_gate(monkeypatch, capsys, status, expected_exit):
    monkeypatch.setattr(loadtest, "run_loadtest", lambda _: {"status": status, "violations": []})
    assert loadtest.main([]) == expected_exit
    capsys.readouterr()


def test_cli_sanitizes_database_errors(monkeypatch, capsys):
    def fail(_):
        raise RuntimeError("untrusted connection contents")

    monkeypatch.setattr(loadtest, "run_loadtest", fail)
    assert loadtest.main([]) == 2
    output = capsys.readouterr().out
    assert "untrusted connection contents" not in output
    assert json.loads(output.splitlines()[-1])["violations"] == ["loadtest_unavailable"]


def test_missing_test_database_does_not_fall_back_to_runtime(monkeypatch, capsys):
    monkeypatch.delenv("DECK_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DECK_DATABASE_URL", _loopback_url())
    monkeypatch.setattr(loadtest, "_engine", lambda _: pytest.fail("Must fail before connecting"))
    assert loadtest.main([]) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["violations"] == [
        "invalid_test_database"
    ]


@pytest.mark.parametrize("action", ["lost_lease", "error", "unavailable"])
def test_worker_conflicts_and_errors_stop_the_run_without_exposing_details(
    monkeypatch, tmp_path, action
):
    context = loadtest.multiprocessing.get_context("spawn")
    start, stop = context.Event(), context.Event()
    start.set()
    monkeypatch.setattr(loadtest.signal, "signal", lambda *_: None)
    monkeypatch.setattr(loadtest, "_engine", lambda _: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(loadtest, "sessionmaker", lambda **_: None)
    monkeypatch.setattr(
        loadtest,
        "OllamaLaneRunner",
        lambda *_, **__: SimpleNamespace(
            run_once=lambda: SimpleNamespace(action=action, error_code="untrusted details")
        ),
    )
    environment = loadtest._environment(_loopback_url(), tmp_path)
    with loadtest._isolated_environment(dict(loadtest.os.environ)):
        loadtest._worker(0, environment, tmp_path, start, stop, 1)
    assert stop.is_set()
    report_text = (tmp_path / "runner-0.json").read_text()
    report = json.loads(report_text)
    assert "untrusted details" not in report_text
    assert report["lease_fencing_conflicts"] == (1 if action == "lost_lease" else 0)
    assert report["errors"] == (0 if action == "lost_lease" else 1)
