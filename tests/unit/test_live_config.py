"""Live activation remains explicit and accepts only literal loopback URLs."""

from __future__ import annotations

import ipaddress

import pytest

from app.core.schemas import Worker
from app.workers.live_config import (
    LiveConfigurationError,
    live_enabled,
    load_live_config,
    validate_ollama_url,
)


@pytest.mark.parametrize("value", [None, "", "0", "false", "TRUE", "yes", " 1", "1 ", "true\n"])
def test_only_exact_flag_values_enable_live(value):
    environment = {} if value is None else {"DECK_LIVE_ENABLED": value}
    assert not live_enabled(environment)
    with pytest.raises(LiveConfigurationError, match="^live_disabled$"):
        load_live_config(Worker.OLLAMA, environment)


@pytest.mark.parametrize("value", ["1", "true"])
def test_exact_flag_values_require_explicit_endpoint(value):
    environment = {"DECK_LIVE_ENABLED": value}
    assert live_enabled(environment)
    with pytest.raises(LiveConfigurationError, match="^live_url_invalid$"):
        load_live_config(Worker.OLLAMA, environment)


@pytest.mark.parametrize("worker", [Worker.CLAUDE, Worker.CODEX])
def test_unsupported_live_lane_precedes_url_validation(worker):
    with pytest.raises(LiveConfigurationError, match="^live_lane_not_supported$"):
        load_live_config(worker, {"DECK_LIVE_ENABLED": "1"})


@pytest.mark.parametrize(
    "url, host, port",
    [
        ("http://127.0.0.1:1", "127.0.0.1", 1),
        ("http://[::1]:65535", "::1", 65535),
    ],
)
def test_explicit_loopback_endpoint(url, host, port):
    endpoint = validate_ollama_url(url)
    assert (endpoint.host, endpoint.port, endpoint.url) == (host, port, url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://localhost:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:-1",
        "http://127.0.0.1:abc",
        "http://127.0.0.1:11434/",
        "http://127.0.0.1:11434/api/chat",
        "http://127.0.0.1:11434?",
        "http://127.0.0.1:11434#",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1:11434#x",
        "http://user@127.0.0.1:11434",
        "http://:password@127.0.0.1:11434",
        " http://127.0.0.1:11434",
        "http://127.0.0.1:11434\n",
        "http://127.0.0.1:\t11434",
        "http://[::1%lo]:11434",
        "http://::1:11434",
        "http://127.1:11434",
        "http://2130706433:11434",
        "HTTP://127.0.0.1:11434",
    ],
)
def test_unsafe_or_ambiguous_url_is_rejected_without_echo(url):
    with pytest.raises(LiveConfigurationError) as error:
        validate_ollama_url(url)
    assert error.value.code == "live_url_invalid"
    assert str(error.value) == "live_url_invalid"


def test_other_addresses_are_rejected_even_when_loopback():
    for address in [ipaddress.ip_address(1), ipaddress.ip_address(0x7F000002)]:
        with pytest.raises(LiveConfigurationError, match="^live_url_invalid$"):
            validate_ollama_url(f"http://{address}:11434")
