"""Nightly entry point: same checked path as the CLI, configured by environment only.

Start from the repository root:  python3 -B -m weitblick.nachtlauf
Installing a cron job or timer is a separate, owner-approved step (see README).
"""

import os
import sys
from pathlib import Path

from .hindsight import OFF_VALUES, ON_VALUES
from .pipeline import EXIT_CONFIG, run

ENV_DISABLED = "WEITBLICK_DISABLED"
ENV_PATHS = {
    "ENTSCHEIDE": "WEITBLICK_ENTSCHEIDE",
    "SERVER": "WEITBLICK_SERVER",
    "AUFTRAEGE": "WEITBLICK_AUFTRAEGE",
}
ENV_OUTPUT = "WEITBLICK_OUTPUT"


def main(environ=None, transport=None):
    environ = os.environ if environ is None else environ

    def out(message):
        is_error = message.startswith("Weitblick:")
        print(message, file=sys.stderr if is_error else sys.stdout)

    disabled = environ.get(ENV_DISABLED, "").strip().lower()
    if disabled in ON_VALUES:
        out("Weitblick-Nachtlauf ausgeschaltet; keine Dateien gelesen oder geschrieben.")
        return 0
    if disabled not in OFF_VALUES:
        out(f"Weitblick: Konfigurationsfehler: {ENV_DISABLED} muss 1 oder 0 sein")
        return EXIT_CONFIG
    names = [*ENV_PATHS.values(), ENV_OUTPUT]
    missing = [name for name in names if not environ.get(name, "").strip()]
    if missing:
        out("Weitblick: Konfigurationsfehler: es fehlen: " + ", ".join(missing))
        return EXIT_CONFIG
    paths = {source: Path(environ[name]) for source, name in ENV_PATHS.items()}
    return run(paths, Path(environ[ENV_OUTPUT]), environ, transport=transport, out=out)


if __name__ == "__main__":
    raise SystemExit(main())
