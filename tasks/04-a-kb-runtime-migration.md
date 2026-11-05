# Bauauftrag 04-a-kb-runtime-migration

## Etappe

Ticket 4 / A: Runtime-Ziel fuer die KB-Migration, einschliesslich Blocker 1(c).
Builder: Codex. Externes Review: Fable, anderer Anbieter als der Builder; noch ausstehend.
Status: nur geplant. Umsetzungsbranch: `stage/04-a-kb-runtime-migration`.
Die Nummer ist eine Ticketnummer; sie ersetzt nicht die historische Etappe 4.

## Ziel

Die bestehende KB-Alembic-Kette kann nach ausdruecklicher Zielwahl den Vertrag `deck_kb.v1`
auch in einer vorgesehenen Runtime-Wissens-DB anlegen und pruefen. Der Testpfad bleibt auf
isolierte `_test`-Datenbanken begrenzt. Kein impliziter Runtime-Fallback und kein Aktivieren
des Live-Wissensadapters. Heute verwendet `alembic_kb/env.py` immer den Test-URL-Getter.

## Rahmen

- Feste Reihenfolge: Plan-PR, dann A -> B -> C -> D; keine Tagesplanung oder Parallelstarts.
- Ein Ticket ist repo-seitig gruen, wenn seine Abnahme, CI und Fables Review gruen sind und
  offene Befunde erledigt sind. Erst dann beginnt das naechste Ticket. Merge nur nach Owner-GO.
- Folge-PRs basieren auf dem geprueften Vorgaenger-Head oder dessen gemergtem Stand;
  keinen Merge vorwegnehmen. `TICKET_BASE` bezeichnet jeweils den dokumentierten PR-Basis-Commit.
- Codex baut Repo-Code; danach fuehrt der Owner Server-Schritte 1 bis 15 einzeln aus,
  jeweils erst nach bestandenem Vorgaenger. Dieser Auftrag fuehrt keine Server-Schritte aus.
- Entscheid Blocker 1: (b) fuer den iPhone-Test, (c) als dieses Ticket. Bei (b) kein Deck-Backup
  und kein `make update`; vorhandene B2-Sicherung muss die zentrale DB nachweislich abdecken.
- Auch ein gemergtes A ersetzt nicht die Anwendung/Pruefung am Betriebsort: (b) bleibt bis
  zum ausdruecklichen Wechsel nach nachgewiesenem Runtime-KB-Vertrag wirksam.
- Entscheid Blocker 3: Variante A, HTTPS ueber Tailscale, Loopback-Bindung, feste Public-Origin.
- Blocker 2 bleibt offen bis Timername UND Manager des bestehenden B2-Timers bekannt sind;
  keine Unit umbenennen, ueberschreiben oder unter einem vermuteten Namen deaktivieren.
- Keine Hostwerte, Secrets, echten Logins, Serveraenderungen oder neuen Abhaengigkeiten.
- `task.v1` verlangt explizites `data_class` (ADR 0005); `local_only` bleibt lokal.
- Tests nur mit isolierten `_test`-DBs; Code/Commits Englisch, Dokumentation de-CH ohne Eszett.

## Schritte (atomar, je ein Commit)

1. `app/kb/config.py`, `tests/unit/test_kb_migration_target.py` (neu): Zielauswahl/URL-Validierung
   kapseln. `target=test` waehlt nur `DECK_KB_TEST_DATABASE_URL`; `target=runtime` waehlt nur
   `DECK_KB_DATABASE_URL`. Runtime nur mit explizitem `-x target=runtime`; unbekannte oder
   doppelte Ziele, fehlende/ungueltige URLs sowie die zentrale DB als KB-Ziel abweisen.
   Ohne Ziel bleibt der bestehende reine Testaufruf kompatibel; jede Runtime-Belegung ohne
   ausdrueckliche Zielwahl wird vor Verbindungsaufbau abgewiesen. Fehler geben keine URL aus.
2. `alembic_kb/env.py`, `tests/unit/test_kb_migration_target.py` (neu): Auswahl fuer Online-
   und Offline-Modus einbinden. `current`, `check` und `upgrade` unterstuetzen das Runtime-Ziel;
   Runtime-Downgrades bleiben gesperrt. Bestehende Versionsdateien/Vertraege nicht umschreiben.
3. `tests/integration/test_kb_migration_target.py` (neu): Upgrade und `check` beider Auswahlpfade
   gegen getrennte Wegwerf-DBs mit `_test` pruefen. Auch beim Test des Runtime-Selektors zeigt
   dessen URL nur auf eine vom Test erzeugte `_test`-DB; echte Runtime-Verbindungen sind gesperrt.
   Revision, Vertragszeile, Tabellen, pgvector und Wiederholung pruefen; kein Zugriff auf die
   zentrale DB. Negative Auswahltests muessen ohne jeden Verbindungsaufbau abbrechen.
4. `docs/WISSENS_DB.md`: ausdrueckliche Zielwahl und private Konfiguration dokumentieren;
   Anwendung auf dem Server, Rollenrechte und Backup-Nachweis bleiben Inbetriebnahme.

## Abnahme (ausfuehrbare Befehle)

| Befehl | Erwartet |
|---|---|
| `uv run pytest tests/unit/test_kb_migration_target.py -q` | Zielmatrix, Mehrdeutigkeit, Runtime-Downgrade und sichere Fehler gruen; kein Netz/DB-Zugriff |
| `uv run pytest tests/integration/test_kb_migration_target.py tests/integration/test_knowledge_store.py tests/integration/test_operations.py -q` | Beide Selektoren und Backup/Restore-Vertrag gruen, ausschliesslich isolierte `_test`-DBs; keine Skips |
| `uv run alembic -c alembic_kb.ini -x target=test upgrade head && uv run alembic -c alembic_kb.ini -x target=test check` | exit 0; keine Schemaabweichung |
| `uv run alembic -x target=test check` | exit 0; zentrale Kette unveraendert |
| `make lint && make test && make reference` | alle gruen; Testzahl/Skips ausweisen, keine neuen Skips; Referenzfaelle bestehen |
| `git diff --check` | exit 0 |

## Abbruchkriterium

Abbruch bei implizitem Runtime-Ziel, Zielverwechslung, ausgegebener URL, Schemaabweichung,
fehlender Test-DB oder rotem Check/Review. Kein Start von B; Fehler beheben und erneut pruefen.
Kein automatischer Downgrade oder Anlegen/Loeschen produktiver Datenbanken.

## Bewusst offen

Anwendung am Betriebsort durch den Owner; bestehende Fremd-Schemas werden nicht automatisch
adaptiert. Live-Adapter, Isolationsnachweise, Port-Scan und echter Restore bleiben separate
Nachweise (T4–T7 aus der Lueckenliste), keine zusaetzlichen Tickets dieses A–D-Auftrags.

## Offene Fragen (TODO)

TODO Blocker 2: B2-Timername und Manager privat ermitteln; der Wert gehoert nicht ins Repo.
TODO Inbetriebnahme: Runtime-KB-Ziel, Migrationsrolle und Backup-Leserechte privat bestaetigen.
