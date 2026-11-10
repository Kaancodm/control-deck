# Betriebsvorlagen und lokale Proben

Der laptop-unabhängige Betrieb, die Kontogrenze und der iPhone-Zugang stehen in
[BETRIEB_OHNE_LAPTOP.md](BETRIEB_OHNE_LAPTOP.md). Diese Datei beschreibt den Code und
die Voraussetzungen. Sie ist kein Nachweis, dass ein laufender Server so bleibt.

Etappe 16 liefert Repo-Code und Tests. Es wurden keine Dienste installiert, gestartet,
aktualisiert oder auf einem Zielserver geprüft. Die systemd-Dateien sind Vorlagen für
Benutzerdienste; Inbetriebnahme bleibt ein eigener Owner-Schritt.

Implementiert mit isolierten PostgreSQL-/pgvector-Datenbanken und Fake-CLIs. Ein echter
Dump-/Restore-Roundtrip und Prozessgruppenabbruch mit anschliessendem Lease-Retry sind
geprüft. Vollständige Pflichtchecks und finale CI werden in [STAND.md](STAND.md) und
Draft-PR #31 dokumentiert; diese Nachweise ersetzen keine Live-Abnahme.

## Explizite Ausführung

`make install`, `make start`, `make stop`, `make status`, `make update`, `make backup`,
`make retention` und `make restore-probe` zeigen standardmässig nur einen Plan.
Erst `ARGS='--apply ...'` führt die jeweilige Aktion aus. Weder ein fehlender Parameter
noch eine fehlende Konfiguration aktiviert einen Ersatzpfad.

Wartung verlangt ein ausdrückliches Ziel. `--target test` verwendet ausschliesslich
`DECK_TEST_DATABASE_URL` und `DECK_KB_TEST_DATABASE_URL` mit Namen auf `_test`;
`--target runtime` verwendet ausdrücklich `DECK_DATABASE_URL` und `DECK_KB_DATABASE_URL`.
Eine fehlende Wissens-DB bricht Backup und Update ab; es gibt kein zentrales Ersatzbackup.
Die Runtime-Variable gilt nur für Wartung und aktiviert keinen Live-Wissensadapter.
Restore akzeptiert ausschliesslich vier getrennte Testdatenbanken.
Keine Zugangsdaten erscheinen in Prozessargumenten oder normaler Kommandoausgabe.

Backup-Ziele sind nach Ziel getrennt: `--target runtime` schreibt nach `DECK_BACKUP_DIR`
(Default `backups/`), `--target test` ausschliesslich nach `DECK_TEST_BACKUP_DIR` (Default
`backups_test/`), nie in `DECK_BACKUP_DIR` selbst. Die Aufbewahrung läuft nur im Verzeichnis
des jeweiligen Ziels; ein Test- oder Probe-Lauf löscht keinen produktiven Satz. Die
Restore-Probe liest den gewählten Satz weiterhin aus `DECK_BACKUP_DIR` und schreibt dort nichts.

## Dienstvorlagen

`deploy/systemd/` enthält API, drei Instanzen des Runner-Templates sowie Backup-,
Retention- und Restore-Erinnerungstimer. Die Installation rendert Repository-, Python-
und Umgebungsdatei-Pfade. Pfade müssen absolut, ohne Leerraum oder systemd-Sonderzeichen
sein. Vorhandene abweichende Units werden nicht überschrieben.

Die Umgebungsdatei bleibt ausserhalb des Repos, gehört dem Dienstnutzer und hat Modus
`0600` in einem privaten Verzeichnis. Symlinks und Hardlinks werden abgewiesen.
`deploy/env.example` enthält ausschliesslich leere Konfigurationsfelder und relative
Standardverzeichnisse. Die Authentisierungsvorgaben aus `SECURITY.md` gelten zusätzlich.

