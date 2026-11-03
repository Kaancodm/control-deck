"""Worker credentials identify exactly one configured lane without a shared fallback."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.config import ApiConfigurationError, get_worker_tokens
from app.api.deps import require_owner, require_worker_token
from app.core.schemas import Worker


@pytest.fixture(autouse=True)
def clear_auth_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DECK_OWNER_TOKEN",
        "DECK_WORKER_TOKEN",
        *(f"DECK_WORKER_TOKEN_{worker.value.upper()}" for worker in Worker),
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("worker", list(Worker))
@pytest.mark.parametrize("header", ["bearer", "dedicated", "both"])
def test_token_identifies_lane(
    monkeypatch: pytest.MonkeyPatch, worker: Worker, header: str
) -> None:
    for lane in Worker:
        monkeypatch.setenv(f"DECK_WORKER_TOKEN_{lane.value.upper()}", f"synthetic-{lane.value}")
    token = f"synthetic-{worker.value}"
    assert (
        require_worker_token(
            authorization=f"Bearer {token}" if header != "dedicated" else None,
            x_deck_worker_token=token if header != "bearer" else None,
        )
        is worker
    )


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"DECK_WORKER_TOKEN": "synthetic-legacy"},
        {"DECK_WORKER_TOKEN_OLLAMA": " "},
        {
            "DECK_WORKER_TOKEN_OLLAMA": "synthetic-same",
            "DECK_WORKER_TOKEN_CLAUDE": "synthetic-same",
        },
        {
            "DECK_WORKER_TOKEN_OLLAMA": " synthetic-same ",
            "DECK_WORKER_TOKEN_CODEX": "synthetic-same",
        },
        {"DECK_WORKER_TOKEN_OLLAMA": "synthetic-same", "DECK_OWNER_TOKEN": "synthetic-same"},
    ],
)
def test_ambiguous_or_missing_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch, settings: dict[str, str]
) -> None:
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ApiConfigurationError):
        get_worker_tokens()
    with pytest.raises(HTTPException) as caught:
        require_worker_token(authorization="Bearer synthetic-legacy")
    assert caught.value.status_code == 503
    assert "synthetic" not in caught.value.detail


@pytest.mark.parametrize(
    ("authorization", "dedicated"),
    [
        (None, None),
        ("Bearer synthetic-unknown", None),
        ("Bearer synthetic-ollama", "synthetic-claude"),
        ("Basic synthetic-ollama", "synthetic-ollama"),
        ("Bearer ", "synthetic-ollama"),
        ("Bearer synthetic-ollama", ""),
        ("Bearer synthetisch-ungültig", None),
    ],
)
def test_invalid_or_conflicting_credentials_fail_closed(
    monkeypatch: pytest.MonkeyPatch, authorization: str | None, dedicated: str | None
) -> None:
    monkeypatch.setenv("DECK_WORKER_TOKEN_OLLAMA", "synthetic-ollama")
    monkeypatch.setenv("DECK_WORKER_TOKEN_CLAUDE", "synthetic-claude")
    with pytest.raises(HTTPException) as caught:
        require_worker_token(authorization=authorization, x_deck_worker_token=dedicated)
    assert caught.value.status_code == 401
    assert "synthetic" not in caught.value.detail


def test_unconfigured_lane_and_shared_token_do_not_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECK_WORKER_TOKEN_OLLAMA", "synthetic-ollama")
    monkeypatch.setenv("DECK_WORKER_TOKEN", "synthetic-legacy")
    assert get_worker_tokens() == {Worker.OLLAMA: "synthetic-ollama"}
    for token in ("synthetic-claude", "synthetic-legacy"):
        with pytest.raises(HTTPException) as caught:
            require_worker_token(authorization=f"Bearer {token}")
        assert caught.value.status_code == 401


@pytest.mark.parametrize("collision", ["owner", "lanes"])
def test_ambiguous_lane_configuration_also_blocks_owner_auth(
    monkeypatch: pytest.MonkeyPatch, collision: str
) -> None:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "synthetic-owner")
    monkeypatch.setenv("DECK_WORKER_TOKEN_OLLAMA", "synthetic-worker")
    if collision == "owner":
        monkeypatch.setenv("DECK_WORKER_TOKEN_CLAUDE", "synthetic-owner")
    else:
        monkeypatch.setenv("DECK_WORKER_TOKEN_CLAUDE", "synthetic-worker")
    with pytest.raises(HTTPException) as caught:
        require_owner(authorization="Bearer synthetic-owner")
    assert caught.value.status_code == 503
    assert "synthetic" not in caught.value.detail


def test_owner_only_configuration_can_authenticate_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "synthetic-owner")
    require_owner(authorization="Bearer synthetic-owner")
