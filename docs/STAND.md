# Stand des Repositorys

## Etappe 8 in Arbeit, gestapelt auf Etappe 7

Branch: `stage/08-runner`, Basis: `stage/07-router` (`924d3d4`, Draft-PR #22).
Der Owner hat die gestapelte Umsetzung von Etappe 7 bis 17 ohne Warten auf Review oder
Merge freigegeben. Etappe 18 wird ausschliesslich als Abnahme-Checkliste vorbereitet.

Geplant: lanegebundene Claims, expliziter Start, sichere Payload-Uebergabe, Heartbeat und
Steuerentscheide, Pause/Not-Aus, Parallelitaetsgrenze und Host-Runner mit Fake-Executor.
Inhaltliche Referenzfaelle bleiben Owner-Aufgabe. Kein Merge durch den Agenten.

### Inbetriebnahme

Echte Logins, Anbieteraufrufe, Modelllaeufe, Serveraenderungen und Deployment sind nicht
Teil dieser Umsetzung. Alle neuen Laufzeittests verwenden Simulationen oder Fake-CLIs.

## Etappe 7 bereit fuer externes Review

Draft-PR: [#22](https://github.com/Kaancodm/control-deck/pull/22).
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
Die Worker-Identitaet und sichere Runner-Anbindung werden in Etappe 8 geschlossen.

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

## Grenze zum Host-Runner

Die lokalen Redaction-Bausteine sind implementiert. Die bestehende HTTP-Worker-API liefert
weiterhin `task.v1`; deshalb bleibt ihre bisherige Schranke fuer Cloud-Auftraege mit Treffern
aktiv. Etappe 8 muss Vorbereitung und Rueckabbildung mit Claim, Lane, Lease, Versuch und
Ergebnisannahme verbinden, Freigabe-Hashes erzwingen und ausschliesslich archivierte Bytes
versenden. Dieser PR aktiviert keinen ungeschuetzten Cloud-Versand.

Die zwei strikten `xfail`-Tests zur Worker-Identitaet bleiben ein Pflichtpunkt fuer Etappe 8.
Inhaltliche Referenzfaelle schreibt der Owner; die vier vorhandenen YAML-Faelle bleiben erhalten.
Es gibt keine Server-Eingriffe, produktiven DB-Verbindungen oder echten Anbieteraufrufe.

## Inbetriebnahme

Echte Logins, CLI-/Modellaufrufe, Anbieter-Kontingente und Serverinstallation werden nicht
getestet oder ausgefuehrt. Dafuer erforderliche Pruefungen bleiben in jedem Etappen-PR
explizit als Inbetriebnahme offen. Die lokalen Pflichtchecks verwenden nur Simulationen
und isolierte Testdatenbanken.