Die Runner-Units laden nie die API-Umgebungsdatei (Owner-Token, Secret Key). Sie lesen eine
eigene, kleinere Datei (Standard `<environment-file>.runner`, oder `--runner-environment-file`)
mit denselben Schutzvorgaben (`0600`, privates Verzeichnis). Sie enthält nur die Variablen,
die `app/workers/daemon.py` weiterreicht; Vorlage: `deploy/runner.env.example`.
Der Akzeptanz-Evaluator startet seinen Kindprozess ausserdem mit einer Allowlist-Umgebung
(`PATH`, `LANG`, `LC_ALL`, `PYTHONPATH`), in einer eigenen Session und in einem Temp-Verzeichnis.

Eine spätere ausdrückliche Installation hat folgende Form:

```text
make install ARGS='--apply --repository <repo> --environment-file <private-env> --unit-directory <private-user-units>'
```

Sie kopiert die Units, lädt den User-Manager neu und aktiviert die Timer für spätere
Anmeldungen; sie startet keine Dienste. `start` und `stop` betreffen nur die festen
Deck-Units. `status --apply` liefert eine strukturierte Liste erlaubter Unit-Zustände,
keine Journalzeilen oder Umgebungswerte.

Die API verwendet ausschliesslich `app.api.server_main` mit den Bindungsprüfungen aus
Etappe 15. Die Vorlagen begrenzen Schreibpfade auf `runs/`, `inbox/` beziehungsweise
`backups/` im Repository. Abweichende Ablagen benötigen einen separat geprüften
Owner-Drop-in. `UMask=0077`, `NoNewPrivileges`, private Temporärverzeichnisse und
Prozessgruppenbeendigung sind gesetzt. Die tatsächliche Unterstützung dieser
Schutzfunktionen durch den User-Manager ist Inbetriebnahme.

## Runner und Dienstende

Der Supervisor startet je Iteration genau einen Host-Runner-Versuch. Dessen gemeinsame
Queue-Logik bestimmt Freigabe, Lane, Versuchsbudget, Backoff, Deadline und Fallback.
Der Supervisor führt keine versteckten Modell-Wiederholungen aus.

Ohne `--simulate` verlangt der Supervisor ausdrücklich `DECK_LIVE_ENABLED=1` oder
`DECK_LIVE_ENABLED=true` in der privaten Runner-Umgebungsdatei. Grossschreibung,
Leerraum und alle anderen Werte schalten Live aus. Nur Ollama hat einen Live-Transport.
`--simulate` startet weiterhin die Fake-CLI des Workers, unabhängig vom Live-Flag;
`--iterations` begrenzt lokale technische Proben. Die Unit-Vorlage benötigt keine neuen
Argumente. `make install` rendert ihren Pfad zur separaten Runner-Umgebungsdatei.

`DECK_OLLAMA_URL` ist bei aktiviertem Live-Profil Pflicht. Erlaubt sind ausschliesslich
HTTP, das IP-Literal `127.0.0.1` oder `[::1]` und ein ausdrücklicher Port von 1 bis 65535.
Ein Pfad (auch `/`), Benutzerangaben, Query, Fragment, Hostname oder HTTPS wird abgewiesen.
Das Flag und die URL gehören nie in die API-Umgebungsdatei. Proxy-Variablen und
`OLLAMA_HOST` werden nicht an Runner weitergereicht.

| Ausgang | Exit-Code | Bedeutung |
|---|---|---|
| Flag aus | `78`, `live_disabled` | Konfigurationsverweigerung; Unit startet nicht erneut |
| Andere Lane bei Flag an | `78`, `live_lane_not_supported` | Live nur für Ollama |
| Fehlende oder ungültige URL | `78`, `live_url_invalid` | Korrektur der Runner-Konfiguration nötig |
| Supervisor-Laufzeitfehler | `1` | systemd versucht den Neustart nach fünf Sekunden |
| Kontrolliertes Dienstende | `0` | Keine neuen Versuche. Ein vom Stopp-Signal beendeter Versuch behält seinen Lease und läuft nach dessen Ablauf erneut. Scheitert ein Versuch während des Stopps anders (Timeout, Ausgabelimit, echter Exit-Code), wird er zuerst gezählt und als `runner_attempt_failed` im Journal vermerkt |

