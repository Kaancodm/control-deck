"""Configuration errors fail closed without exposing local contents."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.schemas import Worker
from app.routing.config import (
    RouterConfig,
    RouterConfigurationError,
    get_router_config,
    load_router_config,
)


def test_partial_lanes_keep_defaults() -> None:
    config = RouterConfig.model_validate({"lanes": {"ollama": {"daily_attempt_limit": 0}}})
    assert set(config.lanes) == set(Worker)
    assert config.lanes[Worker.OLLAMA].daily_attempt_limit == 0
    assert config.lanes[Worker.CLAUDE].enabled
    assert config.lanes[Worker.CODEX].models == {}


@pytest.mark.parametrize(
    "body",
    [
        {"version": True},
        {"version": 2},
        {"version": "1"},
        {"other": 1},
        {"lanes": {"unknown": {}}},
        {"lanes": {"ollama": {"enabled": "true"}}},
        {"lanes": {"ollama": {"daily_attempt_limit": True}}},
        {"lanes": {"ollama": {"daily_attempt_limit": -1}}},
        {"lanes": {"ollama": {"daily_attempt_limit": 1.5}}},
        {"lanes": {"ollama": {"daily_attempt_limit": "2"}}},
        {"lanes": {"ollama": {"models": {"unknown": "fixture"}}}},
        {"lanes": {"ollama": {"models": {"default": ""}}}},
        {"lanes": {"ollama": {"models": {"default": "fixture\nvalue"}}}},
        {"lanes": {"ollama": {"models": {"default": 1}}}},
        {"rules": [{"id": "duplicate"}, {"id": "duplicate"}]},
        {"rules": [{"id": "unsafe value"}]},
        {"rules": [{"id": "x" * 65}]},
        {"rules": [{"id": "r", "kinds": ["code", "code"]}]},
        {"rules": [{"id": "r", "allowed_workers": ["ollama", "ollama"]}]},
        {"rules": [{"id": "r", "data_classes": ["local_only", "local_only"]}]},
        {"rules": [{"id": "r", "kinds": ["unknown"]}]},
    ],
)
def test_invalid_configuration_rejected(body: dict) -> None:
    with pytest.raises(ValidationError):
        RouterConfig.model_validate(body)


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\nversion: 1\n",
        "lanes:\n  ollama:\n    enabled: true\n    enabled: false\n",
        "version: 1\nrules: &rules []\nlanes: *rules\n",
        "version: 1\nlanes:\n  ollama:\n    <<: {enabled: false}\n",
        "!!python/object/apply:os.system ['not-executed']",
        "42: invalid-key\n",
        "[]",
        "null",
        "",
        "version: [",
        "version: 1\nunknown: " + "[" * 40 + "0" + "]" * 40,
        "? !!str [invalid, key]\n: value\n",
    ],
)
def test_yaml_is_strict_and_error_is_safe(tmp_path: Path, text: str) -> None:
    path = tmp_path / "private-routing.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RouterConfigurationError) as caught:
        load_router_config(path)
    assert str(caught.value) == "Routing configuration is unavailable or invalid."
    assert str(path) not in repr(caught.value)


@pytest.mark.parametrize("content", [b"x" * 131073, b"\xff"])
def test_oversize_and_invalid_utf8_rejected(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "router.yaml"
    path.write_bytes(content)
    with pytest.raises(RouterConfigurationError):
        load_router_config(path)


def test_special_files_and_symlinks_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "router.yaml"
    path.write_text("version: 1\n", encoding="utf-8")
    linked = tmp_path / "link.yaml"
    linked.symlink_to(path)
    for candidate in (tmp_path, linked, tmp_path / "missing"):
        with pytest.raises(RouterConfigurationError):
            load_router_config(candidate)


def test_explicit_missing_file_does_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECK_ROUTER_FILE", str(tmp_path / "missing"))
    with pytest.raises(RouterConfigurationError):
        get_router_config()
    monkeypatch.setenv("DECK_ROUTER_FILE", " ")
    with pytest.raises(RouterConfigurationError):
        get_router_config()


def test_configuration_reloaded_without_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "router.yaml"
    monkeypatch.setenv("DECK_ROUTER_FILE", str(path))
    path.write_text("version: 1\n", encoding="utf-8")
    assert get_router_config().lanes[Worker.OLLAMA].enabled
    path.write_text("lanes:\n  ollama:\n    enabled: false\n", encoding="utf-8")
    assert not get_router_config().lanes[Worker.OLLAMA].enabled


def test_shipped_config_has_no_actual_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECK_ROUTER_FILE", raising=False)
    config = get_router_config()
    assert all(lane.models == {} for lane in config.lanes.values())
