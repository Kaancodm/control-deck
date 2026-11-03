# Stand des Repositorys

## Etappe 9 in Arbeit

Branch: `stage/09-ollama`, gestapelt auf `stage/08-runner` / PR #23 (`5fd9704`).
Der Owner hat die Ollama-CLI mit Fake-CLI-Tests anstelle des HTTP-Adapters gewaehlt.
Gebaut werden ein begrenzter Prozessadapter, Lane-Gesundheit und simulierte E2E-Laeufe.
Das Live-Profil bleibt gesperrt: Die offizielle CLI bietet keinen sicheren No-Pull-Schalter
und kann bei multimodalen Modellen Dateien aus dem Prompt nachladen.
Die konkreten Voraussetzungen bleiben unter **Inbetriebnahme** offen.


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