Ein unerreichbarer Ollama-Dienst beendet die Poll-Schleife nicht mit `78`.
Der Runner meldet die Lane als `unavailable`; weitere Polls können die Erholung erkennen.
Die Loopback-API zieht keine fehlenden Modelle nach. Ein Modellfehler nach dem Claim
bleibt ein fehlgeschlagener Versuch nach den bestehenden Retry-Regeln.

### Owner-Prüfung vor dem Ollama-Live-Lauf

Der Owner setzt **im Ollama-Dienst** `OLLAMA_NO_CLOUD=1` und eine Loopback-Bindung.
Eine Variable nur in der Deck-Runner-Datei ändert den laufenden Ollama-Dienst nicht.
Der Owner lädt die erlaubten Modelle vorher und vergleicht das Inventar vor und nach
dem Lauf. Die Allowlist stammt aus `lanes.ollama.models`; Modellnamen brauchen ein
ausdrückliches Tag. Zunächst wird nur eine Runner-Instanz mit `max_parallel=1` betrieben.

Version, Modellfähigkeiten, Cloud-Sperre, Abbruch und vollständiger Abschluss brauchen
einen dokumentierten Servernachweis in `ABNAHME_CHECKLISTE_ENTWURF.md`. Repo-Tests verwenden
nur einen lokalen HTTP-Stub. Sie konfigurieren weder einen echten Ollama-Dienst noch
laden sie ein Modell. Die Restore-Erinnerung bleibt ein Probelauf ohne `--apply`.

SIGTERM oder SIGINT beendet den aktiven Host-Prozess samt Unterprozessen und stoppt neue
Claims. Der Supervisor endet dabei mit `0`, auch wenn der Host-Prozess durch dasselbe
Signal schon beendet wurde. Das schreibt **keinen** Owner-Abbruch. Die bestehende Lease läuft aus; der Reaper
plant den Versuch gemäss Etappe 10 und verbleibendem Budget erneut ein. Nur eine echte
Owner-Deadline erzeugt `expired`. Der Not-Aus in der Oberfläche bleibt davon getrennt:
Er setzt den ausdrücklichen Abbruchpfad für aktive Aufgaben.

Betriebsprozesse verwenden `DECK_OPERATIONS_PATH` als explizite Liste absoluter
Suchverzeichnisse für uv, restic und PostgreSQL-Clients. Standard:
`/usr/local/bin:/usr/bin:/bin`; eigene uv-/PGDG-Verzeichnisse müssen bewusst ergänzt
werden. Der allgemeine Prozess-PATH und fremde Zugangsdaten werden nicht vererbt.
Leere oder relative Einträge werden abgewiesen.

Die Backup-Unit liest ihre verpflichtende private `EnvironmentFile` einschliesslich beider
DB-Konfigurationen. `ProtectHome=read-only` erlaubt das Lesen von Werkzeugen und Konfiguration;
`ReadWritePaths` entspricht dem beim Installieren aus `DECK_BACKUP_DIR` gelesenen Ziel.
Der Installer legt dieses Ziel privat an und lehnt Symlinks sowie Shell-Ausdrücke ab.
Änderungen des Backup-Ziels erfordern eine neu geprüfte Unit-Konfiguration. Restic nutzt
einen privaten temporären Cache; pauschale Schreibrechte im Home-Verzeichnis sind unnötig.
Die wirksamen Sandbox-Pfade auf dem Betriebsrechner bleiben **Inbetriebnahme**.

## Backup und 14 Tage Aufbewahrung

`backup --apply` erstellt einen vollständigen Satz aus zwei PostgreSQL-Custom-Dumps:
`database.dump` für das zentrale `public` und `knowledge.dump` für `public`, `deck_kb`
und die pgvector-Erweiterung der separaten Wissens-DB. Pro Datenbank teilen Dump,
Tabellenzählungen und Revision dieselbe exportierte Snapshot-Transaktion. Die beiden
Datenbanken teilen **keinen** gemeinsamen Snapshot; parallele Exporte können daher
unterschiedliche Zeitpunkte abbilden.

