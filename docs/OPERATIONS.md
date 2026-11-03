# Betriebsvorlagen und lokale Proben

Etappe 16 liefert Repo-Code und Tests. Es wurden keine Dienste installiert, gestartet,
aktualisiert oder auf einem Zielserver geprueft. Die systemd-Dateien sind Vorlagen fuer
Benutzerdienste; Inbetriebnahme bleibt ein eigener Owner-Schritt.

Implementiert mit isolierten PostgreSQL-/pgvector-Datenbanken und Fake-CLIs. Ein echter
Dump-/Restore-Roundtrip und Prozessgruppenabbruch mit anschliessendem Lease-Retry sind
geprueft. Vollstaendige Pflichtchecks und finale CI werden in [STAND.md](STAND.md) und
Draft-PR #31 dokumentiert; diese Nachweise ersetzen keine Live-Abnahme.

## Explizite Ausfuehrung

`make install`, `make start`, `make stop`, `make status`, `make update`, `make backup`,
`make retention` und `make restore-probe` zeigen standardmaessig nur einen Plan.
Erst `ARGS='--apply ...'` fuehrt die jeweilige Aktion aus. Weder ein fehlender Parameter
noch eine fehlende Konfiguration aktiviert einen Ersatzpfad.

Wartung verlangt ein ausdrueckliches Ziel. `--target test` verwendet ausschliesslich
`DECK_TEST_DATABASE_URL` und `DECK_KB_TEST_DATABASE_URL` mit Namen auf `_test`;
`--target runtime` verwendet ausdruecklich `DECK_DATABASE_URL` und `DECK_KB_DATABASE_URL`.
Eine fehlende Wissens-DB bricht Backup und Update ab; es gibt kein zentrales Ersatzbackup.
Die Runtime-Variable gilt nur fuer Wartung und aktiviert keinen Live-Wissensadapter.
Restore akzeptiert ausschliesslich vier getrennte Testdatenbanken.
Keine Zugangsdaten erscheinen in Prozessargumenten oder normaler Kommandoausgabe.

## Dienstvorlagen

`deploy/systemd/` enthaelt API, drei Instanzen des Runner-Templates sowie Backup-,
Retention- und Restore-Erinnerungstimer. Die Installation rendert Repository-, Python-
und Umgebungsdatei-Pfade. Pfade muessen absolut, ohne Leerraum oder systemd-Sonderzeichen
sein. Vorhandene abweichende Units werden nicht ueberschrieben.

Die Umgebungsdatei bleibt ausserhalb des Repos, gehoert dem Dienstnutzer und hat Modus
`0600` in einem privaten Verzeichnis. Symlinks und Hardlinks werden abgewiesen.
`deploy/env.example` enthaelt ausschliesslich leere Konfigurationsfelder und relative
Standardverzeichnisse. Die Authentisierungsvorgaben aus `SECURITY.md` gelten zusaetzlich.

Eine spaetere ausdrueckliche Installation hat folgende Form:

```text
make install ARGS='--apply --repository <repo> --environment-file <private-env> --unit-directory <private-user-units>'
```

Sie kopiert die Units, laedt den User-Manager neu und aktiviert die Timer fuer spaetere
Anmeldungen; sie startet keine Dienste. `start` und `stop` betreffen nur die festen
Deck-Units. `status --apply` liefert eine strukturierte Liste erlaubter Unit-Zustaende,
keine Journalzeilen oder Umgebungswerte.

Die API verwendet ausschliesslich `app.api.server_main` mit den Bindungspruefungen aus
Etappe 15. Die Vorlagen begrenzen Schreibpfade auf `runs/`, `inbox/` beziehungsweise
`backups/` im Repository. Abweichende Ablagen benoetigen einen separat geprueften
Owner-Drop-in. `UMask=0077`, `NoNewPrivileges`, private Temporaerverzeichnisse und
Prozessgruppenbeendigung sind gesetzt. Die tatsaechliche Unterstuetzung dieser
Schutzfunktionen durch den User-Manager ist Inbetriebnahme.

## Runner und Dienstende

Der Supervisor startet je Iteration genau einen Host-Runner-Versuch. Dessen gemeinsame
Queue-Logik bestimmt Freigabe, Lane, Versuchsbudget, Backoff, Deadline und Fallback.
Der Supervisor fuehrt keine versteckten Modell-Wiederholungen aus.

Die derzeitige Dienstvorlage bleibt mit Exit-Code `78` gesperrt, solange kein technisch
nachgewiesenes Live-Profil vorhanden ist. Sie verarbeitet keine produktiven Auftraege mit
Fake-Ergebnissen. Nur `--simulate` aktiviert ausdruecklich die Fake-CLI eines Workers.
`--iterations` begrenzt lokale technische Proben.

