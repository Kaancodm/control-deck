"""The launcher validates configuration before calling an injected server, never a listener."""

from __future__ import annotations

import ipaddress

import pytest
from sqlalchemy.exc import OperationalError

from app.api.server_main import main
from app.db.config import DatabaseConfigurationError


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_BIND_HOST", str(ipaddress.IPv4Address(0x7F000001)))
    monkeypatch.setenv("DECK_ALLOWED_HOSTS", "testserver")


def no_startup() -> None:
    return None


def test_validated_server_disables_proxy_and_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    calls = []
    assert main([], serve=lambda **kwargs: calls.append(kwargs), startup=no_startup) == 0
    assert len(calls) == 1
    options = calls[0]
    assert not options["proxy_headers"]
    assert options["forwarded_allow_ips"] == ""
    assert not options["access_log"]
    assert options["limit_concurrency"] == 32
    assert options["factory"]
    assert options["app"] == "app.api.app:create_app"


def test_restart_releases_login_lock_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    order: list[str] = []
    assert (
        main(
            [],
            serve=lambda **kwargs: order.append("serve"),
            startup=lambda: order.append("startup"),
        )
        == 0
    )
    assert order == ["startup", "serve"]


@pytest.mark.parametrize(
    "failure",
    [
        DatabaseConfigurationError("synthetic-private-database-location"),
        OperationalError("SELECT synthetic", {}, Exception("synthetic-private-dsn")),
    ],
)
def test_failed_lock_release_never_starts_server(
    monkeypatch: pytest.MonkeyPatch, capsys, failure: Exception
) -> None:
    configure(monkeypatch)

    def failing_startup() -> None:
        raise failure

    assert (
        main([], serve=lambda **kwargs: pytest.fail("Listener started"), startup=failing_startup)
        == 2
    )
    captured = capsys.readouterr().err
    assert "HTTP startup rejected: login_lock_release_failed" in captured
    assert "synthetic-private" not in captured


def test_check_only_never_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    assert (
        main(
            ["--check-only"],
            serve=lambda **kwargs: pytest.fail("Listener started"),
            startup=lambda: pytest.fail("Database opened"),
        )
        == 0
    )


def test_missing_binding_and_wildcard_never_start(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("DECK_BIND_HOST", raising=False)

    def serve(**kwargs: object) -> None:
        pytest.fail("Listener started")

    def startup() -> None:
        pytest.fail("Database opened")

    assert main([], serve=serve, startup=startup) == 2
    monkeypatch.setenv("DECK_BIND_HOST", str(ipaddress.IPv4Address(0)))
    assert main([], serve=serve, startup=startup) == 2
    assert "HTTP startup rejected" in capsys.readouterr().err
