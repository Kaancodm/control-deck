"""Load bounded local privacy configuration without exposing its contents."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_BLOCKLIST_BYTES = 65536


class PrivacyConfigurationError(RuntimeError):
    """A safe configuration error that never includes a local path or literal."""

    def __init__(self, code: str = "blocklist_unavailable") -> None:
        self.code = code
        super().__init__("Privacy configuration is unavailable or invalid.")


@dataclass(frozen=True)
class PrivacySettings:
    inbox: Path = field(repr=False)
    blocklist: tuple[str, ...] = field(default=(), repr=False)
    blocklist_configured: bool = False


def _load_blocklist(path: Path) -> tuple[str, ...]:
    try:
        if path.resolve(strict=True).is_relative_to(_PROJECT_ROOT):
            raise PrivacyConfigurationError("blocklist_must_be_external")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BLOCKLIST_BYTES:
                raise PrivacyConfigurationError()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(_MAX_BLOCKLIST_BYTES + 1)
            after = os.fstat(descriptor)
            if (
                len(data) > _MAX_BLOCKLIST_BYTES
                or len(data) != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or after.st_ctime_ns != before.st_ctime_ns
            ):
                raise PrivacyConfigurationError()
        finally:
            os.close(descriptor)
        content = data.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        raise PrivacyConfigurationError() from None

    entries = []
    for line in content.splitlines():
        literal = line.strip()
        if not literal or literal.startswith("#"):
            continue
        if len(literal) > 256 or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in literal):
            raise PrivacyConfigurationError()
        entries.append(literal)
        if len(entries) > 1000:
            raise PrivacyConfigurationError()
    return tuple(dict.fromkeys(entries))


def get_privacy_settings() -> PrivacySettings:
    """Reload the local blocklist at each boundary so edits take effect immediately."""
    raw_path = os.environ.get("DECK_BLOCKLIST_PATH", "")
    return PrivacySettings(
        inbox=Path(os.environ.get("DECK_INBOX_DIR", "inbox")),
        blocklist=_load_blocklist(Path(raw_path)) if raw_path else (),
        blocklist_configured=bool(raw_path),
    )
