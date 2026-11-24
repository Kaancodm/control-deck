"""One checked write path shared by the CLI and the nightly script."""

from datetime import date

from .core import generate, read_sources
from .hindsight import (
    CONFIRMED,
    FAILED,
    PRODUCTION_BANK,
    UNCLEAR,
    HindsightClient,
    HindsightConfigError,
    HindsightWriter,
    config_from_env,
)
from .writer import FileWriter

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2

GENERIC_ERROR = "Weitblick: Eingabe oder Ausgabe ungueltig; keine Freigabe fuer Verwendung."


class MirroredWriter:
    """Writer.write(entries) -> int with the Stage 1 meaning: new JSONL entries.

    JSONL is written and fsynced first. Only afterwards the secondary writer
    receives the authoritative JSONL content. A secondary failure cannot touch
    the JSONL file; its outcome is kept in `mirror_result`.
    """

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.mirror_result = None

    def write(self, entries):
        added = self.primary.write(entries)
        self.secondary.write(self.primary.read_stored())
        self.mirror_result = dict(self.secondary.last_result)
        return added


def run(paths, output, environ, *, no_hindsight=False, today=None, transport=None, out=print):
    """Run Weitblick once. Returns an exit code; messages never contain source data."""
    try:
        config = None if no_hindsight else config_from_env(environ)
    except HindsightConfigError as error:
        out(f"Weitblick: Konfigurationsfehler: {error}")
        return EXIT_CONFIG
    try:
        entries = generate(read_sources(paths), today or date.today().isoformat())
        file_writer = FileWriter(output, paths.values())
        if config is None:
            writer = file_writer
        else:
            hindsight = HindsightWriter(output, HindsightClient(config, transport))
            writer = MirroredWriter(file_writer, hindsight)
        count = writer.write(entries)
    except (OSError, ValueError, TypeError, KeyError):
        # Do not echo source contents, filenames or private values in errors.
        out(GENERIC_ERROR)
        return EXIT_ERROR
    out(f"{count} neue Fragen gespeichert.")
    if config is None:
        out("Hindsight ausgeschaltet; kein Hindsight-Aufruf.")
        return EXIT_OK
    result = writer.mirror_result
    out(
        f"Hindsight (Bank {PRODUCTION_BANK}): {result[CONFIRMED]} bestaetigt, "
        f"{result[FAILED]} fehlgeschlagen, {result[UNCLEAR]} unklar, "
        f"{result['bereits']} bereits bestaetigt."
    )
    if result[FAILED] or result[UNCLEAR]:
        out(
            "Weitblick: Hindsight-Spiegelung unvollstaendig. JSONL ist gesichert; "
            "offene Eintraege werden im naechsten Lauf erneut gesendet."
        )
        return EXIT_ERROR
    return EXIT_OK
