"""Use explicit local HTTP deployment configuration in every isolated test process."""

from __future__ import annotations

import ipaddress

import pytest


@pytest.fixture(autouse=True)
def isolated_http_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_BIND_HOST", str(ipaddress.IPv4Address(0x7F000001)))
    monkeypatch.setenv("DECK_ALLOWED_HOSTS", "testserver,localhost")
    monkeypatch.delenv("DECK_ENV_FILE", raising=False)
    monkeypatch.delenv("DECK_PUBLIC_ORIGIN", raising=False)