Das Manifest in Version 2 verlangt beide Datenbanken mit SHA-256, Grösse, Revision und
schemaqualifizierten Tabellenzählern; für Wissen kommt die pgvector-Version hinzu.
Erst nach beiden erfolgreichen Dumps veröffentlicht eine atomare Umbenennung den Satz.
Ein Fehler beim zweiten Dump publiziert auch den ersten nicht. Fehlgeschlagenes privates
Staging wird sicher entfernt. Unsichere oder währenddessen veränderte `.pending-*`-
Verzeichnisse bleiben zur Diagnose erhalten und sind keine fertigen Backups.

Ältere zentrale Sätze in Manifestversion 1 bleiben unvollständige Altsicherungen.
Restore und Restic lehnen sie ab; Pruning überspringt und auditiert sie. Sie werden weder
als vollständig bezeichnet noch automatisch gelöscht. Ein neuer vollständiger Satz ist erforderlich.

Backups enthalten auch sensible lokale Daten wie Maps und Authentisierungsdaten.
Verzeichnisse sind `0700`, Dateien `0600`; sie gehören nicht ins Repo. Die maximale
Dump-Grösse ist je Datenbank ein GiB, die Prozesszeit begrenzt. Ein Limitfehler publiziert keinen
abgeschnittenen Dump.

Nur vollständige, verifizierte Sätze älter als 14 Tage werden entfernt, und nur wenn
mindestens ein neuerer gültiger Satz vorhanden ist. Der letzte alte Backup-Satz bleibt
bei ausgefallener Sicherung erhalten. Das Manifest enthält keine Verbindungs-URL.
Defekte einzelne Sätze bleiben unangetastet und erzeugen `operation.retention` mit Phase
`failed` und Grund `invalid_backup_set`. Andere verifizierte Sätze werden weiter verarbeitet.
Ein Fehler der nachgelagerten Retention ändert einen erfolgreich publizierten Dump nicht
in einen Backup-Fehler: Exit 0 und Gesamtstatus `completed` bleiben erhalten, das separate
Feld `retention` meldet `partial_failure`, `failed` oder `audit_failed`. Ein allgemeiner
Retention-Fehler wird als `backup_retention_failed` auditiert. Kann das Audit nicht geschrieben
werden, erfolgen keine weiteren Löschungen. Unsichere Root-Pfade werden weiterhin abgewiesen.

`--restic` aktiviert nach einem erfolgreichen Dump einen ausdrücklich konfigurierten
Hook für Backup- und Laufverzeichnisse. Vor dem Kopieren müssen vorhandene Sätze
beide Archive und gültige Prüfsummen enthalten; ein leeres Backup-Verzeichnis reicht nicht. Repository und private Passwortdatei bleiben
extern. Der Prozess erhält einen geprüften offenen Dateideskriptor für das Passwort;
normale Logs enthalten weder Werte noch Prozessdiagnosen. Die Hook-Phasen werden separat
auditiert. Ein Restic-Fehler bleibt ein Gesamtfehler (`failed`, Exit 1), selbst wenn der
lokale Dump bereits erfolgreich publiziert wurde. Ein DB-Dump und eine parallele Dateikopie sind kein atomarer Gesamt-Snapshot;
Off-site-Restore und konsistente Artefaktwiederherstellung bleiben Inbetriebnahme.
Die zentrale und die separate Wissens-DB sind Teil desselben publizierten Backup-Satzes.
Ein konsistenter Gesamtzeitpunkt über beide Datenbanken und Laufdateien bleibt Inbetriebnahme.

## Restore nur in zwei leere Testdatenbanken

Eine Probe benötigt einen ausgewählten Backup-Satz und vier verschiedene `_test`-DBs
auf dem vorgesehenen Testdienst: zentrale Auditquelle, Wissensquelle, leeres zentrales
Ziel und leeres Wissensziel. Die Ziele werden explizit über `DECK_RESTORE_TEST_DATABASE_URL`
und `DECK_KB_RESTORE_TEST_DATABASE_URL` gesetzt. Quellen oder dasselbe Ziel dürfen nicht
wiederverwendet werden. Das Werkzeug erstellt oder löscht keine Datenbanken und Rollen.

