"""Sanctioned local commands for a return path. No shell and no model commands.

``poll`` is read-only. ``ack`` acknowledges one wake correlation. ``ack-receipt``
acknowledges one acceptance receipt. ``dispatch`` is host-side and is not part
of the return command.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence

from app.api.config import owner_token_configured
from app.channels.receipt import acknowledge_task_receipt, database_now
from app.db.session import get_session_factory
from app.transport.contract import WakeContractError
from app.transport.outbox import acknowledge_wake, dispatch_pending_wakes, poll_task


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.transport.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    poll = commands.add_parser("poll")
    poll.add_argument("--task-id", required=True)
    ack = commands.add_parser("ack")
    ack.add_argument("--task-id", required=True)
    ack.add_argument("--correlation", required=True)
    receipt = commands.add_parser("ack-receipt")
    receipt.add_argument("--task-id", required=True)
    receipt.add_argument("--idempotency-key", required=True)
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--limit", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    if not owner_token_configured():
        print("auth_unconfigured", file=sys.stderr)
        return 3
    if args.command == "dispatch":
        try:
            count = dispatch_pending_wakes(get_session_factory(), limit=args.limit)
        except WakeContractError as exc:
            print(exc.code, file=sys.stderr)
            return 5
        print(json.dumps({"dispatched": count}))
        return 0
    task_id = _uuid(args.task_id)
    if task_id is None:
        print("invalid_task_id", file=sys.stderr)
        return 2
    if args.command == "ack" and _uuid(args.correlation) is None:
        print("invalid_correlation", file=sys.stderr)
        return 2
    factory = get_session_factory()
    with factory() as session:
        try:
            if args.command == "poll":
                document = poll_task(session, task_id)
                session.rollback()
            elif args.command == "ack":
                correlation = _uuid(args.correlation)
                assert correlation is not None
                row = acknowledge_wake(session, task_id=task_id, correlation_id=correlation)
                session.commit()
                document = {
                    "task_id": str(task_id),
                    "correlation_id": str(row.correlation_id),
                    "state": row.state,
                }
            else:
                receipt = acknowledge_task_receipt(
                    session,
                    task_id=task_id,
                    idempotency_key=args.idempotency_key,
                    now=database_now(session),
                )
                session.commit()
                document = {
                    "task_id": str(task_id),
                    "receipt_status": receipt.status,
                    "transport": receipt.transport,
                }
        except WakeContractError as exc:
            session.rollback()
            print(exc.code, file=sys.stderr)
            return 4 if exc.code == "not_found" else 5
        except ValueError as exc:
            session.rollback()
            print(str(exc), file=sys.stderr)
            return 5
    print(json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
