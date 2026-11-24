# Arbeitsstand Stufe 1

Implementiert: Offline-Parser, drei Fragearten, Zehn-Felder-Schema, begrenzter
Datei-Schreiber und CLI. Nur erfundene Testlisten; keine echten Listen importiert.

Pruefung: `python3 -m unittest discover -s weitblick/tests -v`: 18 Tests, OK.
Ruff check und format --check fuer weitblick/: gruen. JSON-Schema und erzeugte
Eintraege separat validiert. `python3 scripts/check_rules.py`: Exit 0.
`git diff --check`: Exit 0. Gitleaks fuer den neuen Ordner: keine Funde.

Grenze: `make lint` und `make test` nicht ausfuehrbar (make fehlt).
Direkte Ruff-Pruefung mit vorhandenem Cache-Binary moeglich, pytest fehlt.
Keine Pakete installiert. Gesamttestsuite und Review bleiben offen.

Review: SuperGrok. Merge ausschliesslich durch den Owner. Kein Deploy.
Backup und Formatgrenzen: siehe README. Zustellung der Abschlussmeldung
wartet auf einen erlaubten Postfachweg ohne Serverzugriff.

# Arbeitsstand Stufe 2

2026-11-24 10:52, Claude Opus 5.5: Hindsight-Spiegel, Nachtlauf, S-K80- und
Vertragspruefer, Offline-Tests, README-Abschnitt, `SERVERPRUEFUNG.md`.
Pruefung: `python3 -m unittest discover -s weitblick/tests -v`: Ran 60 tests, OK (0 failed, 0 skipped).
Grenze: Ruff nicht verfuegbar (keine Installation), Format von Hand geprueft.
Offen: F2/F5 und S-K80 nur auf dem Server entscheidbar; T8-Restore braucht GO.
