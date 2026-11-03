"""Fail-closed HTTP binding and private configuration validation."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class DeploymentConfigurationError(ValueError):
    """Configuration errors contain safe codes, never configured values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"HTTP deployment configuration is unavailable ({code}).")


def parse_address(value: str) -> Address:
    if not value or value != value.strip() or "%" in value:
        raise DeploymentConfigurationError("invalid_bind_address")
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        raise DeploymentConfigurationError("invalid_bind_address") from None


def normalize_host(value: str) -> str:
    """Accept an explicit ASCII hostname or IP literal, without ports or wildcards."""
    if not value or len(value) > 253 or not value.isascii() or value != value.strip():
        raise DeploymentConfigurationError("invalid_allowed_host")
    host = value.lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return str(parse_address(host))
    except DeploymentConfigurationError:
        pass
    if any(_DNS_LABEL.fullmatch(label) is None for label in host.split(".")):
        raise DeploymentConfigurationError("invalid_allowed_host")
    return host


def tailnet_addresses() -> frozenset[Address]:
    """Read only the local Tailnet interface; never resolve or contact a remote host."""
    try:
        process = subprocess.run(
            ["ip", "-j", "address", "show", "dev", "tailscale0"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=2,
        )
        if len(process.stdout) > 65536:
            raise ValueError
        payload = json.loads(process.stdout)
        if not isinstance(payload, list):
            raise ValueError
        addresses = set()
        for interface in payload:
            if not isinstance(interface, dict) or interface.get("ifname") != "tailscale0":
                raise ValueError
            for info in interface.get("addr_info", []):
                if not isinstance(info, dict) or not isinstance(info.get("local"), str):
                    raise ValueError
                addresses.add(parse_address(info["local"]))
        return frozenset(addresses)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise DeploymentConfigurationError("tailnet_interface_unavailable") from None


def validate_bind_address(
    value: str,
    *,
    interface_addresses: Callable[[], frozenset[Address]] = tailnet_addresses,
) -> Address:
    address = parse_address(value)
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    effective = mapped or address
    if effective.is_unspecified or effective.is_multicast:
        raise DeploymentConfigurationError("unsafe_bind_address")
    if effective.is_loopback:
        return address
    if address not in interface_addresses():
        raise DeploymentConfigurationError("bind_address_not_on_tailnet")
    return address


def validate_private_config(path: Path, *, owner_uid: int | None = None) -> None:
    """Check the opened file rather than trusting a prior path stat; never read its bytes."""
    uid = os.getuid() if owner_uid is None else owner_uid
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise DeploymentConfigurationError("private_config_permissions")
        finally:
            os.close(descriptor)
    except OSError:
        raise DeploymentConfigurationError("private_config_unavailable") from None


@dataclass(frozen=True)
class DeploymentSettings:
    host: str
    port: int
    allowed_hosts: tuple[str, ...]
    cookie_secure: bool


def load_deployment_settings(
    environment: Mapping[str, str] | None = None,
    *,
    interface_addresses: Callable[[], frozenset[Address]] = tailnet_addresses,
) -> DeploymentSettings:
    env = os.environ if environment is None else environment
    address = validate_bind_address(
        env.get("DECK_BIND_HOST", ""), interface_addresses=interface_addresses
    )
    raw_hosts = env.get("DECK_ALLOWED_HOSTS", "")
    if not raw_hosts:
        raise DeploymentConfigurationError("allowed_hosts_required")
    hosts = tuple(dict.fromkeys(normalize_host(value.strip()) for value in raw_hosts.split(",")))
    if not 1 <= len(hosts) <= 16:
        raise DeploymentConfigurationError("invalid_allowed_host_count")
    try:
        port = int(env.get("DECK_BIND_PORT", "8000"))
    except ValueError:
        raise DeploymentConfigurationError("invalid_bind_port") from None
    if not 1 <= port <= 65535:
        raise DeploymentConfigurationError("invalid_bind_port")
    secure = env.get("DECK_UI_COOKIE_SECURE", "true").lower()
    if secure not in {"true", "false"}:
        raise DeploymentConfigurationError("invalid_cookie_security")
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    if secure == "false" and not (mapped or address).is_loopback:
        raise DeploymentConfigurationError("insecure_nonlocal_cookie")
    if config_file := env.get("DECK_ENV_FILE"):
        validate_private_config(Path(config_file))
    return DeploymentSettings(str(address), port, hosts, secure == "true")
