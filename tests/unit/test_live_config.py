"""Live activation remains explicit and accepts only literal loopback URLs."""

from __future__ import annotations

import ipaddress

import pytest

from app.core.schemas import Worker
from app.workers.live_config import (
    LiveConfigurationError,
    live_enabled,
    load_claude_live_config,
    load_live_config,
    validate_ollama_url,
)
from app.workers.sandbox import SIMULATED_CLAUDE_IMAGE

_IMAGE = "deck-lane-claude@sha256:" + "ab" * 32
_DOCKER_HOST = "unix:///run/deck/docker.sock"
_OTHER_DIGEST = "cd" * 32


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


def _claude_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "DECK_LIVE_ENABLED": "1",
        "DECK_CLAUDE_LIVE_ENABLED": "1",
        "DECK_CLAUDE_IMAGE": _IMAGE,
        "DECK_DOCKER_HOST": _DOCKER_HOST,
    }
    environment.update(overrides)
    return environment


def test_ollama_flag_alone_does_not_enable_claude_live():
    environment = _claude_environment()
    del environment["DECK_CLAUDE_LIVE_ENABLED"]
    with pytest.raises(LiveConfigurationError, match="^claude_live_disabled$"):
        load_claude_live_config(environment)


def test_claude_flag_alone_does_not_enable_claude_live():
    environment = _claude_environment()
    del environment["DECK_LIVE_ENABLED"]
    with pytest.raises(LiveConfigurationError, match="^live_disabled$"):
        load_claude_live_config(environment)


@pytest.mark.parametrize("value", ["", "0", "false", "TRUE", "yes", " 1", "1 ", "true\n"])
def test_claude_live_flag_rejects_inexact_values(value: str):
    with pytest.raises(LiveConfigurationError, match="^claude_live_disabled$"):
        load_claude_live_config(_claude_environment(DECK_CLAUDE_LIVE_ENABLED=value))


@pytest.mark.parametrize("value", ["", "0", "false", "TRUE", "yes", " 1", "1 ", "true\n"])
def test_shared_live_flag_is_still_required_for_claude(value: str):
    with pytest.raises(LiveConfigurationError, match="^live_disabled$"):
        load_claude_live_config(_claude_environment(DECK_LIVE_ENABLED=value))


@pytest.mark.parametrize(
    "image",
    [
        "",
        "deck-lane-claude",
        "deck-lane-claude:latest",
        "deck-lane-claude@sha256:",
        "deck-lane-claude@sha256:" + "ab" * 31,
        "deck-lane-claude@sha256:" + "ab" * 32 + "a",
        "deck-lane-claude@sha256:" + "AB" * 32,
        "other-image@sha256:" + _OTHER_DIGEST,
        "public/deck-lane-claude@sha256:" + _OTHER_DIGEST,
        " deck-lane-claude@sha256:" + _OTHER_DIGEST,
        "deck-lane-claude@sha256:" + _OTHER_DIGEST + "\n",
        SIMULATED_CLAUDE_IMAGE,
    ],
)
def test_claude_image_must_be_the_pinned_name_and_digest(image: str):
    with pytest.raises(LiveConfigurationError) as error:
        load_claude_live_config(_claude_environment(DECK_CLAUDE_IMAGE=image))
    assert error.value.code == "claude_image_invalid"
    assert str(error.value) == "claude_image_invalid"
    if image:
        assert image not in str(error.value)


def test_missing_claude_image_is_invalid():
    environment = _claude_environment()
    del environment["DECK_CLAUDE_IMAGE"]
    with pytest.raises(LiveConfigurationError, match="^claude_image_invalid$"):
        load_claude_live_config(environment)


@pytest.mark.parametrize(
    "docker_host",
    [
        "",
        "tcp://127.0.0.1:2375",
        "tcp://:2375",
        "http://127.0.0.1:2375",
        "unix://run/deck/docker.sock",
        "unix:///run/../docker.sock",
        "unix:///./docker.sock",
        "unix:////run/deck/docker.sock",
        "unix:///run/deck/docker.sock/",
        "unix:///run/deck/docker.sock?x=1",
        "unix:///run/deck/docker.sock#frag",
        " unix:///run/deck/docker.sock",
        "unix:///run/deck/docker.sock\n",
        "UNIX:///run/deck/docker.sock",
        "fd://1",
        "unix://" + ("/deck" * 40),
    ],
)
def test_docker_host_must_be_an_absolute_unix_socket(docker_host: str):
    with pytest.raises(LiveConfigurationError) as error:
        load_claude_live_config(_claude_environment(DECK_DOCKER_HOST=docker_host))
    assert error.value.code == "docker_host_invalid"
    assert str(error.value) == "docker_host_invalid"
    if docker_host:
        assert docker_host not in str(error.value)


def test_invalid_image_is_reported_before_the_docker_host():
    with pytest.raises(LiveConfigurationError, match="^claude_image_invalid$"):
        load_claude_live_config(
            _claude_environment(
                DECK_CLAUDE_IMAGE="deck-lane-claude",
                DECK_DOCKER_HOST="tcp://127.0.0.1:2375",
            )
        )


@pytest.mark.parametrize("live_flag", ["1", "true"])
@pytest.mark.parametrize("claude_flag", ["1", "true"])
def test_claude_live_config_accepts_exact_flags_image_and_socket(live_flag: str, claude_flag: str):
    config = load_claude_live_config(
        _claude_environment(
            DECK_LIVE_ENABLED=live_flag,
            DECK_CLAUDE_LIVE_ENABLED=claude_flag,
        )
    )
    assert config.image == _IMAGE
    assert config.docker_host == _DOCKER_HOST


def test_accepted_claude_settings_do_not_open_load_live_config():
    environment = _claude_environment()
    with pytest.raises(LiveConfigurationError, match="^live_lane_not_supported$"):
        load_live_config(Worker.CLAUDE, environment)
    with pytest.raises(LiveConfigurationError, match="^live_lane_not_supported$"):
        load_live_config(Worker.CODEX, environment)
