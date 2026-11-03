"""Explicit local administration of the persisted owner login lock."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.config import DatabaseConfigurationError
from app.db.session import get_session_factory
from app.db.ui_auth import unlock_owner_login


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], AbstractContextManager[Session]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Explicitly clear the owner login lock.")
    parser.add_argument("--target", choices=("test", "configured"), required=True)
    parser.add_argument("--confirm-unlock", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_unlock:
        print("Unlock requires --confirm-unlock; no database was opened.", file=sys.stderr)
        return 2
    try:
        factory = session_factory or get_session_factory(for_tests=args.target == "test")
        with factory() as session:
            unlock_owner_login(session)
            session.commit()
    except (DatabaseConfigurationError, SQLAlchemyError):
        print("Login unlock failed; database details are not displayed.", file=sys.stderr)
        return 2
    print("Owner login lock cleared and recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
