"""Password tooling has no argv, file, or echo path for a cleartext password."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher, Type, extract_parameters

from scripts.set_password import main


def test_tool_prints_only_strong_argon2id_hash(capsys: pytest.CaptureFixture[str]) -> None:
    synthetic = "synthetic-owner-password-for-tool-tests"
    assert main([], read_password=lambda prompt: synthetic) == 0
    output = capsys.readouterr()
    value = output.out.strip()
    assert output.err == "" and synthetic not in output.out
    assert PasswordHasher().verify(value, synthetic)
    parameters = extract_parameters(value)
    assert parameters.type == Type.ID
    assert parameters.memory_cost == 65536
    assert parameters.time_cost == 3
    assert parameters.parallelism == 1


@pytest.mark.parametrize(
    "values",
    [
        ("short", "short"),
        (" " * 20, " " * 20),
        ("x" * 1025, "x" * 1025),
        ("synthetic-owner-password", "different-synthetic-password"),
    ],
)
def test_tool_rejects_without_echo(
    values: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    source = iter(values)
    assert main([], read_password=lambda prompt: next(source)) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert all(value not in output.err for value in values)


def test_cli_rejects_password_arguments_before_read(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            ["--password", "synthetic-forbidden-argument"],
            read_password=lambda prompt: pytest.fail("Read input"),
        )
    assert caught.value.code == 2
    assert "synthetic-forbidden-argument" not in capsys.readouterr().err


def test_non_private_terminal_never_falls_back_to_echo(capsys: pytest.CaptureFixture[str]) -> None:
    import getpass

    def unavailable(prompt: str) -> str:
        raise getpass.GetPassWarning("synthetic-terminal-detail")

    assert main([], read_password=unavailable) == 2
    output = capsys.readouterr()
    assert output.out == "" and "synthetic-terminal-detail" not in output.err
