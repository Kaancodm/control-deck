# Stand des Repositorys

## Nachtrag: Ollama Live-Adapter als Draft

Der auf `main` nach #37 neu aufgebaute Stack ergänzt gemäss [ADR 0009](DECISIONS/0009-ollama-loopback-http.md)
den strikten Runner-Schalter, Loopback-HTTP für Ollama 0.40.14 und getrennte
Executor-Provenienz. Simulation und nicht attestierte Remote-Ergebnisse zählen
nicht als Live-Erfolg. Canary und Live-Referenzmodus sind für die gesonderte
Owner-Abnahme vorbereitet; echte Modelle und Serverbetrieb bleiben ungeprüft.
Die folgenden Etappenabschnitte beschreiben ihren historischen Simulationsstand.
Aktueller Ausführungsvertrag: [LANES.md](LANES.md), Betrieb: [OPERATIONS.md](OPERATIONS.md).

Der systemd-Prüftest verwendet jetzt passend zu den installierten User-Units
`systemd-analyze --user` und ein privates Runtime-Verzeichnis. Die bisherige
System-Scope-Annahme erforderte unnötig lokale Systemrechte. Gültige Vorlagen
bestehen; eine absichtlich beschädigte Vorlage wird weiterhin abgewiesen.
Aktuelle Lint-, Test- und CI-Nachweise stehen in den Draft-PRs.

Merge-Reihenfolge: #37 zuerst, danach #39, #40, #41, #42, #44, #45, #46, #47.
#37 wurde ausserhalb dieser Arbeit als `a33cc3f` übernommen; der Live-Stack
ist auf diesen tatsächlichen Main-Commit neu aufgebaut.
Die Login-Migration `20261104_0017` bleibt unverändert; die Provenienz-Migration
`20261104_0018` folgt mit `down_revision = "20261104_0017"`. Health ermittelt den
Alembic-Head weiterhin dynamisch. #43 ist nach dem dokumentierten Abgleich zugunsten
von #39 zurückgezogen. Der Live-Stack bleibt ungemergt; ein Serverlauf erfolgte
nicht. Jeder weitere Merge verlangt weiterhin ausdrückliches Owner-GO.

## Etappe 17 implementiert und lokal geprüft

