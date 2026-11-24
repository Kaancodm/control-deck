"""Stable exit codes shared by every Weitblick entry point.

One number has one meaning in all commands (pipeline, nightly run, S-K80,
contract check). Change only together with README and SERVERPRUEFUNG.
"""

EXIT_OK = 0  # Erfolg; bei S-K80: bestanden
EXIT_ERROR = 1  # Fehler oder unvollstaendig; bei S-K80: nicht bestanden
EXIT_CONFIG = 2  # Konfiguration oder Aufruf ungueltig
EXIT_AUTH = 3  # Hindsight hat die Anmeldung abgelehnt (HTTP 401 oder 403)
EXIT_OPEN = 4  # Pruefung ohne Urteil (S-K80: offen)

EXIT_CODES = {
    EXIT_OK: "ok",
    EXIT_ERROR: "fehler",
    EXIT_CONFIG: "konfiguration",
    EXIT_AUTH: "anmeldung",
    EXIT_OPEN: "offen",
}
