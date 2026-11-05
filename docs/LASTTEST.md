# Lasttest: parallele simulierte Runner

Der Lasttest prüft D2 und Abnahmetest 3 aus
[Etappe 18](ETAPPEN/18-abnahme-1-0.md). Standard: **50 Aufgaben, 3 eigene Runner-Prozesse**
der Lane `ollama`. Jeder Runner verwendet den bestehenden `OllamaLaneRunner` und die
eingebündelte Fake-CLI. Es wird kein echtes Ollama und keine Cloud-Lane aufgerufen.
Das Ergebnis ist ein Simulationsnachweis. Der Owner-Canary bleibt ein eigener Schritt nach GO.

## Ausführen

Ort: lokale isolierte Testumgebung, Repo-Wurzel. Voraussetzung ist eine ausdrücklich gesetzte
`DECK_TEST_DATABASE_URL`: PostgreSQL mit Datenbankname auf `_test`, expliziter lokaler
Unix-Socket oder Loopback-IP-Literal. Hostnamen, Service-/Routing-Overrides und implizite
Verbindungswerte werden abgewiesen. `DECK_DATABASE_URL` wird niemals als Ersatz benutzt.
Verbindungswerte gehören weder in den Bericht noch ins Repo.

Die eigene Wegwerf-Testdatenbank muss auf dem aktuellen Alembic-Head stehen. Die Tabellen
`tasks`, `results`, `events`, `leases`, `settings` und `lane_status` müssen leer sein.
Keine anderen Tests oder Runner gleichzeitig auf dieser Datenbank ausführen. Ein eigener
Datenbank-Lock verhindert zwei gleichzeitige Aufrufe des Lasttest-Skripts.

```sh
uv run alembic -x target=test upgrade head
make loadtest
```

Anzahl, Parallelität und Berichtspfad sind konfigurierbar:

```sh
make loadtest ARGS='--tasks 50 --runners 3 --timeout 120 --json-output loadtest-result.json'
```

`--tasks` erlaubt 1–10000 Aufgaben. `--runners` erlaubt 1–16 Prozesse, passend zur vorhandenen
`max_parallel`-Grenze. `--timeout` begrenzt den Testaufbau und die Ausführung; Standard
120 Sekunden. Das ist eine Abbruchsicherung, **keine Performance-SLA**. Die Etappen-Spec
definiert keinen Mindestdurchsatz und keine maximale p95-Latenz.

Die Ausgabe enthält eine Tabelle und anschliessend ein vollständiges JSON-Objekt. Mit
`--json-output` entsteht zusätzlich eine neue JSON-Datei; vorhandene Dateien werden nicht
überschrieben. Die Ausgabe enthält nur Messwerte und feste Fehlercodes, keine Payloads,
Verbindungswerte oder Provider-Fehlertexte.

## Messwerte und Gates

| Anforderung / Messwert | Nachweis und Grenze |
| --- | --- |
| 50 Aufgaben, 3 Prozesse als Standard | Eindeutige neue Task-IDs; Startbarriere; unterschiedliche Prozess-IDs und abgeschlossene Prozessstatistiken. |
| Freigabe vor Ausführung | Aufgaben entstehen als `awaiting_approval`, werden ausdrücklich durch den Test freigegeben und erhalten `approved_at`. |
| Genau ein Resultat je Versuch | Gruppierung nach Task-ID und Versuch; fehlende, doppelte oder unerwartete Resultate führen zu FAIL. Die Fixture erwartet genau Versuch 1. |
| Keine doppelten Claims | Unabhängige Gruppierung der `task.acquire`-Events; genau ein Event je erwartetem Versuch. |
| Keine hängenden Leases | Prüfung nach dem Stoppen und Join aller Runner; jede übrige Lease führt zu FAIL. |
| Erfolgreiche Simulation | Alle Aufgaben in `review`, Ergebnis erfolgreich, Acceptance bestanden, Herkunft `simulation`; sonst FAIL. |
| Lease-/Fencing-Konflikte | Summe der beobachteten `lost_lease`-Rückgaben und `task.lease_expired`-Events; jeder Treffer führt zu FAIL. Mehrere Beobachtungen können denselben Vorfall beschreiben. |
| Nicht näher auflösbare Runner-Fehler | Separater Fehlerzähler; insbesondere `result_persistence_failed` wird nicht als bewiesener Fencing-Fall ausgegeben. Jeder Runner-Fehler oder Prozessabbruch führt zu FAIL. |
| Durchsatz | Erfolgreiche Aufgaben / Wandzeit vom Beginn der Task-Erstellung und Freigabe bis zum Ende aller Runner; Prozessaufbau und Migration sind nicht enthalten. |
| Latenz p50/p95 | Zeit vom gespeicherten `task.approve`-Event bis zum gespeicherten Resultat; enthält Wartezeit in der Queue und Ausführung. Per Nearest-Rank berechnet, keine reine Modelllaufzeit. |
| Fehlerquote | Anteil angeforderter Aufgaben ohne genau einen gültigen erfolgreichen Abschluss; nicht abgeschlossene Aufgaben zählen mit. |

Exit-Code `0` bedeutet PASS, `1` eine verletzte Invariante oder einen abgebrochenen Messlauf,
`2` eine ungültige/unverfügbare Testumgebung oder einen Fehler beim Speichern des Berichts.
Fehlende Messwerte werden bei einem Aufbaufehler nicht durch erfundene Nullen ersetzt.

## Daten und Einordnung

Das Skript löscht keine Datenbankzeilen. Nach dem Lauf bleiben seine Aufgaben, Resultate,
Events und Einstellungen in der Wegwerf-Testdatenbank zur Kontrolle erhalten. Für den
nächsten Lauf eine frische Testdatenbank verwenden. Temporäre Fixture-Konfiguration und
Fake-CLI-Artefakte gehören nur diesem Lauf und werden danach entfernt. Ein verspätetes
oder fehlgeschlagenes Ergebnis wird nicht durch nachträgliches Löschen einer Lease kaschiert.

`make test-server` prüft zuerst die lokale Test-DB-Konfiguration und führt danach die
vorhandene Testsuite (`make test`) und denselben Lasttest **nacheinander** aus, auch bei `make -j`.
Dabei werden auch gesetzte `DECK_KB_TEST_DATABASE_URL`, `DECK_RESTORE_TEST_DATABASE_URL`
und `DECK_KB_RESTORE_TEST_DATABASE_URL` auf lokale `_test`-Ziele geprüft. Geerbte
libpq-Verbindungsvariablen (`PG*`) sowie Runtime-/Live-Konfiguration werden dem Testprozess
nicht weitergegeben. Eine fehlende optionale KB-Test-URL bleibt fehlend; vorhandene
Tests entscheiden wie bisher über die zugehörigen Skips.
Der Name bezeichnet hier das lokale Test-DB-Gate mit Fakes. Echte Owner-Abnahmen mit
Ollama oder Cloud-Lanes sowie die vollständige Betriebsabnahme bleiben gesonderte Schritte.
Version, Release-Tag und Sicherheits-Checkliste bleiben eigene Etappe-18-Aufgaben.

Die Skript-Tests laufen mit `uv run pytest tests/unit/test_loadtest.py
tests/integration/test_loadtest.py`. Der Integrationstest führt den Standardfall tatsächlich
mit 50 Aufgaben und 3 Prozessen gegen eine lokale Testdatenbank aus und prüft die Datenbank
zusätzlich unabhängig vom JSON-Bericht. Ohne Testdatenbank werden Integrationstests wie im
übrigen Repo übersprungen.
