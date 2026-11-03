# Stand des Repositorys

## Etappe 17 implementiert und lokal geprueft

Draft-PR: [#32](https://github.com/Kaancodm/control-deck/pull/32), gestapelt auf Etappe 16.
Migration `0016` zaehlt ausschliesslich endgueltig angenommene Ergebnisse, atomar mit
Ergebnis und Statuswechsel. Zaehler beginnen bei dieser Migration; positive Vorpruefungen
bleiben getrennt. 15 SQL-Integrationstests pruefen Konkurrenz, verworfene Ergebnisse,
Rollback, Acceptance und Migrationsschutz.

Audit zeigt begrenzte sichere Metadaten aus Auftrags-, Betriebs- und Auth-Ereignissen;
CSV entspricht einer sichtbaren Seite. Geschuetzte Lane-Ansicht, Warnband und
`/api/v1/health` nutzen lokale Beobachtungen ohne Anbieterpruefung. Owner-Token oder eine
gueltige Session erlauben Gesundheitsdetails; anonym bleibt nur der Minimalstatus.
Die Session gilt nun fuer den Root-Pfad, CSRF bleibt auf `/ui`; alte Session-Cookies
werden beim Setzen und Abmelden entfernt. Schreib-APIs erhalten keine Session-Rechte.

Nachbesserung der Zaehlernachweise: Die unveraenderte Basis `1f2995f` besteht bereits
34 gezielte Tests. Mit vier zusaetzlichen Testfaellen bestehen **38 gezielte Tests**:
Einmalzaehlung; drei angenommene Fehler und danach Erfolg mit Gesamtzaehler 4 und
Fehlerserie 0; unveraenderte Zaehler, Fehler- und Erfolgszeitpunkte bei Doppelabgabe,
abgelaufener Lease oder Deadline. Rollback- und Konkurrenznachweise bleiben erhalten.
Die Produktionslogik der Zaehler benoetigt keine Korrektur. Auf der nachgebesserten
Etappe-16-Codebasis `5f4243b` bestehen **123 gezielte Tests** fuer Audit, Gesundheit
und Zaehler; `make lint` ist gruen. Audit und CSV erhalten die bekannten Codes
`restore_failed` und `restore_partial_failure`, damit Teilfehler sichtbar bleiben.
Rohdetails und unbekannte Restore-Gruende bleiben ausgeblendet. Die Vollsuite und
Pflichtchecks auf dieser Basis folgen; `CHANGELOG.md` enthaelt den Etappe-17-Eintrag.

Bisherige Pflichtchecks auf isolierten PostgreSQL-17-Testdatenbanken: **2292 Tests bestanden**,
keine xfail oder Skips. Darin enthalten sind **118 neue Etappe-17-Tests** (52 Integration,
66 Unit), einschliesslich echter Chromium-Smokes bei 390 Pixeln mit JavaScript und
1024 Pixeln ohne JavaScript. Geprueft sind Audit-Filter, Seitenwechsel, CSV, Warnband,
Session-Health, Logout, unklassifizierte Fehler, konkurrierende Ergebnisse und sichere
Fehlerantworten. `make lint`, zentrale und KB-Alembic-Pruefung, beide Referenzmodi mit
je **4/4** sowie beide Hook-Suites sind gruen. Eine bestehende Starlette/httpx-Warnung.

Die bisherige Vollsuite lief auf `44a22c5`. Der damalige Eltern-Rebase auf den
Etappe-16-Head `a22149b` aenderte ausschliesslich `docs/STAND.md`; Anwendung, Migrationen
und Tests sind identisch. CI des finalen Heads steht im PR. Der Abnahme-Entwurf aus
Checkpoint `a2e6ff3` liegt unter `docs/ABNAHME_CHECKLISTE_ENTWURF.md`; sein Backup-Kriterium
verlangt nun ausdruecklich beide Datenbanken, zwei leere Ziele und fehlgeschlagene Teil-Restores.
Etappe 18 wird damit nicht gestartet. Externes Review durch einen anderen Anbieter
und Owner-Merge bleiben ausstehend.

### Inbetriebnahme

Anbieter-Kontingente bleiben unbekannt, Live-Profile gesperrt. Echte Anmeldungen,
Lane-Verfuegbarkeit, Betriebsrechte und Serveraenderungen wurden nicht geprueft oder
vorgenommen. Die Gesamt-Abnahme bleibt offen.

## Etappe 13 implementiert und lokal geprueft

Die Codex-Lane ist im gestapelten [Draft-PR #28](https://github.com/Kaancodm/control-deck/pull/28)
auf Etappe 12 umgesetzt. Der versionierte JSONL-Vertrag, eine Fake-CLI und der gemeinsame
Reinraum-Simulator akzeptieren ausschliesslich abgeschlossene Textausgaben. Genau die
freigegebenen und archivierten Payload-Bytes erreichen stdin; `local_only` bleibt bei Ollama.
Tool-Ereignisse, Teilantworten und ungueltige Protokolle werden verworfen. Das Codex-Profil
bleibt vom Claude-Profil getrennt; Abbruch und Timeout behalten eine eigene Bereinigung.

Pflichtchecks auf isolierter PostgreSQL-17-Testdatenbank: **1896 Tests bestanden**, davon
**601 Integrationstests** und **1295 Unit-Tests**; keine xfail oder Skips. `make lint`,
`uv run alembic -x target=test check` sowie beide Referenzmodi mit je **4/4 Faellen** sind
gruen. Eine bestehende Starlette/httpx-Deprecation-Warnung. Die Tests belegen auch Lane-Pause
und erlaubten Fallback, 60-/240-Sekunden-Backoff ueber neue Runner hinweg sowie das globale
Versuchsbudget. Die CI des finalen Heads wird im PR ausgewiesen.

Inbetriebnahme bleibt offen: vollstaendige Tool-Deaktivierung, ausgeschlossene interne
Subscription-Retries, Rootless-/Netzgrenzen, Image-Build und echte Anmeldung sind nicht
nachgewiesen. Das Live-Profil startet keinen Prozess und ist nicht aktivierbar; ein
Simulationserfolg ist kein Live-Nachweis. Keine echten Logins, Anbieteraufrufe, API-Keys
oder Serveraenderungen. Details und Quellen: [LANES.md](LANES.md).

Externes Review durch einen anderen Anbieter und Owner-Merge bleiben ausstehend.

## Etappe 12 bereit fuer externes Review

Branch: `stage/12-claude`, gestapelt auf Etappe 11 (`stage/11-ui-chat`, PR #26).
Draft-PR: [#27](https://github.com/Kaancodm/control-deck/pull/27).
Umgesetzt sind ein versionierter Claude-CLI-Vertrag, streng begrenzte Reinraum-Befehle
und Simulationen mit Fake-Container und Fake-CLI. Es werden nur gepruefte
Versandbytes uebergeben; `local_only` bleibt bei Ollama. Das Live-Profil bleibt gesperrt,
bis Werkzeugabschaltung, Rootless-Isolation und Netzgrenzen bei der Inbetriebnahme belegt
sind. Auth-/Policy-Fehler pausieren die ausfuehrende Lane und fuehren ueber die zentrale
Fehlerlogik zum zulaessigen Fallback. Rate-Limits erhalten 60/240 Sekunden Backoff ueber
Runner-Neustarts hinweg; Acceptance bleibt innerhalb des Budgets auf derselben Lane.
Abbruch, Deadline und Lease-Verlust stoppen den Versuch; Cleanup hat ein eigenes Budget.

Nach Rebase auf Etappe 11: `make lint` gruen, `uv run alembic -x target=test check`
ohne Schemaabweichung, **1714 Tests bestanden** (**575 Integration**, **1139 Unit**),
keine Skips oder xfail. Davon sind **136 neue Etappe-12-Tests** (26 Integration, 110 Unit).
`make reference`: **4/4**. Eigene isolierte PostgreSQL-17-Testdatenbank; eine bestehende
Starlette/httpx-Deprecation-Warnung. Die Vollsuite lief auf `d47827a`; der letzte Eltern-
Rebase aenderte ausschliesslich Dokumentation und zwei historische Migrationstests,
die danach erneut bestanden. Anwendung, Migrationen und Konfiguration sind identisch.
Die CI des finalen Heads und die geprueften Commits stehen im PR.

### Inbetriebnahme

Die gepinnte Image-Vorlage ist nicht gebaut. Keine echten Container-/CLI-/Anbieteraufrufe,
Logins, API-Keys oder Serveraenderungen. Das Live-Profil bleibt gesperrt; Rootless-,
Werkzeug-/MCP-/Hook- und Netzgrenzen sowie Konto-/Abo- und Protokollverhalten muessen
separat nachgewiesen werden. Simulation bestaetigt keine Live-Bereitschaft. Keine neuen
inhaltlichen Referenzfaelle, kein Self-Merge. Details: [LANES.md](LANES.md).

## Etappe 11 bereit fuer externes Review

Draft-PR: [#26](https://github.com/Kaancodm/control-deck/pull/26).
Branch: `stage/11-ui-chat`, gestapelt auf Etappe 10 (`stage/10-error-paths`, PR #25).

Umgesetzt:

- Themebare Jinja2-Oberflaeche mit zentralen CSS-Variablen, wiederverwendbaren Partials und
  lokalem HTMX. Das spaetere Design aus den Owner-Komponenten bleibt separat testbar.
- Argon2id-Passwortlogin, serverseitige Sessions, CSRF, Rotation, Ablauf, Logout und
  persistente Login-Drosselung. Migration 0012 speichert ausschliesslich Token-Hashes.
- Task-Formular, begrenzte sichere UTF-8-Dateien, Liste, Detail, Audit-Zeitlinie,
  Start-Freigabe, Ergebnis-Review und Abbruch ueber gemeinsame API-/UI-Geschaeftsfunktionen.
- Schreibgeschuetzte Fehlerliste, Lane-Zustand, Pause und bestaetigter Not-Aus. Blockierte
  Tasks entsperrt nur der Owner ausdruecklich (`blocked -> queued`, Pflichtbegruendung ab
  10 Zeichen, Audit-Event `task.unblock` mit `actor=owner`); Datenschranke und Routing
  pruefen beim Lauf erneut. Review-Ablehnung fuehrt zu `failed`. Auftrags-GETs schreiben nicht.
- Lokaler Fake-CLI-Chat mit explizitem Kontext und unverbindlichem validiertem Entwurf.
  Keine Aktionsrechte, kein dauerhaft gespeicherter Verlauf und keine automatische
  Uebernahme von Chat-Kontext in Cloud-Auftraege. Das Live-Profil bleibt unverfuegbar.
- Gespeicherte, bereits rueckabgebildete Ergebnisse werden escaped angezeigt. Keine zweite
  Rueckabbildung, keine Redaction-Map im Template. Exakte Rueckabbildungspositionen bleiben
  mangels gespeicherter Provenienz ein Folgepunkt; die eingefrorenen Schemas bleiben erhalten.

Pflichtchecks vom 2026-11-03: `make lint` gruen (Ruff, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1578 bestandenen Tests** (**549 Integration**, **1029 Unit**), **keine xfail oder Skips**.
Darin enthalten: echte Chromium-Smokes bei **390 Pixeln mit JavaScript** und **1024 Pixeln
ohne JavaScript** mit synthetischem Login, Upload, Freigabe, Fake-Worker, Review, `done`,
Chat-Entwurf und Logout. Eine bestehende Starlette/httpx-Deprecation-Warnung.
`make reference`: **4/4**; Ollama-Fake-CLI-Referenzlauf ebenfalls **4/4**. Isolierte
PostgreSQL-17-Testdatenbank. Die CI des finalen Heads wird im PR ausgewiesen.
Details und begruendete Abhaengigkeiten: [UI.md](UI.md), [ETAPPEN/11-ui.md](ETAPPEN/11-ui.md).

### Inbetriebnahme

Keine echten Logins, Modell-/Anbieteraufrufe, API-Keys oder Serveraenderungen. Offen bleiben
sichere Bindung/TLS, echte Zugangsdaten und Geraete, nachgewiesene Live-Ollama-Isolation mit
gemeinsamer Modell-Zulassung sowie das spaetere finale Design. Simulation ist kein Live-Nachweis.
Externes Review erfolgt durch einen anderen Anbieter; es wird nicht selbst gemergt.

## Etappe 10 bereit fuer externes Review

Draft-PR: [#25](https://github.com/Kaancodm/control-deck/pull/25).
Branch: `stage/10-error-paths`, gestapelt auf Etappe 9 (`stage/09-ollama`, PR #24).

Umgesetzt:

- Migration `0011`: dauerhafter Retry-Cursor, festgehaltene Lane, Fehlerzaehler und
  naechster Ausfuehrungszeitpunkt ausserhalb der eingefrorenen Schemas.
- Acceptance auf derselben Lane bis zum globalen Limit; Rate-Limits mit 60/240 Sekunden
  Backoff, danach Fallback; Timeout und Nichtverfuegbarkeit zur naechsten erlaubten Lane;
  ein taskweiter interner Retry. Keine versteckten Adapter-Wiederholungen.
- Anbieterbedingte Auth-/Policy-Fehler pausieren die Lane atomar mit Ergebnis und Event.
  Erneutes Fencing verhindert Wirkungen falscher, doppelter oder verspaeteter Ergebnisse.
- Queue-Polls und Neustarts erhalten den Fortschritt. Backoff wird auch im atomaren
  Claim geprueft. Strukturell erschoepfte Ketten enden als `failed`; voruebergehend
  nicht verfuegbare erlaubte Lanes bleiben wartend.
- `local_only` bleibt Ollama; Datenschranke und Owner-Abbruch loesen keinen Fallback aus.
  Nur eine abgelaufene ausdrueckliche Owner-Deadline erzeugt `expired`.
- Owner-Fehlerliste `GET /api/v1/tasks/failed` ohne Schreibeffekt oder Task-Lock, mit
  sicherem effektivem Fehlercode. Keine Wiederbelebung oder Entsperrung terminaler Tasks.

Pflichtchecks vom 2026-11-03: `make lint` gruen (Ruff ohne Cache, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1455 bestandenen Tests** (**499 Integration**, **956 Unit**), **keine xfail oder Skips**.
Eine bestehende Starlette/httpx-Deprecation-Warnung. `make reference`: **4/4**;
Ollama-Fake-CLI-Referenzlauf ebenfalls **4/4**. Isolierte PostgreSQL-17-Testdatenbanken.
Die CI des finalen Heads wird im PR ausgewiesen. Externes Review und Merge bleiben
beim Owner; es wurde kein eigenes formales Review und kein Merge ausgefuehrt.
Details: [ETAPPEN/10-fehlerpfade.md](ETAPPEN/10-fehlerpfade.md).

### Inbetriebnahme

Alle Laeufe verwenden Simulationen und Fake-CLIs. Echte Anmeldungen, Anbieter-Kontingente,
Modellaufrufe und Live-Fehler bleiben ungeprueft. Keine API-Keys oder Serveraenderungen.
Das Ollama-Live-Profil bleibt gesperrt; neue inhaltliche Referenzfaelle bleiben Owner-Aufgabe.

## Etappe 9 bereit fuer externes Review

Draft-PR: [#24](https://github.com/Kaancodm/control-deck/pull/24).
Branch: `stage/09-ollama`, nach den externen Review-Korrekturen auf
`stage/08-runner` / PR #23 (`dc81401`) rebased.

Umgesetzt sind ein begrenzter Ollama-CLI-Prozessadapter, Lane-Gesundheit mit Migration
`0010`, simulierte Host-E2E-Laeufe und der ausdrueckliche Referenz-Schalter
`--executor ollama-fake-cli --model <simulationskennung>`. Die neue Executor-Identitaet,
Pflicht-Lane und Artefaktkonflikt-Behandlung aus der korrigierten Etappe 8 bleiben erhalten.

Pflichtchecks vom 2026-11-03: `make lint` gruen (Ruff ohne Cache, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1264 bestandenen Tests** (**425 Integration**, **839 Unit**), **keine xfail oder Skips**.
Eine bestehende Starlette/httpx-Deprecation-Warnung. `make reference`: **4/4**;
der zusaetzliche Ollama-Fake-CLI-Referenzlauf ebenfalls **4/4**. Isolierte PostgreSQL-17-
Testdatenbank. Zwei parallele Fake-CLI-Runner schliessen 20 Auftraege jeweils genau einmal
ab. Neue inhaltliche Referenzfaelle wurden nicht erstellt. Die CI des finalen Heads wird
im PR ausgewiesen; externes Review und Merge bleiben beim Owner.

### Inbetriebnahme

Das Live-Profil ist nicht aktivierbar und startet keinen Prozess. Es bleibt
`unavailable` / `live_profile_unavailable`. Ein `ok` mit `simulation_ready` sowie
`last_success_at` belegen ausschliesslich die Fake-Inventarpruefung, keine Live-Bereitschaft.
Die offizielle CLI bietet keinen No-Pull-Schalter; Cloud-Sperren muessen beim Daemon gelten,
multimodale CLI-Pfade koennen promptgesteuert Hostdateien lesen, und Exit-Code null allein
belegt nicht jeden Abschluss. Eine spaetere Live-Anbindung benoetigt nachweisbare Download-/
Cloud-Sperren, Dateisystem-Isolation und einen eindeutigen Abschlussnachweis. Quellen und
Details: [LANES.md](LANES.md). Keine echten Modellaufrufe, Logins, API-Keys, Serveraenderungen
oder produktiven Verbindungen ausgefuehrt.


## Etappe 8 bereit fuer externes Review

Draft-PR: [#23](https://github.com/Kaancodm/control-deck/pull/23).
Branch: `stage/08-runner`, nach Squash-Merge von PR #22 auf `main a092942` rebased.
Der Owner hat die gestapelte Fortsetzung bis Etappe 17 freigegeben. Etappe 18 wird nur als
Abnahme-Checkliste vorbereitet; kein Merge oder Release durch den Agenten.

Umgesetzt:

- Lanegebundene Claims mit Owner-Freigabe, Policy, Versuchs-/Tagesbudget und globaler
  Parallelitaet (Standard 1). `local_only` bleibt Ollama; nur die geclaimte Lane wird belastet.
- Migration `0009` und typisierte `settings` fuer Pause, Not-Aus und Lane-Pause. Claims und
  Steuerung sind atomar serialisiert; bestehende Abbruchanforderungen bleiben gesetzt.
- Finale Datei-Hashes aus genau dem gelesenen Versandtext muessen zur Owner-Freigabe passen.
  Maps und Vorbereitung werden vor Payload-Auslieferung committed. Worker sehen keine
  Originalauftraege, Map oder frueheren Ergebnisse.
- Expliziter Start, Heartbeat und Steuerentscheide `continue`, `cancel`, `expired`,
  `lost_lease`. Expiry wird vor dem Stop committed; Lease-Timeout ist nie `expired`.
- Direkter Host-Runner ohne offene Ausfuehrungs-Transaktion beim Executor-Aufruf; lokale
  Rueckabbildung und Acceptance vor erneuter Ergebnis-Fencing-Pruefung.
- Private unveraenderliche Payload-/Ergebnisartefakte und sichere Laufmetadaten-Logs.
- Review-Nacharbeit `636d724`: deaktivierte erlaubte Lanes fuehren zu `deferred`, nicht
  terminal zu `blocked`. Nach Reaktivierung wird derselbe wartende Task korrekt geclaimt.
- Nacharbeit zum externen Review auf `5fd9704`: eigene Token je Lane bestimmen die
  authentifizierte Identitaet. Doppelte Tokens, Owner-Kollisionen und Lane-Spoofing werden
  abgewiesen; Executor-Identitaet und Queue-Lane sind ausdruecklich erforderlich.
- HTTP-Pulls bereinigen abgelaufene Leases vor dem Claim und committen die Wiederherstellung
  separat. Aktive unbestaetigte Abbrueche behalten bis Bestaetigung oder Ablauf ihre Kapazitaet.
- Artefaktkonflikte blockieren nur den betroffenen Task mit Grund `artifact_conflict`.
  Der Pull prueft begrenzt den naechsten Task; bestehende Bytes bleiben unveraendert.

Pflichtchecks vom 2026-11-03: `make lint` gruen (Ruff ohne Cache, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**1107 bestandenen Tests** (**377 Integration**, **730 Unit**), **keine xfail oder Skips**.
Eine bestehende Starlette/httpx-Deprecation-Warnung. `make reference`: **4/4**.
Isolierte PostgreSQL-17-Testdatenbank. Die CI des finalen Heads wird im PR ausgewiesen.

Details: [RUNNER.md](RUNNER.md), [API.md](API.md), [ROUTING.md](ROUTING.md).
Adapter muessen kooperativen Abbruch und ihren Transport-Timeout einhalten; beliebiger
Python-Code kann vom Rahmen nicht zwangsweise beendet werden. Nichtleere Executor-Datei-
Ausgaben bleiben bis zur verifizierten Adapter-Anbindung gesperrt. Zentrale Fehler-/Fallback-
Regeln folgen in Etappe 10.

### Inbetriebnahme

Nicht ausgefuehrt: echte Logins, Anbieter-/Modellaufrufe, API-Keys, produktive DB-Verbindungen,
Serverinstallation, Deployment oder Release. Neue Laufzeittests verwenden Fake-Executors;
CLI-Adapter folgen ausschliesslich mit Simulationen fuer `ollama run`, `claude -p`, `codex exec`.
Inhaltliche Referenzfaelle bleiben Owner-Aufgabe.

### Folgepunkte

- Aufwaendige erneute Datei-/Datenschutzpruefungen bei ausgeschoepftem Budget reduzieren;
  Index auf `events(event_type, created_at)` fuer die Budgetabfrage ergaenzen.
- Hangul Filler als moegliche Worttrenner gesondert behandeln; derzeit werden sie entfernt.
- Wird eine Routing-Regel nach Freigabe so verschaerft, dass keine strukturell zulaessige
  Lane bleibt, kann ein wartender Task noch terminal `blocked` werden. Dafuer einen
  voruebergehenden Wartezustand oder eine ausdrueckliche Owner-Entscheidung vorsehen.
- Veraenderte Platzhalter, zwischen Versuchen entfernte Blocklisten-Begriffe und eine
  begrenzte Eskalation wiederholter `task.gate_error`-Events bleiben nachvollziehbar offen.

## Etappe 7 gemergt

PR: [#22](https://github.com/Kaancodm/control-deck/pull/22), vom Owner als `a092942` squash-gemergt.
Branch: `stage/07-router`, nach Squash-Merge von PR #21 auf `main 323d727` rebased.

Umgesetzt: reine Routing-Funktion, lokale Regeln und Modellwahl, Tagesbudgets je Lane,
Dry-run ohne Speicherung und dieselbe Routing-Entscheidung im Referenz-Runner.
`local_only` verwendet ausschliesslich Ollama unter Beachtung der Policy; keine zulaessige
Lane fuehrt zu `blocked`, ausgeschoepfte Tagesbudgets verschieben nur die Ausfuehrung.
Der Claim zaehlt atomar mit DB-Zeit, auch bei Absturz oder fehlendem Ergebnis.

Review-Nacharbeit aus PR #21 in eigenen Commits:

- `4b5a509`: Mehrwort-Blocklisten erkennen beliebigen Leerraum, einschliesslich Tab und
  Zeilenumbruch; Originalpositionen und Rueckabbildung bleiben exakt.
- `fa4bb07`: zusaetzlich zu Cf wird die vollstaendige Unicode-15.0-Menge
  `Default_Ignorable_Code_Point` entfernt, einschliesslich CGJ, Variation Selectors und
  Hangul Filler. Tests sichern Secret-Erkennung und vollstaendige E-Mail-Ersetzung ab.

Pflichtchecks vom 2026-11-03: `make lint` gruen (Ruff, Format, Mypy),
`uv run alembic -x target=test check` ohne Schemaabweichung, `make test` mit
**916 bestandenen Tests** (**255 Integration**, **661 Unit**) und **2 bekannten strict xfail**
zur Worker-Identitaet; eine bestehende Starlette/httpx-Warnung. `make reference`: **4/4**.
Isolierte PostgreSQL-17-Testdatenbank, keine produktive Verbindung.
Die CI des finalen Heads wird im PR ausgewiesen. Details: [ROUTING.md](ROUTING.md).

Der Owner hat die gestapelte Fortsetzung bis Etappe 17 ohne Warten auf Review oder Merge
freigegeben. Jede Etappe behaelt einen eigenen Draft-PR. Review-Korrekturen erfolgen im
betroffenen Basis-PR, danach werden nachfolgende Etappen rebased. Kein automatischer Merge.
Etappe 18 wird ausschliesslich als Abnahme-Checkliste vorbereitet und nicht ausgefuehrt.

Folgepunkte aus PR #21: veraenderte Platzhalter, ein zwischen Versuchen aus der Blockliste
entfernter Begriff und eine begrenzte Eskalation wiederholter `task.gate_error`-Events.
Die Worker-Identitaet und sichere Runner-Anbindung sind in Etappe 8 geschlossen.

## Etappe 6 gemergt

Basis: `main 7bf0e1a`, nach Merge von PR #19. Branch: `feat/etappe-6`.
PR: [#21](https://github.com/Kaancodm/control-deck/pull/21), extern reviewt und vom Owner
als `323d727` nach main squash-gemergt.

Der erste Commit `f4e0f8f` nimmt die beiden Folgepunkte aus Etappe 5 auf:

- NFKC-Schattenkopie mit Entfernung von Cf-Zeichen und Leerraum-Normalisierung. Fundstellen
  beziehen sich weiterhin auf den Originaltext. Blocklisten verwenden dieselbe Normalisierung.
  Begrenzte Verarbeitung weist uebermaessig komplexen Unicode-Text sicher ab.
- Lease-Polls schreiben `task.gate_error` je Task, Fehlercode und Quelle hoechstens einmal in
  60 Sekunden. Datenbankzeit und Task-Lock schuetzen auch gleichzeitige Poller. Owner-Versuche
  bleiben einzeln auditiert.

Danach umgesetzt:

- `app/redaction`: sechs unterstuetzte Arten, taskgebundene stabile Platzhalter, verlustfreier
  Unicode-Roundtrip und vollstaendige Residualpruefung. Fremde oder unbekannte Platzhalter
  werden nicht aufgeloest; Originalwerte erscheinen nicht in normalen Fehlern oder Logs.
- Migration `0008` und `redaction_maps`: atomische Zuordnung unter Task-Lock, unveraenderliche
  Eintraege, eindeutige Originalwerte, Hashpruefung und geschuetzter Downgrade.
- Payload-Vorbereitung aus einmalig gelesenen Dateien und allen Text-Metadaten. Der
  Transport-Payload enthaelt keine Map. Sichere Artefakte sind atomar, unveraenderlich und
  privat (`0700`/`0600`); SHA-256 bezeichnet exakt die geschriebenen Bytes.
- Der Fake-/Referenz-Worker erhaelt nur vorbereiteten Text. Rueckabbildung erfolgt lokal.

Details: [REDAKTION.md](REDAKTION.md), [DATENBANK.md](DATENBANK.md).

## Abnahme

Lokale Pflichtchecks vom 2026-11-03:

- `make lint`: Ruff, Format und Mypy gruen.
- `uv run alembic -x target=test check`: keine Schemaabweichung; isolierte PostgreSQL-17-Test-DB.
- `make test`: **759 bestanden**, davon **224 Integrationstests** und **535 Unit-Tests**.
  Zusaetzlich **2 erwartete Fehlschlaege (strict xfail)**; sie zaehlen nicht als bestanden.
  Eine bestehende Starlette/httpx-Deprecation-Warnung.
- `make reference`: **4/4** bestehende Faelle bestanden.

Die GitHub-CI des Etappe-6-Heads war gruen. Das externe Review und der Owner-Merge sind
abgeschlossen; die beiden P2-Nacharbeiten sind in Etappe 7 separat behoben.

## Historische Grenze nach Etappe 6

Die damals noch konservative Worker-API wurde in Etappe 8 durch vorbereiteten Versandtext
und sichere Versuchsdaten ersetzt. Die beiden Worker-Identitaets-Regressionen bestehen jetzt
als normale Tests. Inhaltliche Referenzfaelle bleiben Owner-Aufgabe; die vier vorhandenen
Faelle bleiben erhalten. Es gibt keine echten Anbieteraufrufe oder Server-Eingriffe.

## Inbetriebnahme

Echte Logins, CLI-/Modellaufrufe, Anbieter-Kontingente und Serverinstallation werden nicht
getestet oder ausgefuehrt. Dafuer erforderliche Pruefungen bleiben in jedem Etappen-PR
explizit als Inbetriebnahme offen. Die lokalen Pflichtchecks verwenden nur Simulationen
und isolierte Testdatenbanken.

## Etappe 14: implementiert und vollstaendig lokal geprueft

Draft-PR [#29](https://github.com/Kaancodm/control-deck/pull/29), Branch `stage/14-knowledge`.
Elternbasis: finaler Etappe-13-Head `aa95b47c4b07f46b41adbdfeb79a046cc2766f15`, CI gruen.
Der Rebase uebernimmt die Korrektur des historischen UI-Migrationstests: dieser prueft
Revision `0012` ausdruecklich, statt spaetere KB-Tabellen faelschlich ihr zuzuordnen.

Umgesetzt: eigener versionierter `deck_kb.v1`-Testvertrag, pgvector mit Klassenfilter vor
Rangfolge, unveraenderliche Dokumente und Kontext-Snapshots, ausdrueckliche Datenklassen,
lokale Fake-Embeddings, Freigabe-Hashes und wiederaufnehmbare explizite Owner-Exporte.
Owner-API und UI bieten Suche, Kontextvorschau, Export aus `done` mit CSRF/Bestaetigung
und sicheren Inbox-Import. Keine KB-Aufrufe unter Task-/Lease-Locks.

Frische Pflichtchecks vom 2026-11-03 auf Code-Stand `6b9d470`: **1991 Tests bestanden**,
keine Skips oder xfail; eine bestehende Starlette/httpx-Deprecation-Warnung.
`make lint`, zentraler Alembic-Check und separate KB-Migration samt Check sind gruen.
`make reference` und Ollama-Fake-CLI-Referenzmodus: jeweils **4/4**. Hook-Pruefungen gruen.
Echte Chromium-Laeufe bei 390 Pixeln mit und 1024 Pixeln ohne JavaScript sind enthalten.
Zentrale und KB-Testdatenbank sind getrennte isolierte PostgreSQL-17-/pgvector-Datenbanken.
Der folgende Commit aendert ausschliesslich Dokumentation; finale Head-SHA und dessen CI
werden im PR ausgewiesen. Externes Review bleibt offen, der PR bleibt Draft. Kein Merge.

Die Schlusspruefung identifizierte eine gemeinsame Fehlerursache frueherer sporadischer
Codex-`idle`-Fehler: Die generierte Task-UUID im Prompt konnte wie eine Telefonnummer
aussehen. Die Datengrenze blockierte dann einen sauberen Cloud-Auftrag oder erzeugte eine
unnoetige Redaktionsabbildung. Interne IDs bleiben nun in den strukturierten Daten und
Archivpfaden; die generierte Prompt-Ueberschrift enthaelt sie nicht mehr. Der vollstaendige
Scanner bleibt unveraendert. Sechs feste Regressionen scheiterten vor der Korrektur;
anschliessend bestanden alle 62 betroffenen Tests und der obige Gesamtlauf. Derselbe
Zahlenblock in Nutzertext wird weiterhin blockiert beziehungsweise redigiert.

### Inbetriebnahme

Produktive Schema-Kompatibilitaet, Schreibrechte, echtes lokales Embedding-Modell und
Dimension sowie semantische Suchqualitaet bleiben offen. Das Live-Profil ist gesperrt.
Keine echten Logins, API-Keys, Anbieteraufrufe, Serveraenderungen oder neuen inhaltlichen
Referenzfaelle. Details: [WISSENS_DB.md](WISSENS_DB.md).

## Etappe 15: lokal abgeschlossen

Draft-PR: [#30](https://github.com/Kaancodm/control-deck/pull/30), Branch
`stage/15-hardening`, auf den finalen Etappe-14-Head `f43b70f` rebased.

Die vollstaendige Owner-UI ist mit der aeussersten HTTP-Schutzmiddleware integriert.
Validierte Host-/Origin-Grenzen, begrenzte Requests, sichere Konfigurations- und
Bindungspruefungen sowie redigierte Fehler gelten vor den Handlern. Die UI verwendet
bei konfigurierter Public-Origin denselben validierten Wert; Forwarding-Header verleihen
keine Rechte.

Migration `0014` und ihre UI-Anbindung liefern Passwortbindung, 30 Minuten
Inaktivitaetsablauf, eine unverlaengerbare absolute 12-Stunden-Grenze, Drosselung je
Client-Kennung (Peer-Adresse der Verbindung, nur als Digest gespeichert), globale Login-Sperre
nach 20 Fehlern in 24 Stunden und sichere Auth-Ereignisse. Die Sperre erscheint mit HTTP 423
ohne Passwortformular und endet gemaess D3 mit einem Neustart des Dienstes oder der
ausdruecklichen lokalen Entsperrung. «Ueberall abmelden» widerruft alle bestehenden Sessions
mit Owner-/CSRF-Pruefung.
Die Sperrreihenfolge bleibt Login-State vor Session, auch in der HTTP-Anbindung.

Frischer Gesamtlauf auf dem Code- und Teststand von `397d723`: **2088 bestanden**, keine
Skips oder xfail; eine bestehende Starlette/httpx-Deprecation-Warnung. `make lint`, zentraler
Alembic-Check, separate KB-Migration samt Check und Hook-Pruefungen sind gruen.
`make reference` und Ollama-Fake-CLI-Referenzmodus: jeweils **4/4**. Enthalten sind
Chromium-Laeufe bei 390 Pixeln mit und 1024 Pixeln ohne JavaScript, mit globaler Abmeldung
zweier Browser-Sessions, Sperranzeige nach Neuladen und erneuter Anmeldung nach Entsperrung.
Zentrale und KB-Testdatenbank sind getrennte isolierte PostgreSQL-17-/pgvector-Datenbanken.

Ein vorheriger Gesamtlauf hatte **2080 bestandene Tests und einen Fehlschlag**: ein geerbter
Codex-Poll-Test erhielt beim ersten Queue-Poll unerwartet `idle`. Die Ursache ist inzwischen
deterministisch reproduziert: Die generierte UUID in der Prompt-Ueberschrift konnte zufaellig
wie eine Telefonnummer aussehen. Die erneute Datenschutzpruefung blockierte deshalb einen
bereits freigegebenen synthetischen Auftrag vor jedem Prozessstart. Die gemeinsame Korrektur
aus Etappe 14 entfernt die interne UUID aus der Prompt-Ueberschrift; strukturierte IDs,
Artefaktpfade und die vollstaendige Inhaltspruefung bleiben erhalten. Regressionen pruefen
auch, dass Telefonnummern im eigentlichen Inhalt weiterhin blockiert oder redigiert werden.
Sichere Zustandsdiagnostik bleibt erhalten; keine Assertion wurde abgeschwaecht und kein
Retry hinzugefuegt. Der neue Gesamtlauf nach dieser Korrektur ist vollstaendig gruen.
Der anschliessende Rebase uebernimmt ausschliesslich die finalen Etappe-14-Nachweise;
Code und Tests sind zum geprueften Stand unveraendert. Die CI des exakten finalen Heads
wird im PR separat ausgewiesen.
Externes Review bleibt offen, der PR bleibt Draft. Kein Merge.

### Inbetriebnahme

Offen bleiben reale Tailnet-/TLS-Bindung, externe Erreichbarkeit, private Konfiguration,
Betriebsrechte und echte Login-Einrichtung. Keine Netzwerk-Scans, echten Logins,
Anbieter-/Modellaufrufe, API-Keys, produktiven DB-Verbindungen oder Serveraenderungen.
Details: [SECURITY.md](SECURITY.md), [UI.md](UI.md). Keine neuen inhaltlichen Referenzfaelle.

## Etappe 16: implementiert und vollstaendig lokal geprueft

Draft-PR [#31](https://github.com/Kaancodm/control-deck/pull/31), Branch `stage/16-operations`,
gestapelt auf Etappe 15 (`066ec26`). Kein Merge.

Migration `0015`, append-only Betriebsereignisse, 14-/90-Tage-Aufbewahrung und
wiederaufnehmbare Retention sind integriert. systemd-Benutzervorlagen und begrenzter
Simulations-Supervisor bleiben Repo-Code; keine Installation oder Dienststarts auf einem
Betriebsserver. Echte Prozessgruppenbeendigung samt Unterprozess und anschliessendem
Lease-Retry sind belegt; Dienstende erzeugt keinen Owner-Abbruch. Ein Downgrade verweigert
das Loeschen vorhandener Betriebsnachweise.

Ein atomar publizierter Backup-Satz in Manifestversion 2 verlangt beide Datenbanken:
zentral `public` und separat `public`/`deck_kb` samt pgvector. Pro DB teilen Dump,
Revision und schemaqualifizierte Tabellenzaehler einen exportierten Snapshot. Fehlende
Wissenskonfiguration oder ein fehlgeschlagener zweiter Dump publizieren kein zentrales
Ersatzbackup. CLI, Update, Restic und Pruning verlangen vollstaendige Saetze; alte zentrale
Manifestversion-1-Saetze werden weder als vollstaendig akzeptiert noch automatisch geloescht.

Restore verlangt zwei leere `_test`-Ziele, getrennt voneinander und von beiden Quellen.
Beide Archive, Pruefsummen, Revisionen und Ziele werden vor dem ersten Eingriff validiert.
Nur leeres `public` wird ohne `CASCADE` vorbereitet. Normale Schemas wie `pgdata` werden
nicht als PostgreSQL-Systemschemas uebergangen. Beide Schema-/Zeilennachweise, der
Wissensvertrag, die pgvector-Version und beide Alembic-Checks muessen vor einem Erfolgsereignis
passen. Teilfehler bleiben `restore_partial_failure`; beide Diagnoseziele gelten als nicht
freigegeben und werden nicht automatisch bereinigt. Es gibt keine verteilte Transaktion.

Frischer Gesamtlauf auf dem finalen Code- und Teststand `5f4243b`: **2210 Tests bestanden**,
keine Skips oder xfail; eine bestehende Starlette/httpx-Deprecation-Warnung. `make lint`,
zentrales Upgrade und Alembic-Check, separates KB-Upgrade und KB-Check sowie Hooks sind
gruen. Beide Referenzmodi bestehen mit **4/4**. Der finale Folgecommit aktualisiert nur
Dokumentation; Anwendung und Tests bleiben identisch. Finale Head-SHA und CI stehen im PR.

Die gezielte Erweiterungspruefung besteht mit **92 Tests**. Vier zuvor rote Regressionen
sichern normale Schemas in beiden Quellen und beiden Zielen. Der echte PostgreSQL-17-/
pgvector-Roundtrip weist Dokumentinhalt, Vektor-Suche und je DB konsistente Zaehler trotz
paralleler Aenderungen nach. Fehlende, beschaedigte oder widerspruechliche Wissensarchive,
befuellte Ziele, ungueltiger Wissensvertrag, Restic-Teilsaetze und Restore-Teilfehlerversuche
bleiben ohne Erfolgsnachweis. Der Gesamtlauf enthaelt ausserdem Chromium bei 390/1024 Pixeln
und Offline-`systemd-analyze verify`. Externes Review bleibt offen; der PR bleibt Draft.

### Inbetriebnahme

Offen bleiben reale Bindung/TLS, User-Manager und Lingering, Datei-/Backup-Ziele, Speicher,
Off-site-Kopie und Restore, Restic-Konfiguration sowie echte Lane-Isolation und Anmeldung.
Beide Datenbanken sind im Backup-Satz enthalten; ein konsistenter Gesamtzeitpunkt ueber
beide DBs und Laufdateien bleibt separat nachzuweisen. Die Runtime-Wissensvariable gilt
nur fuer Wartung und aktiviert keinen Live-Wissensadapter.
Der woechentliche Restore-Timer ist eine Trockenlauf-Erinnerung ohne `--apply`, kein
Restore-Nachweis; automatische Bereitstellung leerer Ziele bleibt Inbetriebnahme.
Keine echten Logins, Anbieteraufrufe, API-Keys, produktiven Verbindungen oder Serveraenderungen.
Details: [OPERATIONS.md](OPERATIONS.md). Etappe 18 bleibt eine vorbereitete Checkliste.
