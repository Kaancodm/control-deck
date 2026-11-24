"""One checked write path shared by the CLI and the nightly script."""

from datetime import date

from .core import generate, read_sources
from .exitcodes import EXIT_AUTH, EXIT_CONFIG, EXIT_ERROR, EXIT_OK
from .hindsight import (
    ABANDONED,
    CONFIRMED,
    FAILED,
    MAX_ATTEMPTS,
    PRODUCTION_BANK,
    RESUMED,
    RETURNED,
    UNCLEAR,
    HindsightAuthError,
    HindsightClient,
    HindsightConfigError,
    HindsightWriter,
    config_from_env,
)
from .writer import FileWriter

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
        self.auth_rejected = False
        self.rollback_failed = False

    def write(self, entries):
        added = self.primary.write(entries)
        try:
            self.secondary.write(self.primary.read_stored())
        except HindsightAuthError as error:
            # JSONL is already fsynced. Fresh Hindsight reservations were rolled
            # back; if that failed, the error says so and stays an auth error.
            self.mirror_result = None
            self.auth_rejected = True
            self.rollback_failed = getattr(error, "rollback_failed", False)
            return added
        except (OSError, ValueError, TypeError, KeyError):
            # JSONL is already fsynced. Do not leak exception details.
            self.mirror_result = None
            return added
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
    if getattr(writer, "auth_rejected", False):
        out(
            "Weitblick: Hindsight-Anmeldung abgelehnt. JSONL ist gesichert; "
            "offene Eintraege bleiben unveraendert."
        )
        if writer.rollback_failed:
            out(
                "Weitblick: Reservierungen konnten nicht zurueckgegeben werden; "
                "der naechste Lauf gibt sie ohne Versuch zurueck."
            )
        return EXIT_AUTH
    result = writer.mirror_result
    if result is None:
        out(
            "Weitblick: Hindsight-Spiegelung fehlgeschlagen. JSONL ist gesichert; "
            "offene Eintraege werden im naechsten Lauf erneut gesendet."
        )
        return EXIT_ERROR
    if result[RETURNED] or result[RESUMED]:
        out(
            f"Hindsight: abgebrochener Lauf erkannt: {result[RETURNED]} Reservierungen "
            f"zurueckgegeben, {result[RESUMED]} erneut offen, kein Versuch gezaehlt."
        )
    out(
        f"Hindsight (Bank {PRODUCTION_BANK}): {result[CONFIRMED]} bestaetigt, "
        f"{result[FAILED]} fehlgeschlagen, {result[UNCLEAR]} unklar, "
        f"{result['bereits']} bereits bestaetigt, "
        f"{result[ABANDONED]} aufgegeben."
    )
    if result[FAILED] or result[UNCLEAR] or result[ABANDONED]:
        parts = ["Weitblick: Hindsight-Spiegelung unvollstaendig. JSONL ist gesichert."]
        if result[FAILED] or result[UNCLEAR]:
            parts.append("Offene Eintraege werden im naechsten Lauf erneut gesendet.")
        if result[ABANDONED]:
            parts.append(f"{result[ABANDONED]} Eintraege nach {MAX_ATTEMPTS} Versuchen aufgegeben.")
        out(" ".join(parts))
        return EXIT_ERROR
    return EXIT_OK
