# Etappe 16 - Betrieb: systemd-Units, Backup, Aufbewahrung, Restore-Probe

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 15 gemergt ist
Branch-Vorschlag: `stage/16-betrieb`
Abhaengig von: Etappen 8 (Runner-Prozess), 9, 12, 13 (Lanes), 15 (Bindung); E11; Plan-Frage 3 (DB-Instanz, Backup-Pfad)
Entscheidungen: E11
Reviewer: Claude (gerade Etappe)
Abnahme laut Plan v1.1: Restore in eine leere Datenbank gelingt.

## 1 Ziel

Der Kern laeuft als systemd-Benutzerdienste auf dem Host (ADR 0002): `deck-api` und je Lane ein
`deck-runner@<lane>`. Taegliche DB-Dumps, Aufbewahrung fuer `runs/` und `redaction_maps`, eine
automatische Restore-Probe und ein Betriebshandbuch. Keine Host-Werte im Repo.

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| `deploy/` enthaelt nur `.gitkeep`; `Makefile` kennt `lint`, `test`, `reference`, `validate` | belegt |
| Plan Abschnitt 9: Dump um 03:15 (14 Tage), `runs/` 90 Tage, restic um 03:30, Restore-Probe als Abnahme | Plan v1.1 |
| `.gitignore` enthaelt `backups/`, `runs/` | belegt |
| Produktive Instanz (Port 5433, DB `controldeck`) wird ausserhalb des Repos verwaltet | `docs/DATENBANK.md` |

## 3 Scope

**In:** Unit-Vorlagen, `make install start stop status update backup retention restore-probe
test-server`, Skripte fuer Dump, Aufbewahrung, Restore-Probe, restic-Hook (nur Aufruf),
`docs/OPERATIONS.md`.

**Out:** restic-Konfiguration und Zugangsdaten (ausserhalb des Repos), Monitoring-Server,
Mehr-Host-Betrieb, Container fuer den Kern (bewusst nicht, ADR 0002).

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | Units unter `deploy/systemd/`: `deck-api.service`, `deck-runner@.service` (Instanz = Lane), `deck-backup.service` + `.timer` (03:15), `deck-retention.service` + `.timer` (03:45), `deck-restore-probe.service` + `.timer` (woechentlich). Alle als `systemd --user` des Dienstnutzers, `Restart=on-failure`, `EnvironmentFile=%h/.config/deck/env` (Rechte 600, nicht im Repo), `ProtectSystem=strict`, `ReadWritePaths` nur fuer `runs/`, Backups, Logs. |
| D2 | `make install` kopiert Units nach `~/.config/systemd/user/`, `daemon-reload`, aktiviert Timer; `make update` = `git fetch` und `git merge --ff-only origin/main`, `uv sync --frozen`, `alembic upgrade head`, Neustart der Dienste; Abbruch, wenn der Arbeitsbaum nicht sauber ist. |
| D3 | `scripts/backup.sh`: `pg_dump` im Custom-Format nach `$DECK_BACKUP_DIR/controldeck-<datum>.dump`, Pruefsumme daneben, Loeschung aelter als 14 Tage; danach `restic backup` fuer Dumps und `runs/`, nur wenn `RESTIC_REPOSITORY` gesetzt ist (Zugangsdaten ausserhalb des Repos). |
| D4 | `scripts/retention.py` (Trockenlauf Default, `--apply` fuer Loeschung): `runs/<task_id>/` aelter als 90 Tage (E11) loeschen, zugehoerige `redaction_maps` loeschen; `tasks`, `results`, `events` bleiben (ADR 0003). Jeder Lauf schreibt ein Event `retention.run` mit Zaehlern. |
| D5 | `scripts/restore_probe.sh`: juengsten Dump in eine temporaere leere Datenbank (`controldeck_probe_<datum>`) einspielen, `alembic check` gegen sie ausfuehren, Zeilen in `tasks` und `events` zaehlen und mit dem Dump-Protokoll vergleichen, Datenbank loeschen; Ergebnis PASS oder FAIL als Event und Exit-Code; Timer meldet FAIL im Journal. |
| D6 | `make test-server` (aus Etappe 9) wird erweitert um `alembic check`, Restore-Probe und die Serverlaeufe der Lanes mit gesetzten `DECK_E2E_*`-Schaltern. |
| D7 | `docs/OPERATIONS.md`: Start, Stop, Status, Update, Not-Aus, Lease-Zeiten, Login-Erneuerung der CLIs (Etappen 12, 13), Backup und Restore, Aufbewahrung, Fehlersuche ueber Events und Lane-Logs. Nur Platzhalter fuer Pfade und Adressen. |
| D8 | CI prueft Units mit `systemd-analyze verify`, wenn verfuegbar; sonst Syntaxpruefung ueber ein Skript. |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `deploy/systemd/*.service`, `*.timer`, `deploy/env.example` | Units, Platzhalter-Umgebung |
| `scripts/backup.sh`, `scripts/retention.py`, `scripts/restore_probe.sh` | Betriebsskripte |
| `Makefile` | Ziele aus D2 bis D6 |
| `tests/unit/test_retention.py` (Trockenlauf, Alterslogik, Schutz der Tabellen) | Tests |
| `.github/workflows/ci.yml` | Unit-Pruefung D8 |
| `docs/OPERATIONS.md` (neu), `docs/DATENBANK.md` | Doku |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | `make install` auf Testsystem, `systemctl --user status` | Dienste aktiv, Timer geplant |
| 2 | `make backup` | Dump plus Pruefsumme, alte Dumps entfernt |
| 3 | `make restore-probe` | PASS, temporaere DB entfernt, Event geschrieben |
| 4 | Restore in leere DB und `alembic check` | ohne Abweichung (Plan-Abnahme) |
| 5 | `retention.py` Trockenlauf | listet Kandidaten, loescht nichts; `--apply` loescht nur `runs/` und `redaction_maps` |
| 6 | `make update` mit schmutzigem Arbeitsbaum | Abbruch ohne Aenderung |
| 7 | Runner-Kill (`kill -9`) | Neustart durch systemd, Lease laeuft ab, Auftrag wird wieder aufgenommen |
| 8 | Units ohne Host-Werte | `grep` auf Adressen und Pfade ausser Platzhaltern leer |

## 7 Definition of Done

`make lint`, `make test` gruen; Owner-Protokoll zu Tests 1 bis 4 und 7; `docs/OPERATIONS.md`
vollstaendig; PR-Template mit Reviewer **Claude** und "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: Plan-Frage 3 offen ist (eigene Datenbank in der Instanz, Backup-Pfad);
`systemd --user` auf dem Zielsystem nicht ohne Anmeldung laeuft (Lingering); Etappe 15 die
Bindungspruefung nicht liefert.

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Backup ohne Restore-Test | woechentliche Probe, FAIL als Event |
| Aufbewahrung loescht Beweise | Trockenlauf Default, Events bleiben, Payload-Hash in `results` |
| Update bricht Betrieb | `--ff-only`, Migrationen vor Neustart, Dump vor Update (`make update` ruft `backup`) |

## 10 Bewusst offen

- Plan-Frage 3: Backup-Pfad und Instanz; restic-Ziel.
- Off-site-Kopie und Verschluesselung der Dumps.
- Abgleich mit Plan v1.1 bei Start der Etappe.
