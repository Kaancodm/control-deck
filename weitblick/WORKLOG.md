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

2026-11-24 12:00, Builder: Review-Fix zu PR #98. Feld `mode` aus dem
Retain-Request entfernt (nicht in Hindsight v1.2.1). F2 in der README an der
Quelle dokumentiert. T4 und der Fake pruefen gegen die echte Feldliste.
Vertragspruefer meldet fehlendes `mode` nicht mehr als Abweichung. SERVERPRUEFUNG:
Schritt 5 (retain_extraction_mode=chunks auf Test-Banks, braucht Kaans Ja, nicht
ausgefuehrt) und Chunk-Pfad in S-K80/T5. Kann: Retry-Limit, Meldung bei
Exception im zweiten Schreiber, Test-Banknamen als Konstante.
Pruefung: `python3 -m unittest discover -s weitblick/tests -v`: Ran 65 tests, OK (0 failed, 0 skipped), 24.11.2026 12:03 CET.

2026-11-24 12:50, Builder: Review-Nachzug PR #98. Chunk-Pruefpunkt in S-K80
bleibt offen (404 auf unbekannte Chunk-ID belegt keine Trennung). Aufgegebene
Eintraege nach 3 Versuchen werden gezaehlt, mit Exit ungleich 0 und klarer
Meldung. Hinweis zu enable_observations in SERVERPRUEFUNG Schritt 5.
Pruefung: `python3 -m unittest discover -s weitblick/tests -v`: Ran 66 tests, OK (0 failed, 0 skipped), 24.11.2026 12:52 CET.
