"""Explicit, offline command line entry point."""

import argparse
from datetime import date
from pathlib import Path

from .core import generate, read_sources
from .writer import FileWriter


def main():
    parser = argparse.ArgumentParser(description="Weitblick: lokale Fragen, keine Tatsachen")
    for name in ("entscheide", "server", "auftraege", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--disabled", action="store_true")
    args = parser.parse_args()
    if args.disabled:
        print("Weitblick ausgeschaltet; keine Dateien gelesen oder geschrieben.")
        return 0
    if not all((args.entscheide, args.server, args.auftraege, args.output)):
        parser.error("Alle drei Quellen und --output sind erforderlich")
    paths = {"ENTSCHEIDE": args.entscheide, "SERVER": args.server, "AUFTRAEGE": args.auftraege}
    try:
        entries = generate(read_sources(paths), date.today().isoformat())
        count = FileWriter(args.output, paths.values()).write(entries)
    except (OSError, ValueError, TypeError, KeyError):
        # Do not echo source contents, filenames or private values in errors.
        parser.exit(
            1, "Weitblick: Eingabe oder Ausgabe ungueltig; keine Freigabe fuer Verwendung.\n"
        )
    print(f"{count} neue Fragen gespeichert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