```text
make restore-probe ARGS='--apply --target test --backup-name <backup-id>'
```

Vor dem ersten Eingriff müssen **beide** Ziele leer sein, beide Archive samt Prüfsummen
vorliegen und beide Manifest-Revisionen zum aktuellen Code passen. Falsche Schemaangaben,
eine fehlende oder korrupte Wissensdatei und ein befülltes Wissensziel lassen das zentrale
Ziel unverändert. Die geprüften Dateideskriptoren bleiben für die Wiederherstellung offen.
Nach Archivprüfung wird die Leerheit erneut kontrolliert. Nur das leere `public` wird ohne
`CASCADE` entfernt, weil die Dumps es selbst anlegen; bestehende Objekte werden nie mit
`--clean` entfernt.

`pg_restore` verwendet je Datenbank eine Transaktion, ohne Owner-/Rechteübernahme. Danach
müssen beide Tabelleninventare, Zeilenzähler und Revisionen sowie die pgvector-Version
passen. Der Wissensvertrag prüft unter anderem Vektordimension und unveränderliche
Dokumente. Zentrales `alembic -x target=test check` und separates
`alembic -c alembic_kb.ini check` laufen gegen die jeweiligen Ziele. Erst danach wird ein
vollständiger Erfolg in der separaten zentralen Auditquelle gespeichert.

Es gibt keine verteilte Restore-Transaktion. Scheitert der zweite Restore oder eine
nachfolgende Prüfung, bleibt die Probe **fehlgeschlagen** (`restore_partial_failure`),
auch wenn ein Ziel bereits Daten enthält. Beide Wegwerf-Ziele bleiben zur Diagnose
zurück und gelten als gesperrt für eine Übernahme; es gibt keinen automatischen
Rollback und keinen Erfolgsnachweis. Für einen neuen Versuch sind zwei frische leere
Ziele erforderlich. Das Werkzeug räumt keine fremden Daten weg.

Der wöchentliche Timer ist überall eine **Trockenlauf-Erinnerung**, keine automatische
oder bestätigte Restore-Probe. Seine Service-Vorlage startet nur `restore-probe` ohne
`--apply`. Automatische Bereitstellung leerer Ziele und der operative Ablauf bleiben
Inbetriebnahme. Integrationstests verwenden echte Wegwerf-Testdatenbanken und passende
PostgreSQL-17-Clients, keine übersprungenen Ersatztests.

## 90 Tage für abgeschlossene Laufdaten

Retention verlangt einen terminalen Task ohne vorhandene Lease und einen Abschlussstand
älter als 90 Tage. `review`, wartende oder laufende Tasks bleiben erhalten. Das gilt auch
für sehr alte Dateien und für `cancelled` mit noch nicht bestätigter Lease.

Unter erneutem Task-Lock wird das Laufverzeichnis in einen taskgebundenen Zwischenpfad
verschoben. Sichere, begrenzte Bereinigung entfernt danach Artefakte, Redaction-Maps und
genehmigte Kontext-Snapshots. Tasks, Results, Task-Events, Wissens-Dokumente und
Exportentscheidungen bleiben bestehen. Verzeichnis-Mtime allein ist nie ein Löschgrund.
Links, ungültige Rechte und unerwartete Dateitypen brechen die Bereinigung ab.

Dateisystem und DB teilen keine Transaktion. Deshalb dokumentieren unveränderliche
`operations_events` die Phasen `started`, `artifacts_removed`, `completed` und `failed`.
Ein abgebrochener Zwischenpfad kann beim nächsten Lauf wieder aufgenommen werden;
ein erfolgreich abgeschlossener Task wird nicht bei jedem Timer erneut protokolliert.
DB-Fehler können bereits gelöschte Dateien nicht zurückholen: Ein offener oder
fehlgeschlagener Lauf ist deshalb sichtbar und wird niemals als abgeschlossen bezeichnet.

