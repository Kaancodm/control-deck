"""The local unlock tool requires explicit intent and never prints database errors."""

from __future__ import annotations

import pytest

from app.db.config import DatabaseConfigurationError
from scripts.unlock_login import main


def test_unlock_requires_confirmation_before_any_database_access() -> None:
    assert main(["--target", "test"], session_factory=lambda: pytest.fail("Database opened")) == 2


def test_unlock_requires_target() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--confirm-unlock"], session_factory=lambda: pytest.fail("Database opened"))
    assert caught.value.code == 2


def test_unlock_errors_do_not_echo_database_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable():
        raise DatabaseConfigurationError("synthetic-private-database-location")

    assert main(["--target", "test", "--confirm-unlock"], session_factory=unavailable) == 2
    assert "synthetic-private" not in capsys.readouterr().err