SIGTERM oder SIGINT beendet den aktiven Host-Prozess samt Unterprozessen und stoppt neue
Claims. Das schreibt **keinen** Owner-Abbruch. Die bestehende Lease laeuft aus; der Reaper
plant den Versuch gemaess Etappe 10 und verbleibendem Budget erneut ein. Nur eine echte
Owner-Deadline erzeugt `expired`. Der Not-Aus in der Oberflaeche bleibt davon getrennt:
Er setzt den ausdruecklichen Abbruchpfad fuer aktive Aufgaben.

## Backup und 14 Tage Aufbewahrung

`backup --apply` erstellt einen vollstaendigen Satz aus zwei PostgreSQL-Custom-Dumps:
`database.dump` fuer das zentrale `public` und `knowledge.dump` fuer `public`, `deck_kb`
und die pgvector-Erweiterung der separaten Wissens-DB. Pro Datenbank teilen Dump,
Tabellenzaehlungen und Revision dieselbe exportierte Snapshot-Transaktion. Die beiden
Datenbanken teilen **keinen** gemeinsamen Snapshot; parallele Exporte koennen daher
unterschiedliche Zeitpunkte abbilden.

Das Manifest in Version 2 verlangt beide Datenbanken mit SHA-256, Groesse, Revision und
schemaqualifizierten Tabellenzaehlern; fuer Wissen kommt die pgvector-Version hinzu.
Erst nach beiden erfolgreichen Dumps veroeffentlicht eine atomare Umbenennung den Satz.
Ein Fehler beim zweiten Dump publiziert auch den ersten nicht. Fehlgeschlagenes privates
Staging wird sicher entfernt. Unsichere oder waehrenddessen veraenderte `.pending-*`-
Verzeichnisse bleiben zur Diagnose erhalten und sind keine fertigen Backups.

Aeltere zentrale Saetze in Manifestversion 1 bleiben unvollstaendige Altsicherungen.
Restore, Restic und Pruning lehnen sie ab; sie werden weder als vollstaendig bezeichnet
noch automatisch geloescht. Ein neuer vollstaendiger Satz ist erforderlich.

Backups enthalten auch sensible lokale Daten wie Maps und Authentisierungsdaten.
Verzeichnisse sind `0700`, Dateien `0600`; sie gehoeren nicht ins Repo. Die maximale
Dump-Groesse ist je Datenbank ein GiB, die Prozesszeit begrenzt. Ein Limitfehler publiziert keinen
abgeschnittenen Dump.

Nur vollstaendige, verifizierte Saetze aelter als 14 Tage werden entfernt, und nur wenn
mindestens ein neuerer gueltiger Satz vorhanden ist. Der letzte alte Backup-Satz bleibt
bei ausgefallener Sicherung erhalten. Das Manifest enthaelt keine Verbindungs-URL.

`--restic` aktiviert nach einem erfolgreichen Dump einen ausdruecklich konfigurierten
Hook fuer Backup- und Laufverzeichnisse. Vor dem Kopieren muessen vorhandene Saetze
beide Archive und gueltige Pruefsummen enthalten; ein leeres Backup-Verzeichnis reicht nicht. Repository und private Passwortdatei bleiben
extern. Der Prozess erhaelt einen geprueften offenen Dateideskriptor fuer das Passwort;
normale Logs enthalten weder Werte noch Prozessdiagnosen. Die Hook-Phasen werden separat
auditiert. Ein DB-Dump und eine parallele Dateikopie sind kein atomarer Gesamt-Snapshot;
Off-site-Restore und konsistente Artefaktwiederherstellung bleiben Inbetriebnahme.
Die zentrale und die separate Wissens-DB sind Teil desselben publizierten Backup-Satzes.
Ein konsistenter Gesamtzeitpunkt ueber beide Datenbanken und Laufdateien bleibt Inbetriebnahme.

## Restore nur in zwei leere Testdatenbanken

Eine Probe benoetigt einen ausgewaehlten Backup-Satz und vier verschiedene `_test`-DBs
auf dem vorgesehenen Testdienst: zentrale Auditquelle, Wissensquelle, leeres zentrales
Ziel und leeres Wissensziel. Die Ziele werden explizit ueber `DECK_RESTORE_TEST_DATABASE_URL`
und `DECK_KB_RESTORE_TEST_DATABASE_URL` gesetzt. Quellen oder dasselbe Ziel duerfen nicht
wiederverwendet werden. Das Werkzeug erstellt oder loescht keine Datenbanken und Rollen.

```text
make restore-probe ARGS='--apply --target test --backup-name <backup-id>'
```

Vor dem ersten Eingriff muessen **beide** Ziele leer sein, beide Archive samt Pruefsummen
vorliegen und beide Manifest-Revisionen zum aktuellen Code passen. Falsche Schemaangaben,
eine fehlende oder korrupte Wissensdatei und ein befuelltes Wissensziel lassen das zentrale
Ziel unveraendert. Die geprueften Dateideskriptoren bleiben fuer die Wiederherstellung offen.
Nach Archivpruefung wird die Leerheit erneut kontrolliert. Nur das leere `public` wird ohne
`CASCADE` entfernt, weil die Dumps es selbst anlegen; bestehende Objekte werden nie mit
`--clean` entfernt.

