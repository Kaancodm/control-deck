"""Deployment checks do not connect to a host or read private configuration contents."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

import pytest

from app.core.bind import (
    DeploymentConfigurationError,
    load_deployment_settings,
    normalize_host,
    validate_bind_address,
    validate_private_config,
)

LOOPBACK = str(ipaddress.IPv4Address(0x7F000001))
TAILNET_STUB = str(ipaddress.IPv4Address(0x64400001))


def environment(**values: str) -> dict[str, str]:
    return {"DECK_BIND_HOST": LOOPBACK, "DECK_ALLOWED_HOSTS": "testserver", **values}


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "testserver",
        "*",
        str(ipaddress.IPv4Address(0)),
        str(ipaddress.IPv6Address(0)),
        "::%scope",
    ],
)
def test_invalid_binding_fails_without_interface_lookup(value: str) -> None:
    with pytest.raises(DeploymentConfigurationError):
        validate_bind_address(
            value, interface_addresses=lambda: pytest.fail("Unexpected interface read")
        )


def test_loopback_never_queries_interfaces() -> None:
    assert (
        str(
            validate_bind_address(
                LOOPBACK, interface_addresses=lambda: pytest.fail("Unexpected interface read")
            )
        )
        == LOOPBACK
    )


def test_nonlocal_binding_must_match_actual_tailnet_interface() -> None:
    address = ipaddress.ip_address(TAILNET_STUB)
    assert (
        validate_bind_address(TAILNET_STUB, interface_addresses=lambda: frozenset({address}))
        == address
    )
    with pytest.raises(DeploymentConfigurationError, match="not_on_tailnet"):
        validate_bind_address(TAILNET_STUB, interface_addresses=frozenset)


@pytest.mark.parametrize(
    "host",
    [
        "*",
        "*.invalid",
        "bad/name",
        "name:8000",
        "user@name",
        "name..invalid",
        "name,other",
        " name",
        "name.",
        "é.invalid",
        "-name",
        "name-",
    ],
)
def test_allowed_hosts_are_explicit_and_unambiguous(host: str) -> None:
    with pytest.raises(DeploymentConfigurationError):
        normalize_host(host)


def test_deployment_requires_hosts_and_bounds_ports() -> None:
    with pytest.raises(DeploymentConfigurationError, match="allowed_hosts_required"):
        load_deployment_settings(environment(DECK_ALLOWED_HOSTS=""))
    for port in ("0", "65536", "not-a-port"):
        with pytest.raises(DeploymentConfigurationError, match="invalid_bind_port"):
            load_deployment_settings(environment(DECK_BIND_PORT=port))
    settings = load_deployment_settings(environment(DECK_ALLOWED_HOSTS="TestServer,testserver"))
    assert settings.allowed_hosts == ("testserver",)
    assert settings.cookie_secure


def test_insecure_cookie_requires_loopback() -> None:
    assert not load_deployment_settings(environment(DECK_UI_COOKIE_SECURE="false")).cookie_secure
    with pytest.raises(DeploymentConfigurationError, match="insecure_nonlocal_cookie"):
        load_deployment_settings(
            environment(DECK_BIND_HOST=TAILNET_STUB, DECK_UI_COOKIE_SECURE="false"),
            interface_addresses=lambda: frozenset({ipaddress.ip_address(TAILNET_STUB)}),
        )


def test_private_config_regular_single_link_owned_and_mode600(tmp_path: Path) -> None:
    path = tmp_path / "private"
    path.write_bytes(b"synthetic fixture; never loaded")
    path.chmod(0o600)
    validate_private_config(path)
    with pytest.raises(DeploymentConfigurationError, match="permissions"):
        validate_private_config(path, owner_uid=os.getuid() + 1)
    path.chmod(0o640)
    with pytest.raises(DeploymentConfigurationError, match="permissions"):
        validate_private_config(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(DeploymentConfigurationError, match="unavailable"):
        validate_private_config(link)
    link.unlink()
    os.link(path, link)
    with pytest.raises(DeploymentConfigurationError, match="permissions"):
        validate_private_config(path)


def test_config_errors_do_not_echo_values(tmp_path: Path) -> None:
    private_path = tmp_path / "sensitive-location"
    with pytest.raises(DeploymentConfigurationError) as caught:
        load_deployment_settings(environment(DECK_ENV_FILE=str(private_path)))
    assert str(private_path) not in str(caught.value)
