# Etappe 14 - Wissens-DB-Anbindung

Status: implementiert als gestapelter Draft-PR [#29](https://github.com/Kaancodm/control-deck/pull/29)
auf Etappe 13. Gesamtvalidierung und finaler CI-Nachweis werden in [STAND.md](../STAND.md)
und im PR dokumentiert. Kein Merge durch den Agenten.

Der Wiederaufnahme-Checkpoint und die ausdrueckliche Freigabe fuer Simulation ersetzen
hier die frueheren Entwurfsannahmen zu Live-Embeddings, impliziten Datenklassen und
Warten auf einen Eltern-Merge. Der produktive KB-Vertrag bleibt Inbetriebnahme.

## Umgesetzter Vertrag

- Eigene Alembic-Kette fuer `deck_kb.v1` in einer separaten ausdruecklichen `_test`-DB
  mit pgvector. Dokumente und Chunks sind unveraenderlich; Datenklassen haben keinen Default.
- Lokale deterministische Fake-Embeddings (`deck-fake-embedding-v1`, Dimension 8).
  Aktivierung nur mit `DECK_KB_PROFILE=simulation` und `DECK_KB_TEST_DATABASE_URL`;
  ein beliebiger URL aktiviert keinen Adapter. Live bleibt gesperrt.
- Klassenfilter vor der SQL-Rangfolge: `local_only` darf alle Klassen laden,
  `cloud_redacted` nur `cloud_redacted` und `cloud_ok`, `cloud_ok` nur `cloud_ok`.
- Explizite kanonische Dokument-UUIDs, hoechstens 16 Referenzen und 96 KB Kontext;
  fehlende, unzulaessige oder zu grosse Referenzen werden abgewiesen.
- Migration `0013`: lokale unveraenderliche Kontext-Snapshots und persistierte
  Exportentscheide. Freigabe und jeder Versuch pruefen den vollstaendigen Digest-Satz.
  Scanner, Redaction und Versandarchiv verwenden dieselben UTF-8-Bytes.
- Suche, Kontextvorschau und expliziter Export ueber Owner-API und UI. Export ausschliesslich
  aus `done`, mit ausdruecklicher gleichbleibender oder strengerer Datenklasse. Ein stabiler
  Dokumentschluessel und einmaliges Audit erlauben Wiederaufnahme nach Teilfehlern.
- Sicherer Inbox-Import mit Pflichtklasse und Simulation; Chat erhaelt keine Exportrechte.
  Keine KB-/Embedding-Aufrufe unter Task-/Lease-Locks.

## Nachweise

Pflichtchecks: `make lint`, `uv run alembic -x target=test check`, `make test`,
`make reference`, beide KB-Migrationsbefehle und der Ollama-Fake-CLI-Referenzmodus.
Die Gesamtsuite umfasst Klassenmatrix, echte pgvector-Abfragen, Unicode-/Hash-Fencing,
Geheimnissperre, Migration und geschuetzte Downgrades, parallele Exporte, Teilfehler sowie
Browserlaeufe bei 390 Pixeln mit und 1024 Pixeln ohne JavaScript. Fehlende Integrationstests
zaehlen nicht als bestanden; keine neuen inhaltlichen Referenzfaelle.

## Inbetriebnahme

Zielschema, Schreibrechte, reales lokales Embedding-Modell, Dimension und semantische
Suchqualitaet sind separat nachzuweisen. Der Testvertrag behauptet keine Kompatibilitaet
mit fremden Tabellen. Keine echten Logins, API-Keys, Anbieteraufrufe oder Serveraenderungen.
Externes Review erfolgt durch einen anderen Anbieter; keine Freigabe fuer Release oder Betrieb.

Details: [WISSENS_DB.md](../WISSENS_DB.md).
