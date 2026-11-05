"""Live reference evidence requires the flag, configured models and a real HTTP boundary."""

import json
from pathlib import Path

import pytest

from app.core.schemas import ResultStatus, TaskV1
from app.reference.load import load_all_cases
from app.reference.ollama import OllamaReferenceExecutor
from app.reference.runner import FakeExecutor, evaluate_expect, run_cases
from scripts.run_reference import main
from tests.support.ollama_stub import OllamaStub

MODEL = "reference-fixture:configured"


def configure(monkeypatch, tmp_path, url):
    router = tmp_path / "router.yaml"
    router.write_text(json.dumps({"lanes": {"ollama": {"models": {"default": MODEL}}}}))
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_ROUTER_FILE", str(router))
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("DECK_OLLAMA_URL", url)


def test_live_reference_gates_before_network(monkeypatch):
    monkeypatch.delenv("DECK_LIVE_ENABLED", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["--executor", "ollama-live", "--model", MODEL])
    assert error.value.code == 2


def test_live_reference_requires_configured_model(monkeypatch, tmp_path):
    with OllamaStub(models=(MODEL,)) as stub:
        configure(monkeypatch, tmp_path, stub.url)
        with pytest.raises(ValueError):
            OllamaReferenceExecutor(model="unlisted:tag", execution_profile="live")
        assert stub.requests == []


def test_reference_tracks_live_evidence_and_exact_payload(monkeypatch, tmp_path):
    with OllamaStub(models=(MODEL,), output="reference-ok: done") as stub:
        configure(monkeypatch, tmp_path, stub.url)
        executor = OllamaReferenceExecutor(model=MODEL, execution_profile="live")
        cases, issues = load_all_cases(Path("tests/reference/cases"))
        assert not issues
        summary = run_cases([cases[0]], executor)
        assert summary.passed == 1
        assert summary.live_passed == 1
        assert run_cases([cases[0]], FakeExecutor()).live_passed == 0
        requests = [request["body"] for request in stub.requests if request["path"] == "/api/chat"]
        assert len(requests) == 1
        assert requests[0]["model"] == MODEL + ":local"
        assert requests[0]["messages"][0]["role"] == "user"
        assert "images" not in requests[0]["messages"][0]


def test_live_reference_privacy_gate_never_counts_model_evidence(monkeypatch, tmp_path):
    with OllamaStub(models=(MODEL,)) as stub:
        configure(monkeypatch, tmp_path, stub.url)
        executor = OllamaReferenceExecutor(model=MODEL, execution_profile="live")
        cases, _ = load_all_cases(Path("tests/reference/cases"))
        trap = next(case for case in cases if case.case.id.startswith("011"))
        result = executor.execute(TaskV1.model_validate(trap.task_payload))
        assert result.status == ResultStatus.BLOCKED
        summary = run_cases([trap], executor)
        assert summary.passed == 1
        assert summary.live_passed == 0
        assert stub.requests == []


def test_live_provenance_preserves_transport_privacy_checks(monkeypatch, tmp_path):
    with OllamaStub(models=(MODEL,), output="reference-ok: done") as stub:
        configure(monkeypatch, tmp_path, stub.url)
        executor = OllamaReferenceExecutor(model=MODEL, execution_profile="live")
        cases, issues = load_all_cases(Path("tests/reference/cases"))
        assert not issues
        task = TaskV1.model_validate(cases[0].task_payload).model_copy(
            update={"instructions": "Echo boundaryproofmarker."}
        )
        result = executor.execute(task)
        request = next(r["body"] for r in stub.requests if r["path"] == "/api/chat")
        assert result.execution_profile == "live"
        assert result.transport_text == request["messages"][0]["content"]
        assert "boundaryproofmarker" not in result.output_text
        expectation = cases[0].case.expect.model_copy(
            update={"must_not_contain": ["boundaryproofmarker"]}
        )
        assert any(
            check.rule == "must_not_contain" for check in evaluate_expect(expectation, result)
        )


@pytest.mark.parametrize(
    ("case_prefix", "output", "expected"),
    [
        ("011", "reference-ok: done", 1),
        ("008", "reference-ok: done", 1),
        ("001", "reference-ok: done", 0),
        ("001", "unmatched", 1),
    ],
)
def test_live_reference_cli_requires_successful_model_case(
    monkeypatch, tmp_path, case_prefix, output, expected
):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    source = next(Path("tests/reference/cases").glob(case_prefix + "-*.yaml"))
    (cases_dir / source.name).write_bytes(source.read_bytes())
    with OllamaStub(models=(MODEL,), output=output) as stub:
        configure(monkeypatch, tmp_path, stub.url)
        assert (
            main(["--executor", "ollama-live", "--model", MODEL, "--cases-dir", str(cases_dir)])
            == expected
        )
    if case_prefix != "001":
        assert not any(r["path"] == "/api/chat" for r in stub.requests)


def test_simulated_reference_cli_does_not_require_live_evidence(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    source = next(Path("tests/reference/cases").glob("011-*.yaml"))
    (cases_dir / source.name).write_bytes(source.read_bytes())
    assert main(["--cases-dir", str(cases_dir)]) == 0


def test_live_reference_uses_lane_context_with_missing_sibling(monkeypatch, tmp_path):
    with OllamaStub(
        models=(MODEL,),
        show_extra={"model_info": {"fixture.context_length": 8192}},
        output="reference-ok: done",
    ) as stub:
        configure(monkeypatch, tmp_path, stub.url)
        (tmp_path / "router.yaml").write_text(
            json.dumps(
                {
                    "lanes": {
                        "ollama": {
                            "num_ctx": 2048,
                            "models": {"default": MODEL, "classify": "missing:tag"},
                        }
                    }
                }
            )
        )
        executor = OllamaReferenceExecutor(model=MODEL, execution_profile="live")
        cases, _ = load_all_cases(Path("tests/reference/cases"))
        assert run_cases([cases[0]], executor).live_passed == 1
        chat = next(r["body"] for r in stub.requests if r["path"] == "/api/chat")
        assert chat["options"]["num_ctx"] == 2048
