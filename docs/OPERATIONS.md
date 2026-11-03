# Betriebsvorlagen und lokale Proben

Etappe 16 liefert Repo-Code und Tests. Es wurden keine Dienste installiert, gestartet,
aktualisiert oder auf einem Zielserver geprueft. Die systemd-Dateien sind Vorlagen fuer
Benutzerdienste; Inbetriebnahme bleibt ein eigener Owner-Schritt.

Zwischenstand: Die Umsetzung ist auf Owner-Wunsch pausiert. 66 neue Unit-Tests und die
Offline-Unit-Pruefung sind bestanden. DB-Integration, echter Dump-/Restore-Roundtrip,
vollstaendige Pflichtchecks und finale CI stehen nach der Parent-Integration noch aus.
Die folgenden Abschnitte beschreiben den vorbereiteten Vertrag, keine Live-Abnahme.

## Explizite Ausfuehrung

`make install`, `make start`, `make stop`, `make status`, `make update`, `make backup`,
`make retention` und `make restore-probe` zeigen standardmaessig nur einen Plan.
Erst `ARGS='--apply ...'` fuehrt die jeweilige Aktion aus. Weder ein fehlender Parameter
noch eine fehlende Konfiguration aktiviert einen Ersatzpfad.

Wartung verlangt ein ausdrueckliches Ziel. `--target test` verwendet ausschliesslich
`DECK_TEST_DATABASE_URL` mit einem Datenbanknamen auf `_test`; `--target runtime`
verwendet `DECK_DATABASE_URL`. Restore akzeptiert ausschliesslich das Testziel.
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

`backup --apply` erstellt ein PostgreSQL-Custom-Dump des Deck-Schemas in einem privaten
Staging-Verzeichnis. `pg_dump` und die Tabellenzaehlungen teilen dieselbe exportierte
Snapshot-Transaktion. Danach folgen SHA-256, Schema-Revision, Zeit und Zaehler im
versionierten Manifest. Erst eine atomare Umbenennung veroeffentlicht den vollstaendigen
Satz. Fehlgeschlagenes privates Staging wird sicher entfernt. Unsichere oder waehrenddessen
veraenderte `.pending-*`-Verzeichnisse bleiben zur Diagnose erhalten und sind keine fertigen Backups.

Backups enthalten auch sensible lokale Daten wie Maps und Authentisierungsdaten.
Verzeichnisse sind `0700`, Dateien `0600`; sie gehoeren nicht ins Repo. Die maximale
Dump-Groesse ist ein GiB, die Prozesszeit begrenzt. Ein Limitfehler publiziert keinen
abgeschnittenen Dump.

Nur vollstaendige, verifizierte Saetze aelter als 14 Tage werden entfernt, und nur wenn
mindestens ein neuerer gueltiger Satz vorhanden ist. Der letzte alte Backup-Satz bleibt
bei ausgefallener Sicherung erhalten. Das Manifest enthaelt keine Verbindungs-URL.

`--restic` aktiviert nach einem erfolgreichen Dump einen ausdruecklich konfigurierten
Hook fuer Backup- und Laufverzeichnisse. Repository und private Passwortdatei bleiben
extern. Der Prozess erhaelt einen geprueften offenen Dateideskriptor fuer das Passwort;
normale Logs enthalten weder Werte noch Prozessdiagnosen. Die Hook-Phasen werden separat
auditiert. Ein DB-Dump und eine parallele Dateikopie sind kein atomarer Gesamt-Snapshot;
Off-site-Restore und konsistente Artefaktwiederherstellung bleiben Inbetriebnahme.

## Restore nur in eine leere Testdatenbank

Eine Probe benoetigt einen ausdruecklich ausgewaehlten Backup-Satz sowie zwei verschiedene
`_test`-Datenbanken auf dem vorgesehenen Testdienst: Auditquelle und leeres Restore-Ziel.
Das Werkzeug erstellt oder loescht keine Datenbanken und keine Rollen. Nichtleere Ziele,
falsche Namen, fremde Pfade, geaenderte Dumps oder widerspruechliche Manifeste werden
abgewiesen. Beispiel mit vom Owner bereitgestellter leerer Testdatenbank:

```text
make restore-probe ARGS='--apply --target test --backup-name <backup-id>'
```

`pg_restore` laeuft in einer Transaktion ohne Owner-/Rechteuebernahme. Danach muessen
Tabellenzaehlungen, Revision und `alembic -x target=test check` passen. Erfolg wird erst
anschliessend in der separaten Auditquelle gespeichert. Ein Fehler hinterlaesst keinen
Erfolgsnachweis. Die Wiederherstellung einer bereits verwendeten Testdatenbank wird
abgewiesen; der Owner stellt fuer die naechste Probe ein frisches Ziel bereit.

Der woechentliche Timer ist bewusst eine **Trockenlauf-Erinnerung**, keine bestaetigte
Restore-Probe. Ein automatisch bereitgestelltes isoliertes Ziel und der operative Ablauf
muessen bei der Inbetriebnahme festgelegt werden. Die Integrationstests verwenden echte
Wegwerf-Testdatenbanken und passende PostgreSQL-Clients, keine uebersprungenen Ersatztests.

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
Aenderung wird ein Backup angelegt. Danach werden API und Runner gestoppt; Fetch,
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
