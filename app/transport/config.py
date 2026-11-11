"""Sender configuration. The routine key never appears in repr or errors."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_BEARER = re.compile(r"\ABearer [!-~]{1,512}\Z")
_KEY = re.compile(r"\A[!-~]{1,512}\Z")


class TransportConfigError(RuntimeError):
    """The wake switch is on but the sender configuration is unusable."""

    def __init__(self) -> None:
        super().__init__("Routine wake configuration is unavailable or invalid.")


@dataclass(frozen=True)
class WakeConfig:
    """Enabled only when the owner turns the switch on and supplies a sender."""

    enabled: bool
    url: str
    key: str = field(repr=False, default="")

    def authorization_value(self) -> str:
        if not self.enabled or not _KEY.fullmatch(self.key):
            raise TransportConfigError()
        return f"Bearer {self.key}"


def _sender_key(raw: str) -> str:
    value = raw.strip()
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        if not _KEY.fullmatch(token):
            raise TransportConfigError()
        return token
    if not _KEY.fullmatch(value):
        raise TransportConfigError()
    return value


def _sender_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or not parts.hostname
        or parts.fragment
    ):
        raise TransportConfigError()
    return raw.strip()


def wake_enabled() -> bool:
    """The switch is off unless it is exactly ``1``."""
    return os.environ.get("DECK_ROUTINE_WAKE", "") == "1"


def wake_config_from_env() -> WakeConfig:
    """Read the opt-in switch. Anything other than ``1`` stays disabled."""
    if not wake_enabled():
        return WakeConfig(enabled=False, url="")
    try:
        url = _sender_url(os.environ.get("DECK_ROUTINE_WAKE_URL", ""))
        key = _sender_key(os.environ.get("DECK_ROUTINE_WAKE_AUTHORIZATION", ""))
    except TransportConfigError:
        raise
    header = f"Bearer {key}"
    if not _BEARER.fullmatch(header):
        raise TransportConfigError()
    return WakeConfig(enabled=True, url=url, key=key)
