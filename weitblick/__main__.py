"""Explicit command line entry point."""

import argparse
import os
import sys
from pathlib import Path

from .pipeline import run


def main(argv=None, environ=None, transport=None):
    parser = argparse.ArgumentParser(description="Weitblick: lokale Fragen, keine Tatsachen")
    for name in ("entscheide", "server", "auftraege", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument(
        "--no-hindsight",
        action="store_true",
        help="Hindsight auch bei gesetzter Umgebung nicht aufrufen",
    )
    args = parser.parse_args(argv)
    if args.disabled:
        print("Weitblick ausgeschaltet; keine Dateien gelesen oder geschrieben.")
        return 0
    if not all((args.entscheide, args.server, args.auftraege, args.output)):
        parser.error("Alle drei Quellen und --output sind erforderlich")
    paths = {"ENTSCHEIDE": args.entscheide, "SERVER": args.server, "AUFTRAEGE": args.auftraege}

    def out(message):
        is_error = message.startswith("Weitblick:")
        print(message, file=sys.stderr if is_error else sys.stdout)

    return run(
        paths,
        args.output,
        os.environ if environ is None else environ,
        no_hindsight=args.no_hindsight,
        transport=transport,
        out=out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
