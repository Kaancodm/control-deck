# Stand des Repositorys

## Etappe 6 bereit fuer externes Review

Basis: `main 7bf0e1a`, nach Merge von PR #19. Branch: `feat/etappe-6`.
Draft-PR: [#21](https://github.com/Kaancodm/control-deck/pull/21).

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

Die GitHub-CI des aktuellen Heads wird im PR ausgewiesen. Externes Review und Owner-Merge
stehen aus; kein Merge durch den Agenten.

## Grenze zum Host-Runner

Die lokalen Redaction-Bausteine sind implementiert. Die bestehende HTTP-Worker-API liefert
weiterhin `task.v1`; deshalb bleibt ihre bisherige Schranke fuer Cloud-Auftraege mit Treffern
aktiv. Etappe 8 muss Vorbereitung und Rueckabbildung mit Claim, Lane, Lease, Versuch und
Ergebnisannahme verbinden, Freigabe-Hashes erzwingen und ausschliesslich archivierte Bytes
versenden. Dieser PR aktiviert keinen ungeschuetzten Cloud-Versand.

Die zwei strikten `xfail`-Tests zur Worker-Identitaet bleiben ein Pflichtpunkt fuer Etappe 8.
Inhaltliche Referenzfaelle schreibt der Owner; die vier vorhandenen YAML-Faelle bleiben erhalten.
Es gibt keine Server-Eingriffe, produktiven DB-Verbindungen oder echten Anbieteraufrufe.
