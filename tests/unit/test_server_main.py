"""The launcher validates configuration before calling an injected server, never a listener."""

from __future__ import annotations

import ipaddress

import pytest

from app.api.server_main import main


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_BIND_HOST", str(ipaddress.IPv4Address(0x7F000001)))
    monkeypatch.setenv("DECK_ALLOWED_HOSTS", "testserver")


def test_validated_server_disables_proxy_and_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    calls = []
    assert main([], serve=lambda **kwargs: calls.append(kwargs)) == 0
    assert len(calls) == 1
    options = calls[0]
    assert not options["proxy_headers"]
    assert options["forwarded_allow_ips"] == ""
    assert not options["access_log"]
    assert options["limit_concurrency"] == 32
    assert options["factory"]
    assert options["app"] == "app.api.app:create_app"


def test_check_only_never_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    assert main(["--check-only"], serve=lambda **kwargs: pytest.fail("Listener started")) == 0


def test_missing_binding_and_wildcard_never_start(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("DECK_BIND_HOST", raising=False)
    assert main([], serve=lambda **kwargs: pytest.fail("Listener started")) == 2
    monkeypatch.setenv("DECK_BIND_HOST", str(ipaddress.IPv4Address(0)))
    assert main([], serve=lambda **kwargs: pytest.fail("Listener started")) == 2
    assert "HTTP startup rejected" in capsys.readouterr().err
