# Stand des Repositorys

## Etappe 6 in Arbeit

Basis: `main 7bf0e1a`, nach Merge von PR #19. Branch: `feat/etappe-6`.

Der erste Commit nimmt die beiden Folgepunkte aus Etappe 5 auf:

- NFKC-Schattenkopie mit Entfernung von Cf-Zeichen und Leerraum-Normalisierung. Fundstellen
  beziehen sich weiterhin auf den Originaltext. Blocklisten verwenden dieselbe Normalisierung.
  Begrenzte Verarbeitung weist uebermaessig komplexen Unicode-Text sicher ab.
- Lease-Polls schreiben `task.gate_error` je Task, Fehlercode und Quelle hoechstens einmal in
  60 Sekunden. Datenbankzeit und Task-Lock schuetzen auch gleichzeitige Poller. Owner-Versuche
  bleiben einzeln auditiert.

Danach folgen `app/redaction`, taskgebundene `redaction_maps`, stabile Platzhalter,
verlustfreie Rueckabbildung und sichere Versand-Artefakte. Die Implementierung und die
Gesamtpruefung dieser Teile sind noch nicht abgeschlossen.

## Abnahme

Vor Reviewbereitschaft: `make lint`, `uv run alembic -x target=test check`, `make test` und
`make reference` gegen eine isolierte Testdatenbank. Die finalen Testzahlen stehen dann hier
und im Draft-PR. Kein Merge durch den Agenten.

Die zwei strikten `xfail`-Tests zur Worker-Identitaet bleiben ein Pflichtpunkt fuer Etappe 8.
Inhaltliche Referenzfaelle schreibt der Owner; die vier vorhandenen YAML-Faelle bleiben erhalten.
Es gibt keine Server-Eingriffe, produktiven DB-Verbindungen oder echten Anbieteraufrufe.
