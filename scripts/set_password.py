"""Create an owner Argon2id hash using interactive, non-echoed input only."""

from __future__ import annotations

import argparse
import getpass
import hmac
import sys
import warnings
from collections.abc import Callable, Sequence

from argon2 import PasswordHasher, Type


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "Unexpected arguments; enter passwords only at the protected prompt.\n")


def _read_password(prompt: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def main(
    argv: Sequence[str] | None = None,
    *,
    read_password: Callable[[str], str] = _read_password,
) -> int:
    parser = _PrivateArgumentParser(description="Print an owner Argon2id hash, never a password.")
    parser.parse_args(argv)
    try:
        password = read_password("Owner password: ")
        confirmation = read_password("Repeat owner password: ")
        raw = password.encode("utf-8")
        if (
            len(password.strip()) < 12
            or len(raw) > 1024
            or not hmac.compare_digest(raw, confirmation.encode("utf-8"))
        ):
            raise ValueError
    except (ValueError, UnicodeError, EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("Password was not accepted; nothing was written.", file=sys.stderr)
        return 2
    print(PasswordHasher(type=Type.ID, memory_cost=65536, time_cost=3, parallelism=1).hash(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
