# Etappe 16 - Betriebsvorlagen, Backup, Retention und Restore

Gestapelter Draft-PR [#31](https://github.com/Kaancodm/control-deck/pull/31) auf Etappe 15.
Der Wiederaufnahme-Checkpoint ersetzt die frueheren Annahmen zu automatischen
Dienststarts und Restore-Timern. Der Auftrag umfasst Repo-Code und Simulation;
Deployment und Gesamt-Abnahme sind nicht freigegeben.

## Umgesetzter Vertrag

- Migration `0015` nach `0014`: append-only `operations_events` mit sicheren Metadaten.
- Private, atomar publizierte PostgreSQL-Custom-Dumps fuer zentrale DB und separate
  Wissens-DB einschliesslich `deck_kb` und pgvector. Manifestversion 2 verlangt beide
  Archive; je DB sind Dump, Revision und Tabellenzaehlungen snapshot-konsistent.
- Restore ausschliesslich in zwei ausdrueckliche leere `_test`-DBs, getrennt voneinander
  und von beiden Quellen. Beide Archive, Pruefsummen, Revisionen und Ziele werden vor
  dem ersten Eingriff validiert. Beide Schema-/Zeilenbelege und Alembic-Checks muessen
  passen, bevor Erfolg auditiert wird. Teilfehler bleiben fehlgeschlagen; keine verteilte
  Transaktion und kein automatisches Loeschen bereits restaurierter Diagnoseziele.
- Vollstaendige Backups bleiben 14 Tage; ohne neueren gueltigen Satz bleibt der letzte
  alte Dump bestehen. Retention ist standardmaessig ein Trockenlauf.
- Laufartefakte, Maps und Kontext-Snapshots duerfen nach 90 Tagen nur fuer terminale
  Tasks ohne Lease entfernt werden. Tasks, Results, Audit, KB-Dokumente und Exportbelege
  bleiben erhalten. Teilfehler sind sichtbar und wiederaufnehmbar.
- systemd-Benutzervorlagen und feste Install-/Start-/Stop-/Status-/Update-Befehle.
  Standardmaessig nur Planung; kein Aufruf dieser Dienste durch die Implementierung.
  Update verlangt sauberes `main`, beide Backups, Stopp und Fast-forward; Neustart nur nach
  erfolgreicher Migration. Der woechentliche Restore-Timer ist nur eine Trockenlauf-Erinnerung ohne `--apply`.
- Begrenzter Simulations-Supervisor: Dienstende beendet die Prozessgruppe, setzt keinen
  Owner-Abbruch und laesst die Lease-Recovery uebernehmen. Live bleibt Exit-Code 78.
- Optionaler expliziter Restic-Hook, nur mit Fake-CLI getestet.

## Nachweise

Pflichtchecks und Testzahl stehen in [STAND.md](../STAND.md) und im PR. Geprueft werden
Snapshot-Konsistenz trotz paralleler Aenderung, echter Dump-/Restore-Roundtrip in
Wegwerf-Testdatenbanken, unveraenderte nichtleere Ziele, korrupte Archive, Retention-Grenzen,
Teilfehlerversuche, append-only Audit und echter Dienstprozess-Abbruch mit Lease-Retry.
PostgreSQL-17-Clients passen zum Testserver; die CI verwendet Client-Wrapper im isolierten
Service-Container. Gerenderte systemd-Units werden ausschliesslich offline geprueft.

## Inbetriebnahme

Reale Bindung/TLS, Lingering, Dateirechte am Ziel, Speicher-/Backup-Ziel, Restic und
Off-site-Restore sowie echte Lane-Isolation bleiben offen. Der vollstaendige Backup-Satz
sichert beide Datenbanken mit getrennten Snapshots; ein konsistenter Gesamtzeitpunkt
ueber beide DBs und Laufdateien bleibt separat nachzuweisen.
Keine echten Logins, Anbieteraufrufe, API-Keys, produktiven DB-Verbindungen, Serveraenderungen,
Tags, Releases oder Merges. Etappe 18 bleibt eine vorbereitete Checkliste.

Details: [OPERATIONS.md](../OPERATIONS.md).
