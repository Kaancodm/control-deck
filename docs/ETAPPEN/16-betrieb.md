# Etappe 16 - Betriebsvorlagen, Backup, Retention und Restore

Gestapelter Draft-PR [#31](https://github.com/Kaancodm/control-deck/pull/31) auf Etappe 15.
Der Wiederaufnahme-Checkpoint ersetzt die frueheren Annahmen zu automatischen
Dienststarts und Restore-Timern. Der Auftrag umfasst Repo-Code und Simulation;
Deployment und Gesamt-Abnahme sind nicht freigegeben.

## Umgesetzter Vertrag

- Migration `0015` nach `0014`: append-only `operations_events` mit sicheren Metadaten.
- Private, atomar publizierte PostgreSQL-Custom-Dumps des zentralen `public`-Schemas.
  Dump, Manifest und Tabellenzaehlungen stammen aus demselben exportierten Snapshot.
- Restore nur in eine ausdrueckliche leere `_test`-DB, getrennt von der Auditquelle.
  Nach verifiziertem Archiv wird die Leerheit erneut geprueft und das leere `public`
  ohne `CASCADE` fuer den Dump vorbereitet. Pruefsumme, Zaehler, Revision und Alembic
  muessen passen, bevor Erfolg auditiert wird.
- Vollstaendige Backups bleiben 14 Tage; ohne neueren gueltigen Satz bleibt der letzte
  alte Dump bestehen. Retention ist standardmaessig ein Trockenlauf.
- Laufartefakte, Maps und Kontext-Snapshots duerfen nach 90 Tagen nur fuer terminale
  Tasks ohne Lease entfernt werden. Tasks, Results, Audit, KB-Dokumente und Exportbelege
  bleiben erhalten. Teilfehler sind sichtbar und wiederaufnehmbar.
- systemd-Benutzervorlagen und feste Install-/Start-/Stop-/Status-/Update-Befehle.
  Standardmaessig nur Planung; kein Aufruf dieser Dienste durch die Implementierung.
  Update verlangt sauberes `main`, Backup, Stopp und Fast-forward; Neustart nur nach
  erfolgreicher Migration. Der Restore-Timer ist eine Trockenlauf-Erinnerung.
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
Off-site-Restore sowie echte Lane-Isolation bleiben offen. Der zentrale Dump sichert
nicht die separate KB-Datenbank und ist kein atomarer Gesamt-Snapshot mit Laufdateien.
Keine echten Logins, Anbieteraufrufe, API-Keys, produktiven DB-Verbindungen, Serveraenderungen,
Tags, Releases oder Merges. Etappe 18 bleibt eine vorbereitete Checkliste.

Details: [OPERATIONS.md](../OPERATIONS.md).
