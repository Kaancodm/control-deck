"""Canary submission and read-only inspection exercise the HTTP stub and persisted evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Event, Task
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.ollama_lane import OllamaLaneRunner
from scripts.live_canary import check_canary, run_canary
from tests.support.ollama_stub import OllamaStub


@pytest.mark.parametrize("profile", ["live", "simulation"])
def test_canary_requires_live_evidence(db_session, tmp_path, monkeypatch, profile):
    model = "canary-fixture:configured"
    router = tmp_path / "router.yaml"
    router.write_text(json.dumps({"lanes": {"ollama": {"models": {"default": model}}}}))
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_ROUTER_FILE", str(router))
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    task_id = uuid4()
    runs_root = tmp_path / "runs"
    with OllamaStub(models=(model,)) as stub:
        monkeypatch.setenv("DECK_OLLAMA_URL", stub.url)
        executor = (
            OllamaExecutor(profile="live", url=stub.url)
            if profile == "live"
            else OllamaExecutor(
                profile="simulation",
                simulation=OllamaSimulation(models=(model,), output_text="CANARY-OK"),
            )
        )
        runner = OllamaLaneRunner(factory, runs_root=runs_root, executor=executor)
        original_check = check_canary

        def drive_runner(*args, **kwargs):
            outcome = runner.run_once()
            assert outcome.action in {"completed", "idle"}
            return original_check(*args, **kwargs)

        monkeypatch.setattr("scripts.live_canary.check_canary", drive_runner)
        with ThreadPoolExecutor(max_workers=1) as pool:
            passed = pool.submit(
                run_canary,
                factory,
                task_id=task_id,
                runs_root=runs_root,
                submit=True,
                timeout_s=10,
            ).result(timeout=15)
        assert passed is (profile == "live")
        db_session.expire_all()
        assert db_session.get(Task, task_id).status.value == "review"
        before = db_session.scalar(select(func.count()).select_from(Event))
        assert original_check(factory, task_id=task_id, runs_root=runs_root) is passed
        assert db_session.scalar(select(func.count()).select_from(Event)) == before
        assert not any(request["path"] == "/api/pull" for request in stub.requests)