Draft-PR: [#32](https://github.com/Kaancodm/control-deck/pull/32), gestapelt auf Etappe 16.
Migration `0016` zählt ausschliesslich endgültig angenommene Ergebnisse, atomar mit
Ergebnis und Statuswechsel. Zähler beginnen bei dieser Migration; positive Vorprüfungen
bleiben getrennt. 15 SQL-Integrationstests prüfen Konkurrenz, verworfene Ergebnisse,
Rollback, Acceptance und Migrationsschutz.

Audit zeigt begrenzte sichere Metadaten aus Auftrags-, Betriebs- und Auth-Ereignissen;
CSV entspricht einer sichtbaren Seite. Geschützte Lane-Ansicht, Warnband und
`/api/v1/health` nutzen lokale Beobachtungen ohne Anbieterprüfung. Owner-Token oder eine
gültige Session erlauben Gesundheitsdetails; anonym bleibt nur der Minimalstatus.
Die Session gilt nun für den Root-Pfad, CSRF bleibt auf `/ui`; alte Session-Cookies
werden beim Setzen und Abmelden entfernt. Schreib-APIs erhalten keine Session-Rechte.

Nachgebesserte Zählernachweise prüfen Einmalzählung, drei angenommene Fehler und
danach Erfolg mit Gesamtzähler 4 und
Fehlerserie 0; unveränderte Zähler, Fehler- und Erfolgszeitpunkte bei Doppelabgabe,
abgelaufener Lease oder Deadline. Rollback- und Konkurrenznachweise bleiben erhalten.
Die Produktionslogik der Zähler benötigt keine Korrektur. Audit und CSV erhalten die bekannten Codes
`restore_failed` und `restore_partial_failure`, damit Teilfehler sichtbar bleiben.
Rohdetails und unbekannte Restore-Gründe bleiben ausgeblendet. `CHANGELOG.md` enthält
den Etappe-17-Eintrag.

Pflichtchecks auf isolierten PostgreSQL-17-Testdatenbanken: **2335 Tests bestanden**,
keine xfail oder Skips. Darin enthalten sind **125 Etappe-17-Tests** (57 Integration,
68 Unit), einschliesslich echter Chromium-Smokes bei 390 Pixeln mit JavaScript und
1024 Pixeln ohne JavaScript. Geprüft sind Audit-Filter, Seitenwechsel, CSV, Warnband,
Session-Health, Logout, unklassifizierte Fehler, konkurrierende Ergebnisse und sichere
Fehlerantworten. `make lint`, zentrale und KB-Alembic-Prüfung, beide Referenzmodi mit
je **4/4** sowie beide Hook-Suites sind grün. Eine bestehende Starlette/httpx-Warnung.

Die Vollsuite lief auf `98e3ec4`. Der finale Eltern-Rebase auf den
Etappe-16-Head `ef5c669` änderte ausschliesslich `docs/STAND.md` und `CHANGELOG.md`;
Anwendung, Migrationen und Tests sind identisch. CI des finalen Heads steht im PR.
Der Abnahme-Entwurf aus
Checkpoint `a2e6ff3` liegt unter `docs/ABNAHME_CHECKLISTE_ENTWURF.md`; sein Backup-Kriterium
verlangt nun ausdrücklich beide Datenbanken, zwei leere Ziele und fehlgeschlagene Teil-Restores.
Etappe 18 wird damit nicht gestartet. Externes Review durch einen anderen Anbieter
und Owner-Merge bleiben ausstehend.

### Inbetriebnahme

Anbieter-Kontingente bleiben unbekannt, Live-Profile gesperrt. Echte Anmeldungen,
Lane-Verfügbarkeit, Betriebsrechte und Serveränderungen wurden nicht geprüft oder
vorgenommen. Die Gesamt-Abnahme bleibt offen.

## Etappe 13 implementiert und lokal geprüft

Die Codex-Lane ist im gestapelten [Draft-PR #28](https://github.com/Kaancodm/control-deck/pull/28)
auf Etappe 12 umgesetzt. Der versionierte JSONL-Vertrag, eine Fake-CLI und der gemeinsame
Reinraum-Simulator akzeptieren ausschliesslich abgeschlossene Textausgaben. Genau die
freigegebenen und archivierten Payload-Bytes erreichen stdin; `local_only` bleibt bei Ollama.
Tool-Ereignisse, Teilantworten und ungültige Protokolle werden verworfen. Das Codex-Profil
bleibt vom Claude-Profil getrennt; Abbruch und Timeout behalten eine eigene Bereinigung.

Pflichtchecks auf isolierter PostgreSQL-17-Testdatenbank: **1896 Tests bestanden**, davon
**601 Integrationstests** und **1295 Unit-Tests**; keine xfail oder Skips. `make lint`,
`uv run alembic -x target=test check` sowie beide Referenzmodi mit je **4/4 Fällen** sind
grün. Eine bestehende Starlette/httpx-Deprecation-Warnung. Die Tests belegen auch Lane-Pause
und erlaubten Fallback, 60-/240-Sekunden-Backoff über neue Runner hinweg sowie das globale
Versuchsbudget. Die CI des finalen Heads wird im PR ausgewiesen.

Inbetriebnahme bleibt offen: vollständige Tool-Deaktivierung, ausgeschlossene interne
Subscription-Retries, Rootless-/Netzgrenzen, Image-Build und echte Anmeldung sind nicht
nachgewiesen. Das Live-Profil startet keinen Prozess und ist nicht aktivierbar; ein
Simulationserfolg ist kein Live-Nachweis. Keine echten Logins, Anbieteraufrufe, API-Keys
oder Serveränderungen. Details und Quellen: [LANES.md](LANES.md).

Externes Review durch einen anderen Anbieter und Owner-Merge bleiben ausstehend.

## Etappe 12 bereit für externes Review

Branch: `stage/12-claude`, gestapelt auf Etappe 11 (`stage/11-ui-chat`, PR #26).
Draft-PR: [#27](https://github.com/Kaancodm/control-deck/pull/27).
Umgesetzt sind ein versionierter Claude-CLI-Vertrag, streng begrenzte Reinraum-Befehle
und Simulationen mit Fake-Container und Fake-CLI. Es werden nur geprüfte
Versandbytes übergeben; `local_only` bleibt bei Ollama. Das Live-Profil bleibt gesperrt,
bis Werkzeugabschaltung, Rootless-Isolation und Netzgrenzen bei der Inbetriebnahme belegt
sind. Auth-/Policy-Fehler pausieren die ausführende Lane und führen über die zentrale
Fehlerlogik zum zulässigen Fallback. Rate-Limits erhalten 60/240 Sekunden Backoff über
Runner-Neustarts hinweg; Acceptance bleibt innerhalb des Budgets auf derselben Lane.
Abbruch, Deadline und Lease-Verlust stoppen den Versuch; Cleanup hat ein eigenes Budget.

Nach Rebase auf Etappe 11: `make lint` grün, `uv run alembic -x target=test check`
ohne Schemaabweichung, **1714 Tests bestanden** (**575 Integration**, **1139 Unit**),
keine Skips oder xfail. Davon sind **136 neue Etappe-12-Tests** (26 Integration, 110 Unit).
`make reference`: **4/4**. Eigene isolierte PostgreSQL-17-Testdatenbank; eine bestehende
Starlette/httpx-Deprecation-Warnung. Die Vollsuite lief auf `d47827a`; der letzte Eltern-
Rebase änderte ausschliesslich Dokumentation und zwei historische Migrationstests,
die danach erneut bestanden. Anwendung, Migrationen und Konfiguration sind identisch.
Die CI des finalen Heads und die geprüften Commits stehen im PR.

### Inbetriebnahme

Die gepinnte Image-Vorlage ist nicht gebaut. Keine echten Container-/CLI-/Anbieteraufrufe,
Logins, API-Keys oder Serveränderungen. Das Live-Profil bleibt gesperrt; Rootless-,
Werkzeug-/MCP-/Hook- und Netzgrenzen sowie Konto-/Abo- und Protokollverhalten müssen
separat nachgewiesen werden. Simulation bestätigt keine Live-Bereitschaft. Keine neuen
inhaltlichen Referenzfälle, kein Self-Merge. Details: [LANES.md](LANES.md).

## Etappe 11 bereit für externes Review

Draft-PR: [#26](https://github.com/Kaancodm/control-deck/pull/26).
Branch: `stage/11-ui-chat`, gestapelt auf Etappe 10 (`stage/10-error-paths`, PR #25).

Umgesetzt:

- Themebare Jinja2-Oberfläche mit zentralen CSS-Variablen, wiederverwendbaren Partials und
  lokalem HTMX. Das spätere Design aus den Owner-Komponenten bleibt separat testbar.
- Argon2id-Passwortlogin, serverseitige Sessions, CSRF, Rotation, Ablauf, Logout und
  persistente Login-Drosselung. Migration 0012 speichert ausschliesslich Token-Hashes.
- Task-Formular, begrenzte sichere UTF-8-Dateien, Liste, Detail, Audit-Zeitlinie,
  Start-Freigabe, Ergebnis-Review und Abbruch über gemeinsame API-/UI-Geschäftsfunktionen.
- Schreibgeschützte Fehlerliste, Lane-Zustand, Pause und bestätigter Not-Aus. Blockierte
  Tasks entsperrt nur der Owner ausdrücklich (`blocked -> queued`, Pflichtbegründung ab
  10 Zeichen, Audit-Event `task.unblock` mit `actor=owner`); Datenschranke und Routing
  prüfen beim Lauf erneut. Review-Ablehnung führt zu `failed`. Auftrags-GETs schreiben nicht.
- Lokaler Fake-CLI-Chat mit explizitem Kontext und unverbindlichem validiertem Entwurf.
  Keine Aktionsrechte, kein dauerhaft gespeicherter Verlauf und keine automatische
  Übernahme von Chat-Kontext in Cloud-Aufträge. Das Live-Profil bleibt unverfügbar.
- Gespeicherte, bereits rückabgebildete Ergebnisse werden escaped angezeigt. Keine zweite
  Rückabbildung, keine Redaction-Map im Template. Exakte Rückabbildungspositionen bleiben
  mangels gespeicherter Provenienz ein Folgepunkt; die eingefrorenen Schemas bleiben erhalten.

Pflichtchecks vom 2026-11-03: `make lint` grün (Ruff, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1578 bestandenen Tests** (**549 Integration**, **1029 Unit**), **keine xfail oder Skips**.
Darin enthalten: echte Chromium-Smokes bei **390 Pixeln mit JavaScript** und **1024 Pixeln
ohne JavaScript** mit synthetischem Login, Upload, Freigabe, Fake-Worker, Review, `done`,
Chat-Entwurf und Logout. Eine bestehende Starlette/httpx-Deprecation-Warnung.
`make reference`: **4/4**; Ollama-Fake-CLI-Referenzlauf ebenfalls **4/4**. Isolierte
PostgreSQL-17-Testdatenbank. Die CI des finalen Heads wird im PR ausgewiesen.
Details und begründete Abhängigkeiten: [UI.md](UI.md), [ETAPPEN/11-ui.md](ETAPPEN/11-ui.md).

### Inbetriebnahme

Keine echten Logins, Modell-/Anbieteraufrufe, API-Keys oder Serveränderungen. Offen bleiben
sichere Bindung/TLS, echte Zugangsdaten und Geräte, nachgewiesene Live-Ollama-Isolation mit
gemeinsamer Modell-Zulassung sowie das spätere finale Design. Simulation ist kein Live-Nachweis.
Externes Review erfolgt durch einen anderen Anbieter; es wird nicht selbst gemergt.

## Etappe 10 bereit für externes Review

Draft-PR: [#25](https://github.com/Kaancodm/control-deck/pull/25).
Branch: `stage/10-error-paths`, gestapelt auf Etappe 9 (`stage/09-ollama`, PR #24).

Umgesetzt:

- Migration `0011`: dauerhafter Retry-Cursor, festgehaltene Lane, Fehlerzähler und
  nächster Ausführungszeitpunkt ausserhalb der eingefrorenen Schemas.
- Acceptance auf derselben Lane bis zum globalen Limit; Rate-Limits mit 60/240 Sekunden
  Backoff, danach Fallback; Timeout und Nichtverfügbarkeit zur nächsten erlaubten Lane;
  ein taskweiter interner Retry. Keine versteckten Adapter-Wiederholungen.
- Anbieterbedingte Auth-/Policy-Fehler pausieren die Lane atomar mit Ergebnis und Event.
  Erneutes Fencing verhindert Wirkungen falscher, doppelter oder verspäteter Ergebnisse.
- Queue-Polls und Neustarts erhalten den Fortschritt. Backoff wird auch im atomaren
  Claim geprüft. Strukturell erschöpfte Ketten enden als `failed`; vorübergehend
  nicht verfügbare erlaubte Lanes bleiben wartend.
- `local_only` bleibt Ollama; Datenschranke und Owner-Abbruch lösen keinen Fallback aus.
  Nur eine abgelaufene ausdrückliche Owner-Deadline erzeugt `expired`.
- Owner-Fehlerliste `GET /api/v1/tasks/failed` ohne Schreibeffekt oder Task-Lock, mit
  sicherem effektivem Fehlercode. Keine Wiederbelebung oder Entsperrung terminaler Tasks.

Pflichtchecks vom 2026-11-03: `make lint` grün (Ruff ohne Cache, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1455 bestandenen Tests** (**499 Integration**, **956 Unit**), **keine xfail oder Skips**.
Eine bestehende Starlette/httpx-Deprecation-Warnung. `make reference`: **4/4**;
Ollama-Fake-CLI-Referenzlauf ebenfalls **4/4**. Isolierte PostgreSQL-17-Testdatenbanken.
Die CI des finalen Heads wird im PR ausgewiesen. Externes Review und Merge bleiben
beim Owner; es wurde kein eigenes formales Review und kein Merge ausgeführt.
Details: [ETAPPEN/10-fehlerpfade.md](ETAPPEN/10-fehlerpfade.md).

### Inbetriebnahme

Alle Läufe verwenden Simulationen und Fake-CLIs. Echte Anmeldungen, Anbieter-Kontingente,
Modellaufrufe und Live-Fehler bleiben ungeprüft. Keine API-Keys oder Serveränderungen.
Das Ollama-Live-Profil bleibt gesperrt; neue inhaltliche Referenzfälle bleiben Owner-Aufgabe.

## Etappe 9 bereit für externes Review

Draft-PR: [#24](https://github.com/Kaancodm/control-deck/pull/24).
Branch: `stage/09-ollama`, nach den externen Review-Korrekturen auf
`stage/08-runner` / PR #23 (`dc81401`) rebased.

Umgesetzt sind ein begrenzter Ollama-CLI-Prozessadapter, Lane-Gesundheit mit Migration
`0010`, simulierte Host-E2E-Läufe und der ausdrückliche Referenz-Schalter
`--executor ollama-fake-cli --model <simulationskennung>`. Die neue Executor-Identität,
Pflicht-Lane und Artefaktkonflikt-Behandlung aus der korrigierten Etappe 8 bleiben erhalten.

Pflichtchecks vom 2026-11-03: `make lint` grün (Ruff ohne Cache, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1264 bestandenen Tests** (**425 Integration**, **839 Unit**), **keine xfail oder Skips**.
Eine bestehende Starlette/httpx-Deprecation-Warnung. `make reference`: **4/4**;
der zusätzliche Ollama-Fake-CLI-Referenzlauf ebenfalls **4/4**. Isolierte PostgreSQL-17-
Testdatenbank. Zwei parallele Fake-CLI-Runner schliessen 20 Aufträge jeweils genau einmal
ab. Neue inhaltliche Referenzfälle wurden nicht erstellt. Die CI des finalen Heads wird
im PR ausgewiesen; externes Review und Merge bleiben beim Owner.

### Inbetriebnahme

Das Live-Profil ist nicht aktivierbar und startet keinen Prozess. Es bleibt
`unavailable` / `live_profile_unavailable`. Ein `ok` mit `simulation_ready` sowie
`last_success_at` belegen ausschliesslich die Fake-Inventarprüfung, keine Live-Bereitschaft.
Die offizielle CLI bietet keinen No-Pull-Schalter; Cloud-Sperren müssen beim Daemon gelten,
multimodale CLI-Pfade können promptgesteuert Hostdateien lesen, und Exit-Code null allein
belegt nicht jeden Abschluss. Eine spätere Live-Anbindung benötigt nachweisbare Download-/
Cloud-Sperren, Dateisystem-Isolation und einen eindeutigen Abschlussnachweis. Quellen und
Details: [LANES.md](LANES.md). Keine echten Modellaufrufe, Logins, API-Keys, Serveränderungen
oder produktiven Verbindungen ausgeführt.


## Etappe 8 bereit für externes Review

Draft-PR: [#23](https://github.com/Kaancodm/control-deck/pull/23).
Branch: `stage/08-runner`, nach Squash-Merge von PR #22 auf `main a092942` rebased.
Der Owner hat die gestapelte Fortsetzung bis Etappe 17 freigegeben. Etappe 18 wird nur als
Abnahme-Checkliste vorbereitet; kein Merge oder Release durch den Agenten.

Umgesetzt:

- Lanegebundene Claims mit Owner-Freigabe, Policy, Versuchs-/Tagesbudget und globaler
  Parallelität (Standard 1). `local_only` bleibt Ollama; nur die geclaimte Lane wird belastet.
- Migration `0009` und typisierte `settings` für Pause, Not-Aus und Lane-Pause. Claims und
  Steuerung sind atomar serialisiert; bestehende Abbruchanforderungen bleiben gesetzt.
- Finale Datei-Hashes aus genau dem gelesenen Versandtext müssen zur Owner-Freigabe passen.
  Maps und Vorbereitung werden vor Payload-Auslieferung committed. Worker sehen keine
  Originalaufträge, Map oder früheren Ergebnisse.
- Expliziter Start, Heartbeat und Steuerentscheide `continue`, `cancel`, `expired`,
  `lost_lease`. Expiry wird vor dem Stop committed; Lease-Timeout ist nie `expired`.
- Direkter Host-Runner ohne offene Ausführungs-Transaktion beim Executor-Aufruf; lokale
  Rückabbildung und Acceptance vor erneuter Ergebnis-Fencing-Prüfung.
- Private unveränderliche Payload-/Ergebnisartefakte und sichere Laufmetadaten-Logs.
- Review-Nacharbeit `636d724`: deaktivierte erlaubte Lanes führen zu `deferred`, nicht
  terminal zu `blocked`. Nach Reaktivierung wird derselbe wartende Task korrekt geclaimt.
- Nacharbeit zum externen Review auf `5fd9704`: eigene Token je Lane bestimmen die
  authentifizierte Identität. Doppelte Tokens, Owner-Kollisionen und Lane-Spoofing werden
  abgewiesen; Executor-Identität und Queue-Lane sind ausdrücklich erforderlich.
- HTTP-Pulls bereinigen abgelaufene Leases vor dem Claim und committen die Wiederherstellung
  separat. Aktive unbestätigte Abbrüche behalten bis Bestätigung oder Ablauf ihre Kapazität.
- Artefaktkonflikte blockieren nur den betroffenen Task mit Grund `artifact_conflict`.
  Der Pull prüft begrenzt den nächsten Task; bestehende Bytes bleiben unverändert.

Pflichtchecks vom 2026-11-03: `make lint` grün (Ruff ohne Cache, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1107 bestandenen Tests** (**377 Integration**, **730 Unit**), **keine xfail oder Skips**.
Eine bestehende Starlette/httpx-Deprecation-Warnung. `make reference`: **4/4**.
Isolierte PostgreSQL-17-Testdatenbank. Die CI des finalen Heads wird im PR ausgewiesen.

Details: [RUNNER.md](RUNNER.md), [API.md](API.md), [ROUTING.md](ROUTING.md).
Adapter müssen kooperativen Abbruch und ihren Transport-Timeout einhalten; beliebiger
Python-Code kann vom Rahmen nicht zwangsweise beendet werden. Nichtleere Executor-Datei-
Ausgaben bleiben bis zur verifizierten Adapter-Anbindung gesperrt. Zentrale Fehler-/Fallback-
Regeln folgen in Etappe 10.

### Inbetriebnahme

Nicht ausgeführt: echte Logins, Anbieter-/Modellaufrufe, API-Keys, produktive DB-Verbindungen,
Serverinstallation, Deployment oder Release. Neue Laufzeittests verwenden Fake-Executors;
CLI-Adapter folgen ausschliesslich mit Simulationen für `ollama run`, `claude -p`, `codex exec`.
Inhaltliche Referenzfälle bleiben Owner-Aufgabe.

### Folgepunkte

- Aufwändige erneute Datei-/Datenschutzprüfungen bei ausgeschöpftem Budget reduzieren;
  Index auf `events(event_type, created_at)` für die Budgetabfrage ergänzen.
- Hangul Filler als mögliche Worttrenner gesondert behandeln; derzeit werden sie entfernt.
- Wird eine Routing-Regel nach Freigabe so verschärft, dass keine strukturell zulässige
  Lane bleibt, kann ein wartender Task noch terminal `blocked` werden. Dafür einen
  vorübergehenden Wartezustand oder eine ausdrückliche Owner-Entscheidung vorsehen.
- Veränderte Platzhalter, zwischen Versuchen entfernte Blocklisten-Begriffe und eine
  begrenzte Eskalation wiederholter `task.gate_error`-Events bleiben nachvollziehbar offen.

## Etappe 7 gemergt

PR: [#22](https://github.com/Kaancodm/control-deck/pull/22), vom Owner als `a092942` squash-gemergt.
Branch: `stage/07-router`, nach Squash-Merge von PR #21 auf `main 323d727` rebased.

Umgesetzt: reine Routing-Funktion, lokale Regeln und Modellwahl, Tagesbudgets je Lane,
Dry-run ohne Speicherung und dieselbe Routing-Entscheidung im Referenz-Runner.
`local_only` verwendet ausschliesslich Ollama unter Beachtung der Policy; keine zulässige
Lane führt zu `blocked`, ausgeschöpfte Tagesbudgets verschieben nur die Ausführung.
Der Claim zählt atomar mit DB-Zeit, auch bei Absturz oder fehlendem Ergebnis.

Review-Nacharbeit aus PR #21 in eigenen Commits:

- `4b5a509`: Mehrwort-Blocklisten erkennen beliebigen Leerraum, einschliesslich Tab und
  Zeilenumbruch; Originalpositionen und Rückabbildung bleiben exakt.
- `fa4bb07`: zusätzlich zu Cf wird die vollständige Unicode-15.0-Menge
  `Default_Ignorable_Code_Point` entfernt, einschliesslich CGJ, Variation Selectors und
  Hangul Filler. Tests sichern Secret-Erkennung und vollständige E-Mail-Ersetzung ab.

Pflichtchecks vom 2026-11-03: `make lint` grün (Ruff, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**916 bestandenen Tests** (**255 Integration**, **661 Unit**) und **2 bekannten strict xfail**
zur Worker-Identität; eine bestehende Starlette/httpx-Warnung. `make reference`: **4/4**.
Isolierte PostgreSQL-17-Testdatenbank, keine produktive Verbindung.
Die CI des finalen Heads wird im PR ausgewiesen. Details: [ROUTING.md](ROUTING.md).

Der Owner hat die gestapelte Fortsetzung bis Etappe 17 ohne Warten auf Review oder Merge
freigegeben. Jede Etappe behält einen eigenen Draft-PR. Review-Korrekturen erfolgen im
betroffenen Basis-PR, danach werden nachfolgende Etappen rebased. Kein automatischer Merge.
Etappe 18 wird ausschliesslich als Abnahme-Checkliste vorbereitet und nicht ausgeführt.

Folgepunkte aus PR #21: veränderte Platzhalter, ein zwischen Versuchen aus der Blockliste
entfernter Begriff und eine begrenzte Eskalation wiederholter `task.gate_error`-Events.
Die Worker-Identität und sichere Runner-Anbindung sind in Etappe 8 geschlossen.

## Etappe 6 gemergt

Basis: `main 7bf0e1a`, nach Merge von PR #19. Branch: `feat/etappe-6`.
PR: [#21](https://github.com/Kaancodm/control-deck/pull/21), extern reviewt und vom Owner
als `323d727` nach main squash-gemergt.

Der erste Commit `f4e0f8f` nimmt die beiden Folgepunkte aus Etappe 5 auf:

- NFKC-Schattenkopie mit Entfernung von Cf-Zeichen und Leerraum-Normalisierung. Fundstellen
  beziehen sich weiterhin auf den Originaltext. Blocklisten verwenden dieselbe Normalisierung.
  Begrenzte Verarbeitung weist übermässig komplexen Unicode-Text sicher ab.
- Lease-Polls schreiben `task.gate_error` je Task, Fehlercode und Quelle höchstens einmal in
  60 Sekunden. Datenbankzeit und Task-Lock schützen auch gleichzeitige Poller. Owner-Versuche
  bleiben einzeln auditiert.

Danach umgesetzt:

- `app/redaction`: sechs unterstützte Arten, taskgebundene stabile Platzhalter, verlustfreier
  Unicode-Roundtrip und vollständige Residualprüfung. Fremde oder unbekannte Platzhalter
  werden nicht aufgelöst; Originalwerte erscheinen nicht in normalen Fehlern oder Logs.
- Migration `0008` und `redaction_maps`: atomische Zuordnung unter Task-Lock, unveränderliche
  Einträge, eindeutige Originalwerte, Hashprüfung und geschützter Downgrade.
- Payload-Vorbereitung aus einmalig gelesenen Dateien und allen Text-Metadaten. Der
  Transport-Payload enthält keine Map. Sichere Artefakte sind atomar, unveränderlich und
  privat (`0700`/`0600`); SHA-256 bezeichnet exakt die geschriebenen Bytes.
- Der Fake-/Referenz-Worker erhält nur vorbereiteten Text. Rückabbildung erfolgt lokal.

Details: [REDAKTION.md](REDAKTION.md), [DATENBANK.md](DATENBANK.md).

## Abnahme

Lokale Pflichtchecks vom 2026-11-03:

- `make lint`: Ruff, Format und Mypy grün.
- `uv run alembic -x target=test check`: keine Schemaabweichung; isolierte PostgreSQL-17-Test-DB.
- `make test`: **759 bestanden**, davon **224 Integrationstests** und **535 Unit-Tests**.
  Zusätzlich **2 erwartete Fehlschläge (strict xfail)**; sie zählen nicht als bestanden.
  Eine bestehende Starlette/httpx-Deprecation-Warnung.
- `make reference`: **4/4** bestehende Fälle bestanden.

Die GitHub-CI des Etappe-6-Heads war grün. Das externe Review und der Owner-Merge sind
abgeschlossen; die beiden P2-Nacharbeiten sind in Etappe 7 separat behoben.

## Historische Grenze nach Etappe 6

Die damals noch konservative Worker-API wurde in Etappe 8 durch vorbereiteten Versandtext
und sichere Versuchsdaten ersetzt. Die beiden Worker-Identitäts-Regressionen bestehen jetzt
als normale Tests. Inhaltliche Referenzfälle bleiben Owner-Aufgabe; die vier vorhandenen
Fälle bleiben erhalten. Es gibt keine echten Anbieteraufrufe oder Server-Eingriffe.

## Inbetriebnahme

Echte Logins, CLI-/Modellaufrufe, Anbieter-Kontingente und Serverinstallation werden nicht
getestet oder ausgeführt. Dafür erforderliche Prüfungen bleiben in jedem Etappen-PR
explizit als Inbetriebnahme offen. Die lokalen Pflichtchecks verwenden nur Simulationen
und isolierte Testdatenbanken.

## Etappe 14: implementiert und vollständig lokal geprüft

Draft-PR [#29](https://github.com/Kaancodm/control-deck/pull/29), Branch `stage/14-knowledge`.
Elternbasis: finaler Etappe-13-Head `aa95b47c4b07f46b41adbdfeb79a046cc2766f15`, CI grün.
Der Rebase übernimmt die Korrektur des historischen UI-Migrationstests: dieser prüft
Revision `0012` ausdrücklich, statt spätere KB-Tabellen fälschlich ihr zuzuordnen.

Umgesetzt: eigener versionierter `deck_kb.v1`-Testvertrag, pgvector mit Klassenfilter vor
Rangfolge, unveränderliche Dokumente und Kontext-Snapshots, ausdrückliche Datenklassen,
lokale Fake-Embeddings, Freigabe-Hashes und wiederaufnehmbare explizite Owner-Exporte.
Owner-API und UI bieten Suche, Kontextvorschau, Export aus `done` mit CSRF/Bestätigung
und sicheren Inbox-Import. Keine KB-Aufrufe unter Task-/Lease-Locks.

Frische Pflichtchecks vom 2026-11-03 auf Code-Stand `6b9d470`: **1991 Tests bestanden**,
keine Skips oder xfail; eine bestehende Starlette/httpx-Deprecation-Warnung.
`make lint`, zentraler Alembic-Check und separate KB-Migration samt Check sind grün.
`make reference` und Ollama-Fake-CLI-Referenzmodus: jeweils **4/4**. Hook-Prüfungen grün.
Echte Chromium-Läufe bei 390 Pixeln mit und 1024 Pixeln ohne JavaScript sind enthalten.
Zentrale und KB-Testdatenbank sind getrennte isolierte PostgreSQL-17-/pgvector-Datenbanken.
Der folgende Commit ändert ausschliesslich Dokumentation; finale Head-SHA und dessen CI
werden im PR ausgewiesen. Externes Review bleibt offen, der PR bleibt Draft. Kein Merge.

Die Schlussprüfung identifizierte eine gemeinsame Fehlerursache früherer sporadischer
Codex-`idle`-Fehler: Die generierte Task-UUID im Prompt konnte wie eine Telefonnummer
aussehen. Die Datengrenze blockierte dann einen sauberen Cloud-Auftrag oder erzeugte eine
unnötige Redaktionsabbildung. Interne IDs bleiben nun in den strukturierten Daten und
Archivpfaden; die generierte Prompt-Überschrift enthält sie nicht mehr. Der vollständige
Scanner bleibt unverändert. Sechs feste Regressionen scheiterten vor der Korrektur;
anschliessend bestanden alle 62 betroffenen Tests und der obige Gesamtlauf. Derselbe
Zahlenblock in Nutzertext wird weiterhin blockiert beziehungsweise redigiert.

### Inbetriebnahme

Produktive Schema-Kompatibilität, Schreibrechte, echtes lokales Embedding-Modell und
Dimension sowie semantische Suchqualität bleiben offen. Das Live-Profil ist gesperrt.
Keine echten Logins, API-Keys, Anbieteraufrufe, Serveränderungen oder neuen inhaltlichen
Referenzfälle. Details: [WISSENS_DB.md](WISSENS_DB.md).

## Etappe 15: lokal abgeschlossen

Draft-PR: [#30](https://github.com/Kaancodm/control-deck/pull/30), Branch
`stage/15-hardening`, auf den finalen Etappe-14-Head `f43b70f` rebased.

Die vollständige Owner-UI ist mit der äussersten HTTP-Schutzmiddleware integriert.
Validierte Host-/Origin-Grenzen, begrenzte Requests, sichere Konfigurations- und
Bindungsprüfungen sowie redigierte Fehler gelten vor den Handlern. Die UI verwendet
bei konfigurierter Public-Origin denselben validierten Wert; Forwarding-Header verleihen
keine Rechte.

Migration `0014` und ihre UI-Anbindung liefern Passwortbindung, 30 Minuten
Inaktivitätsablauf, eine unverlängerbare absolute 12-Stunden-Grenze, Drosselung je
Client-Kennung (Peer-Adresse der Verbindung, nur als Digest gespeichert), globale Login-Sperre
nach 20 Fehlern in 24 Stunden und sichere Auth-Ereignisse. Die Sperre erscheint mit HTTP 423
ohne Passwortformular. Seit der P1-Nachbesserung endet sie ausschliesslich mit der
ausdrücklichen lokalen Entsperrung; Neustarts erhalten Sperre und Tagesbudget. «Überall abmelden» widerruft alle bestehenden Sessions
mit Owner-/CSRF-Prüfung.
Die Sperrreihenfolge bleibt Login-State vor Session, auch in der HTTP-Anbindung.

Frischer Gesamtlauf auf dem Code- und Teststand von `397d723`: **2088 bestanden**, keine
Skips oder xfail; eine bestehende Starlette/httpx-Deprecation-Warnung. `make lint`, zentraler
Alembic-Check, separate KB-Migration samt Check und Hook-Prüfungen sind grün.
`make reference` und Ollama-Fake-CLI-Referenzmodus: jeweils **4/4**. Enthalten sind
Chromium-Läufe bei 390 Pixeln mit und 1024 Pixeln ohne JavaScript, mit globaler Abmeldung
zweier Browser-Sessions, Sperranzeige nach Neuladen und erneuter Anmeldung nach Entsperrung.
Zentrale und KB-Testdatenbank sind getrennte isolierte PostgreSQL-17-/pgvector-Datenbanken.

Ein vorheriger Gesamtlauf hatte **2080 bestandene Tests und einen Fehlschlag**: ein geerbter
Codex-Poll-Test erhielt beim ersten Queue-Poll unerwartet `idle`. Die Ursache ist inzwischen
deterministisch reproduziert: Die generierte UUID in der Prompt-Überschrift konnte zufällig
wie eine Telefonnummer aussehen. Die erneute Datenschutzprüfung blockierte deshalb einen
bereits freigegebenen synthetischen Auftrag vor jedem Prozessstart. Die gemeinsame Korrektur
aus Etappe 14 entfernt die interne UUID aus der Prompt-Überschrift; strukturierte IDs,
Artefaktpfade und die vollständige Inhaltsprüfung bleiben erhalten. Regressionen prüfen
auch, dass Telefonnummern im eigentlichen Inhalt weiterhin blockiert oder redigiert werden.
Sichere Zustandsdiagnostik bleibt erhalten; keine Assertion wurde abgeschwächt und kein
Retry hinzugefügt. Der neue Gesamtlauf nach dieser Korrektur ist vollständig grün.
Der anschliessende Rebase übernimmt ausschliesslich die finalen Etappe-14-Nachweise;
Code und Tests sind zum geprüften Stand unverändert. Die CI des exakten finalen Heads
wird im PR separat ausgewiesen.
Externes Review bleibt offen, der PR bleibt Draft. Kein Merge.

### Inbetriebnahme

Offen bleiben reale Tailnet-/TLS-Bindung, externe Erreichbarkeit, private Konfiguration,
Betriebsrechte und echte Login-Einrichtung. Keine Netzwerk-Scans, echten Logins,
Anbieter-/Modellaufrufe, API-Keys, produktiven DB-Verbindungen oder Serveränderungen.
Details: [SECURITY.md](SECURITY.md), [UI.md](UI.md). Keine neuen inhaltlichen Referenzfälle.

## Etappe 16: implementiert und vollständig lokal geprüft

Draft-PR [#31](https://github.com/Kaancodm/control-deck/pull/31), Branch `stage/16-operations`,
gestapelt auf Etappe 15 (`066ec26`). Kein Merge.

Migration `0015`, append-only Betriebsereignisse, 14-/90-Tage-Aufbewahrung und
wiederaufnehmbare Retention sind integriert. systemd-Benutzervorlagen und begrenzter
Simulations-Supervisor bleiben Repo-Code; keine Installation oder Dienststarts auf einem
Betriebsserver. Echte Prozessgruppenbeendigung samt Unterprozess und anschliessendem
Lease-Retry sind belegt; Dienstende erzeugt keinen Owner-Abbruch. Ein Downgrade verweigert
das Löschen vorhandener Betriebsnachweise.

Ein atomar publizierter Backup-Satz in Manifestversion 2 verlangt beide Datenbanken:
zentral `public` und separat `public`/`deck_kb` samt pgvector. Pro DB teilen Dump,
Revision und schemaqualifizierte Tabellenzähler einen exportierten Snapshot. Fehlende
Wissenskonfiguration oder ein fehlgeschlagener zweiter Dump publizieren kein zentrales
Ersatzbackup. CLI, Update und Restic verlangen vollständige Sätze. Pruning überspringt und auditiert
seit der P1-Nachbesserung defekte Sätze; alte zentrale Manifestversion-1-Sätze werden weder
als vollständig akzeptiert noch automatisch gelöscht.

Restore verlangt zwei leere `_test`-Ziele, getrennt voneinander und von beiden Quellen.
Beide Archive, Prüfsummen, Revisionen und Ziele werden vor dem ersten Eingriff validiert.
Nur leeres `public` wird ohne `CASCADE` vorbereitet. Normale Schemas wie `pgdata` werden
nicht als PostgreSQL-Systemschemas übergangen. Beide Schema-/Zeilennachweise, der
Wissensvertrag, die pgvector-Version und beide Alembic-Checks müssen vor einem Erfolgsereignis
passen. Teilfehler bleiben `restore_partial_failure`; beide Diagnoseziele gelten als nicht
freigegeben und werden nicht automatisch bereinigt. Es gibt keine verteilte Transaktion.

Frischer Gesamtlauf auf dem finalen Code- und Teststand `5f4243b`: **2210 Tests bestanden**,
keine Skips oder xfail; eine bestehende Starlette/httpx-Deprecation-Warnung. `make lint`,
zentrales Upgrade und Alembic-Check, separates KB-Upgrade und KB-Check sowie Hooks sind
grün. Beide Referenzmodi bestehen mit **4/4**. Der finale Folgecommit aktualisiert nur
Dokumentation; Anwendung und Tests bleiben identisch. Finale Head-SHA und CI stehen im PR.

Die gezielte Erweiterungsprüfung besteht mit **92 Tests**. Vier zuvor rote Regressionen
sichern normale Schemas in beiden Quellen und beiden Zielen. Der echte PostgreSQL-17-/
pgvector-Roundtrip weist Dokumentinhalt, Vektor-Suche und je DB konsistente Zähler trotz
paralleler Änderungen nach. Fehlende, beschädigte oder widersprüchliche Wissensarchive,
befüllte Ziele, ungültiger Wissensvertrag, Restic-Teilsätze und Restore-Teilfehlerversuche
bleiben ohne Erfolgsnachweis. Der Gesamtlauf enthält ausserdem Chromium bei 390/1024 Pixeln
und Offline-`systemd-analyze verify`. Externes Review bleibt offen; der PR bleibt Draft.

### Inbetriebnahme

Offen bleiben reale Bindung/TLS, User-Manager und Lingering, Datei-/Backup-Ziele, Speicher,
Off-site-Kopie und Restore, Restic-Konfiguration sowie echte Lane-Isolation und Anmeldung.
Beide Datenbanken sind im Backup-Satz enthalten; ein konsistenter Gesamtzeitpunkt über
beide DBs und Laufdateien bleibt separat nachzuweisen. Die Runtime-Wissensvariable gilt
nur für Wartung und aktiviert keinen Live-Wissensadapter.
Der wöchentliche Restore-Timer ist eine Trockenlauf-Erinnerung ohne `--apply`, kein
Restore-Nachweis; automatische Bereitstellung leerer Ziele bleibt Inbetriebnahme.
Keine echten Logins, Anbieteraufrufe, API-Keys, produktiven Verbindungen oder Serveränderungen.
Details: [OPERATIONS.md](OPERATIONS.md). Etappe 18 bleibt eine vorbereitete Checkliste.

## Review-Nachbesserung P1 (Befunde 0–7)

Branch `fix/review-p1` basiert auf `main` nach Referenz-PR #36. Die ausdrücklich genannten
Review-Befunde sind die Quelle; der externe Bericht wurde nicht ins Repository übernommen.

- Git ignoriert private Umgebungs- und Runner-Dateien mit Ausnahme `.env.example`;
  CI prüft die Regeln mit `git check-ignore`. Der Claude-Guard erfasst direkte Bash-Zugriffe.
- Neustarts beobachten die Login-Sperre nur. Tagesbudget und Fensterbeginn bleiben erhalten;
  nur `make unlock-login` entsperrt. Argon2 hält keine DB-Transaktion oder Zeilensperre;
  Session, Sperre und Budgets werden danach erneut geprüft. UI-DB-Handler laufen im Threadpool.
- Migration `0017` begrenzt anonyme Sessions je Client und GET-Aufrufe der Login-Seite.
  Kapazität und Rate liefern 429; eine Flut eines Clients sperrt den anderen Owner nicht aus.
- Updates merken Revision und aktive Units, stellen bei Fehler den vorigen Checkout wieder her,
  starten zuvor aktive Units im `finally` und melden Exit 78 samt fehlgeschlagenem Audit-Ereignis.
- Betriebssubprozesse erhalten einen explizit konfigurierbaren Werkzeug-PATH. Die Backup-Unit
  verwendet das konfigurierte private Schreibziel; Restic braucht keine Home-Schreibrechte.
- Defekte Backup-Sätze werden bei Retention übersprungen und auditiert. Erfolgreicher Dump
  und Retention-Status bleiben getrennt; ein Restic-Fehler setzt den Gesamtstatus auf `failed`.
- Referenz-Executoren verwenden den vorbereiteten Eingang. Negative Tests für 007/013
  erkennen fehlende Redaktion auch bei konstanter Worker-Antwort; 010 prüft die echte
  Datenschranke. Die freigegebenen Dateien 013/014 bleiben unverändert.
- Owner-Health liest den Alembic-Head dynamisch; CI synchronisiert Abhängigkeiten mit `--frozen`.

Gezielte Regressionen, Lint, beide Alembic-Checks, Hooks und `make reference` (14/14) sind grün.
Der PR enthält den Gesamtnachweis mit Testzahl und CI-Status. Alle Datenbanken und Git-Repositories
für diese Tests sind lokal und wegwerfbar; Dienst-, Anbieter- und Off-site-Aufrufe sind simuliert.

**Inbetriebnahme:** Reale systemd-Sandbox und Werkzeugpfade, Restic-Transport/Restore und die
Bereitschaft wieder gestarteter Dienste bleiben offen. Ein Code-Rücksprung macht keine bereits
ausgeführte DB-Migration rückgängig; die Kompatibilität des alten Codes zum neuen Schema
muss betrieblich geprüft werden. Keine Serverzugriffe, echten Logins oder Merges durchgeführt.