## Update und Fehlerbehandlung

Ein ausdrückliches Update verlangt einen sauberen Arbeitsbaum auf `main`. Vor der ersten
Änderung werden die vorige Git-Revision und die aktiven Units festgehalten und ein
vollständiger Backup-Satz beider Datenbanken angelegt. Danach werden API und Runner gestoppt;
Fetch, ausschliesslicher Fast-forward, `uv sync --frozen` und Migration erfolgen ohne laufende
alte Worker. Nach Erfolg starten nur zuvor aktive Units.

Bei einem Fehler nach Beginn des Updates wird die gespeicherte Revision per
`git checkout --detach <vorige-revision>` wiederhergestellt und ihre eingefrorene Umgebung
synchronisiert. Ein `finally`-Pfad versucht jede zuvor aktive Unit einzeln wieder zu starten,
auch wenn eine andere Wiederherstellungsaktion scheitert. Inaktive Units bleiben inaktiv.
Der Ablauf endet mit Exit 78 und `operation.update`, Phase `failed`: `update_failed` bei
wiederhergestelltem Code oder `update_rollback_failed` bei unvollständiger Wiederherstellung.
Ein erfolgreiches Backup bleibt erhalten. Der Checkout ist danach detached; ein neuer
Update-Versuch verlangt wieder einen vom Owner vorbereiteten sauberen `main`-Checkout.
Es gibt keinen automatischen Rollback einer bereits ausgeführten Schema-Migration.
Kompatibilität des vorigen Codes mit dem migrierten Schema und reale Dienstbereitschaft
bleiben **Inbetriebnahme**; ein erfolgreicher systemctl-Start ist kein Health-Nachweis.

Betriebsfehler melden stabile Codes und Operation-IDs. Die geschützte Audit-Ansicht aus
Etappe 17 kann diese Phasen anzeigen. Rohdaten, Passwörter, Dateipfade und Ausgabe von
Unterprozessen gehören nicht in die Auditdetails.

`make update` migriert nur die zentrale Kette. Die Wissens-DB hat eine eigene Alembic-Kette
(`alembic_kb.ini`), die ein getrennter, ausdrücklicher Schritt bleibt.

## Rollout echtes Embedding der Wissens-DB

Umstellung von `deck-fake-embedding-v1` (8 Dimensionen) auf `qwen3-embedding:0.6b`
(1024 Dimensionen) mit Migration `kb_0002`, siehe [WISSENS_DB.md](WISSENS_DB.md) und ADR 0011.
Alle Schritte führt der Owner aus, einzeln und jeweils erst nach bestandenem Vorgänger; jeder
Prod-Zugriff braucht ein ausdrückliches GO. Agents bereiten nur vor. Werte stehen nur in der
privaten Env-Datei, nie im Repo oder in Logs.