`pg_restore` verwendet je Datenbank eine Transaktion, ohne Owner-/Rechteuebernahme. Danach
muessen beide Tabelleninventare, Zeilenzaehler und Revisionen sowie die pgvector-Version
passen. Der Wissensvertrag prueft unter anderem Vektordimension und unveraenderliche
Dokumente. Zentrales `alembic -x target=test check` und separates
`alembic -c alembic_kb.ini check` laufen gegen die jeweiligen Ziele. Erst danach wird ein
vollstaendiger Erfolg in der separaten zentralen Auditquelle gespeichert.

Es gibt keine verteilte Restore-Transaktion. Scheitert der zweite Restore oder eine
nachfolgende Pruefung, bleibt die Probe **fehlgeschlagen** (`restore_partial_failure`),
auch wenn ein Ziel bereits Daten enthaelt. Beide Wegwerf-Ziele bleiben zur Diagnose
zurueck und gelten als gesperrt fuer eine Uebernahme; es gibt keinen automatischen
Rollback und keinen Erfolgsnachweis. Fuer einen neuen Versuch sind zwei frische leere
Ziele erforderlich. Das Werkzeug raeumt keine fremden Daten weg.

Der woechentliche Timer ist ueberall eine **Trockenlauf-Erinnerung**, keine automatische
oder bestaetigte Restore-Probe. Seine Service-Vorlage startet nur `restore-probe` ohne
`--apply`. Automatische Bereitstellung leerer Ziele und der operative Ablauf bleiben
Inbetriebnahme. Integrationstests verwenden echte Wegwerf-Testdatenbanken und passende
PostgreSQL-17-Clients, keine uebersprungenen Ersatztests.

## 90 Tage fuer abgeschlossene Laufdaten

Retention verlangt einen terminalen Task ohne vorhandene Lease und einen Abschlussstand
aelter als 90 Tage. `review`, wartende oder laufende Tasks bleiben erhalten. Das gilt auch
fuer sehr alte Dateien und fuer `cancelled` mit noch nicht bestaetigter Lease.

Unter erneutem Task-Lock wird das Laufverzeichnis in einen taskgebundenen Zwischenpfad
verschoben. Sichere, begrenzte Bereinigung entfernt danach Artefakte, Redaction-Maps und
genehmigte Kontext-Snapshots. Tasks, Results, Task-Events, Wissens-Dokumente und
Exportentscheidungen bleiben bestehen. Verzeichnis-Mtime allein ist nie ein Loeschgrund.
Links, ungueltige Rechte und unerwartete Dateitypen brechen die Bereinigung ab.

Dateisystem und DB teilen keine Transaktion. Deshalb dokumentieren unveraenderliche
`operations_events` die Phasen `started`, `artifacts_removed`, `completed` und `failed`.
Ein abgebrochener Zwischenpfad kann beim naechsten Lauf wieder aufgenommen werden;
ein erfolgreich abgeschlossener Task wird nicht bei jedem Timer erneut protokolliert.
DB-Fehler koennen bereits geloeschte Dateien nicht zurueckholen: Ein offener oder
fehlgeschlagener Lauf ist deshalb sichtbar und wird niemals als abgeschlossen bezeichnet.

## Update und Fehlerbehandlung

Ein ausdrueckliches Update verlangt einen sauberen Arbeitsbaum auf `main`. Vor der ersten
Aenderung wird ein vollstaendiger Backup-Satz beider Datenbanken angelegt. Danach werden API und Runner gestoppt; Fetch,
ausschliesslicher Fast-forward, `uv sync --frozen` und Migration erfolgen ohne laufende
alte Worker. Erst bei Erfolg folgt ein Neustart. Konflikte, Migration- oder Backup-Fehler
beenden den Ablauf; nach einem Stopp bleiben die Dienste bis zum ausdruecklichen
Owner-Start gestoppt. Es gibt keinen automatischen Rollback einer Migration.

Betriebsfehler melden stabile Codes und Operation-IDs. Die geschuetzte Audit-Ansicht aus
Etappe 17 kann diese Phasen anzeigen. Rohdaten, Passwoerter, Dateipfade und Ausgabe von
Unterprozessen gehoeren nicht in die Auditdetails.

## Inbetriebnahme

Offen bleiben der reale User-Manager mit Lingering, Bindung und TLS, Datei-/Backup-Ziele,
passende PostgreSQL-Clientversionen, verfuegbarer Speicher, Restic-Ziel und dessen Restore,
automatische Bereitstellung leerer Probe-Datenbanken sowie reale Lane-Isolation und Logins.
Keine dieser Host- oder Anbieterpruefungen wurde durch diese Etappe ausgefuehrt.