0. **Preflight, nur lesend.** `alembic_kb_version` ist `kb_0001`; Vertragszeile
   (`deck_kb.v1`, `deck-fake-embedding-v1`, 8); Spalte `deck_kb.chunks.embedding` ist
   `vector(8)`; `deck_kb.documents` und `deck_kb.chunks` haben je 0 Zeilen; pgvector-Version
   wie im letzten Backup-Manifest; `/api/tags` listet `qwen3-embedding:0.6b`. Zusätzlich
   prüfen, ob `/api/embed` die Kennung mit Suffix `:local` annimmt (siehe
   [LANES.md](LANES.md#embedding-vertrag-apiembed), Status `unproven`). Bei jeder Abweichung Stopp.
1. **Backup mit dem alten Release:** `make backup`. Dieser Satz lässt sich nur mit altem Code
   zurückspielen, weil Restore eine passende Manifest-Revision verlangt.
2. **KB-Migration, eigener Schritt,** als Tabelleneigentümer:
   ```bash
   uv run alembic -c alembic_kb.ini -x target=runtime upgrade head
   uv run alembic -c alembic_kb.ini -x target=runtime current
   uv run alembic -c alembic_kb.ini -x target=runtime check
   ```
   `kb_0002` sperrt Vertrag, Dokumente und Chunks, bricht bei vorhandenen Zeilen, fremder Vertragszeile oder
   anderem Spaltentyp ohne Änderung ab und prüft danach die Unveränderlichkeits-Trigger. Ein
   Runtime-Downgrade ist gesperrt und nicht nötig.
3. **Private Env-Datei:** `DECK_KB_PROFILE=runtime`, `DECK_KB_EMBEDDING_PROVIDER=ollama`,
   `DECK_KB_EMBEDDING_MODEL=qwen3-embedding:0.6b`, `DECK_KB_EMBEDDING_DIMENSION=1024`;
   `DECK_KB_DATABASE_URL` und `DECK_OLLAMA_URL` sind gesetzt. Optional
   `DECK_KB_EMBEDDING_TIMEOUT_S` (Standard 60) und `DECK_KB_EMBEDDING_KEEP_ALIVE`
   (Standard `server`). Der Ollama-Dienst bleibt unverändert.
4. **`make update` und Neustart.** Die Reihenfolge von 2 und 4 ist unkritisch: Jeder
   Zwischenzustand endet in `knowledge_contract_mismatch` oder
   `knowledge_embedding_configuration_invalid`, ohne Schreiben.
5. **Backup mit dem neuen Release:** `make backup` (Manifest mit `kb_0002`).
6. **Prod-Prüfung ohne Schreiben:** Suche über `/ui/knowledge` mit synthetischer Anfrage;
   erwartet wird eine leere Trefferliste. Die Zähler bleiben 0.
7. **Testdokument nur in einer isolierten Kopie:** Restore-Probe des Backups aus 5 in die
   `_test`-Ziele oder frische `_test`-DB mit `upgrade head`; dort `DECK_KB_PROFILE=simulation`
   mit `ollama` und `scripts/kb_import.py … --simulate` mit synthetischem Text; Suche mit
   umformulierter Anfrage und `resolve` prüfen; danach verwirft der Owner die Kopie.
   `kb_import.py` weist das Runtime-Profil ab (`knowledge_import_simulation_only`).
8. **Kaltstart des Chat-Modells** nach Schritt 6 messen, als Grundlage für den Entscheid
   zum Modellwechsel zwischen Chat und Embedding.

Warum eine Kopie statt Prod: Die Trigger weisen UPDATE und DELETE für jede Rolle ab. Ein
Testdokument in Prod liesse sich nur per `TRUNCATE`, deaktivierten Triggern oder Restore
entfernen; alle drei heben die Unveränderlichkeit auf.

**Backup-Kompatibilität.** Restore prüft den Wissensvertrag gegen die konfigurierte
Embedding-Spec. Eine Restore-Probe eines Runtime-Backups nach `kb_0002` braucht deshalb
dieselben `DECK_KB_EMBEDDING_*`-Variablen wie der Betrieb; mit Fake-Standard scheitert sie
mit `knowledge_contract_mismatch`. Sätze vor `kb_0002` brauchen den alten Code.

**Rückweg.** Vor Schritt 4: Env-Werte entfernen, Profil bleibt `disabled`. Nach Schritt 4:
`DECK_KB_PROFILE` leeren und neu starten; die Wissens-DB bleibt leer und auf `kb_0002`.
Alter Code gegen `kb_0002` liefert nur `knowledge_contract_mismatch`, kein Schreiben.

## Inbetriebnahme

Offen bleiben der reale User-Manager mit Lingering, Bindung und TLS, Datei-/Backup-Ziele,
passende PostgreSQL-Clientversionen, verfügbarer Speicher, Restic-Ziel und dessen Restore,
automatische Bereitstellung leerer Probe-Datenbanken sowie reale Lane-Isolation und Logins.
Keine dieser Host- oder Anbieterprüfungen wurde durch diese Etappe ausgeführt.
