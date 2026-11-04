# Datenbank (Control Deck)

Etappe `03-db-schema` fuehrt PostgreSQL als persistente Queue und Audit-Store ein. Die Verbindung erfolgt ausschliesslich ueber die Umgebungsvariable `DECK_DATABASE_URL` (SQLAlchemy-URL, z. B. `postgresql+psycopg://…/controldeck`). Es werden keine Zugangsdaten im Repository abgelegt.

## Tabellen

| Tabelle | Zweck |
|---------|--------|
| `tasks` | Unveraenderlicher `task.v1`-Payload (JSONB), Laufstatus, `data_class`, Versuchszaehler, Freigabe (`requires_approval`, `approved_at`), optionale Owner-Deadline (`deadline_at`) |
| `results` | `result.v1`-Snapshots pro Versuch (eindeutig pro `task_id` + `attempt`) |
| `events` | Append-only Audit-Log (Uebergaenge, Metadaten in JSONB) |
| `leases` | Pull-Lease pro Task (`lease_id`, `expires_at`, `heartbeat_at`, optional `holder`) |
| `settings` | Typisierte lokale Runner-Steuerung: Pause, Not-Aus, Lane-Pause, Parallelitaet |
| `redaction_maps` | Lokale, taskgebundene Zuordnung stabiler Platzhalter zu exakten Originalwerten |
| `lane_status` | Persistierte Lane-Gesundheit mit sicherem Grund und DB-Zeitstempeln |

### `data_class`

Pflichtfeld gemaess ADR 0005, ohne Default in Schema oder Datenbank. Revision `20251103_0003` entfernt den frueheren DB-Default. Erlaubte Werte: `local_only`, `cloud_redacted`, `cloud_ok` (identisch zu `task.v1`).

### Task-Status

| Status | Bedeutung |
|--------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe (HTTP-API) |
| `queued` | Freigegeben, wartet auf Lease |
| `leased` | Runner haelt Lease, Start steht aus |
| `running` | Ausfuehrung aktiv |
| `review` | Automatische Abnahme bestanden oder mit `acceptance_failed` abgelehnt, wartet auf Owner-Entscheid |
| `done` | Erfolgreich abgeschlossen und gegebenenfalls vom Owner geprueft |
| `expired` | Explizite Owner-Deadline erreicht |
| `succeeded` | Historischer terminaler Status; bleibt fuer bestehende Zeilen erhalten |
| `failed` | Nicht behebbarer Abschluss |
| `blocked` | Schranke / Policy |
| `cancelled` | Abgebrochen |

Die erlaubten Uebergaenge sind in `app/db/state_machine.py` als reine Python-Logik definiert; jeder Uebergang erzeugt ein Audit-Event (`task.<trigger>`).

Wichtige Pfade:

- `queued` → `leased` → `running` → `review` → `done`
- Ohne Review-Pflicht und ohne `human`-Regel: `running` → `done`
- Owner-Ablehnung: `review` → `failed`; Owner-Abbruch aus `review` → `cancelled`
- Retry oder Lease-Ablauf: zurueck nach `queued` (`retry`, `lease_expired`), ohne Freigabe nach `awaiting_approval`, bei erschoepften Versuchen nach `failed`
- Eine abgelaufene Owner-Deadline fuehrt aus `awaiting_approval`, `queued`, `leased`, `running` oder `review` nach `expired`

## Routing-Laufzeitdaten

`tasks.plan` und `tasks.lane` enthalten ab Etappe 7 den versionierten Routing-Entscheid.
Der Auftrag bleibt unveraendert. Claims speichern Lane und UTC-Budgettag in `task.acquire`;
dieses append-only Protokoll zaehlt auch Versuche ohne Ergebnis. Konfigurierte Tagesgrenzen
werden mit einem transaktionsgebundenen Advisory-Lock vor den Task-Locks geprueft.
Rollback verwirft Claim und Verbrauch gemeinsam. Es gibt fuer Etappe 7 keine neue Tabelle
oder Migration. Details: [ROUTING.md](ROUTING.md).

## Retry-Laufzeitdaten (Revision 0011)

`tasks.retry_state` ist optionales versioniertes JSONB mit stabilem Policy-Cursor,
festgehaltener Lane, Rate-Limit-Zaehlern und taskweitem internem Retry-Zaehler.
`tasks.next_attempt_at` ist der optionale naechste Ausfuehrungszeitpunkt mit Zeitzone.
Beide Felder liegen ausserhalb des unveraenderlichen `task.v1`. Die Queue und der atomare
Claim vergleichen den Zeitpunkt mit `clock_timestamp()`; der Claim entfernt ihn.
Der partielle Index `ix_tasks_queued_next_attempt` auf Zeitpunkt und Erstellungszeit
gilt nur fuer `queued`. Der Downgrade verweigert vorhandene Werte in beiden Spalten.
Verhalten und Fehlerliste: [ETAPPEN/10-fehlerpfade.md](ETAPPEN/10-fehlerpfade.md).

## Queue (SKIP LOCKED)

Runner ziehen Arbeit mit `SELECT … FOR UPDATE SKIP LOCKED` auf dem aeltesten `queued`-Task (Index `status`, `created_at`). Die Funktion `dequeue_next_queued_task` nutzt zentral `transition_task` (Audit-Event, `attempt += 1`, `updated_at`) und ersetzt die Lease-Zeile re-acquire-sicher (ADR 0001, 0004). Abgelaufene Leases holt `reap_expired_leases` per `SKIP LOCKED` zurueck in die Queue.

Erreicht eine Owner-Deadline erst nach dem begrenzten Sweep das Zeitlimit, behandelt der Reaper die Fencing-Ablehnung pro Lease und materialisiert `expired`. So bleiben die anderen Ergebnisse des Batches erhalten; sonstige Fencing-Fehler werden weiterhin gemeldet.

Persistenz neuer Auftraege: `create_task_from_v1` validiert `TaskV1`, leitet `id` / `data_class` aus dem Payload ab und startet in `awaiting_approval` (Owner-Freigabe via `approve_task` / HTTP-Approve).

Ein voruebergehend unlesbarer Anhang verbraucht keinen Versuch und erzeugt keine Lease.
`task.gate_error` protokolliert den Fehler ohne Statuswechsel. System-/Lease-Polls speichern
identische Fehler pro Task, Code und Quelle hoechstens einmal in 60 Sekunden. Der Task-Lock
serialisiert diese Entscheidung; Zeitfenster und Event-Zeitstempel verwenden die aktuelle
DB-Uhr. Owner-Aktionen bleiben einzeln auditiert.

Worker-Uebergaenge aus `leased`/`running` erfordern die passende `lease_id` (Fencing). Retry respektiert `policy.max_attempts`; danach Dead-Letter via `failed`.

### Owner-Deadline und Review

`deadline_at` ist eine nullable Spalte mit Zeitzone und ohne Default. Nur ein expliziter Wert beim Owner-Create setzt sie; der unveraenderliche `task.v1`-Payload und `policy.timeout_s` liefern keine Deadline. Eine Lease kann ablaufen, ohne dass der Task `expired` wird.

Die Ablaufpruefung verwendet die Datenbankzeit. `expire_overdue_tasks` verarbeitet ueberfaellige nichtterminale Tasks in begrenzten Batches mit `FOR UPDATE SKIP LOCKED`; `expire_task_if_due` prueft einen einzelnen Task. Die Transition `expire` setzt `expired`, entfernt die Lease und schreibt ein Audit-Event. Zusaetzliche SQL-Praedikate verhindern Acquire oder Abschluss nach der Deadline, auch wenn ein Sweep noch aussteht. Terminale Tasks bleiben unveraendert.

Nach erfolgreicher Core-Acceptance steuert `policy.require_review` den Uebergang nach `review` oder `done`. Eine `human`-Regel erzwingt `review`. Die Owner-Entscheide `review_approve` und `review_reject` sind auditierte Uebergaenge nach `done` beziehungsweise `failed`; Ergebnis-Snapshots bleiben unveraendert.

## Migrationen

### Freigabe-Gate (Revision 0002 / 0004)

Revision `20251103_0002` fuehrt `awaiting_approval` ein. **Legacy-Bestand:**

- **`queued` ohne `approved_at`** → `awaiting_approval` (Owner-Freigabe noetig).
- **`leased` / `running` ohne `approved_at`** → `approved_at` bleibt **NULL** (keine Auto-Freigabe); bei Lease-Ablauf oder Retry leitet die Laufzeit nach `awaiting_approval`.

Laufzeit: `transition_task` leitet unapproved Requeues von `lease_expired`/`retry` nach `awaiting_approval` statt in ein blockiertes `queued`.

Revision `20251103_0004` korrigiert idempotent DBs, die noch die **urspruengliche** 0002-Massen-Freigabe (`requires_approval = false`, gesetztes `approved_at` auf allen Zeilen) angewendet haben — inklusive **`leased` / `running`**.

Revision `20251103_0005` entzieht DBs, die die alte Auto-Freigabe in 0002/0004 erhalten haben, `approved_at` wieder, wenn kein Audit-Event `task.approve` existiert.

Revision `20251103_0006` setzt bei nicht-terminalen Tasks mit `requires_approval = false`, `approved_at IS NULL` und ohne `task.approve`-Event `requires_approval = true` (idle `queued` → `awaiting_approval`). Laufzeit-Requeue und Dequeue verlangen ein gesetztes `approved_at`.

### Review und Deadline (Revision 0007)

Revision `20251103_0007` ergaenzt die Enum-Werte `review`, `done`, `expired` und `tasks.deadline_at`. Es gibt keinen Deadline-Backfill und keine automatische Umdeutung alter `succeeded`-Tasks.

Der partielle Index `ix_tasks_active_deadline` auf `(deadline_at, id)` enthaelt nur nichtterminale Tasks mit gesetzter Deadline. Die aktuelle Zeit wird beim Sweep geprueft und ist kein Bestandteil des Index-Praedikats. Der Downgrade entfernt den Index vor der Rueckumwandlung des Status-Enums.

Ein Downgrade wird verweigert, solange Tasks oder Audit-Events einen der drei neuen Status verwenden oder eine Owner-Deadline gespeichert ist. Die betreffenden Daten muessen vorher explizit migriert werden; Audit-Events werden nicht automatisch umgeschrieben oder geloescht. Nach erfolgreicher Pruefung werden die neuen Enum-Werte und die Deadline-Spalte entfernt.

### Lokale Redaction-Maps (Revision 0008)

Revision `20251103_0008` ergaenzt ausschliesslich `redaction_maps`; die eingefrorenen Task- und
Ergebnisformate bleiben unveraendert.

| Spalte | Bedeutung |
|--------|-----------|
| `id` | UUID als Primaerschluessel |
| `task_id` | Fremdschluessel auf `tasks.id`, `ON DELETE RESTRICT` |
| `placeholder` | Taskgebundener Platzhalter `[[CD_<task_id.hex>_<KIND>_<N>]]` |
| `kind` | `EMAIL`, `PHONE`, `IBAN`, `AHV`, `IP` oder `TERM` |
| `original` | Exakter Originalwert, lokal im Klartext |
| `original_sha256` | SHA-256 der UTF-8-Bytes des Originalwerts |
| `first_attempt` | Positiver Versuch, in dem die Zuordnung erstmals gespeichert wurde |
| `created_at` | Erstellungszeitpunkt mit Zeitzone |

Eindeutig sind `(task_id, placeholder)` und `(task_id, original_sha256)`. Der Hash ermoeglicht
den Vergleich auch langer Originalwerte ohne einen B-Tree-Index auf dem Rohtext. Die Datenbank
prueft den Hash gegen den Originalwert sowie Kind, positive Versuchszahl und Taskbindung des
Platzhalters. Ein Hashkonflikt mit einem anderen Originalwert wird vom Repository abgewiesen.
Der Index `ix_redaction_maps_task_id` unterstuetzt das taskgebundene Laden.

`redact_for_task` sperrt zuerst die Task-Zeile mit `FOR UPDATE`, laedt deren Map und ergaenzt
neue Zuordnungen atomar. Damit verwenden parallele Vorbereitungen und spaetere Versuche
dieselben Platzhalter. Die erste Klassifikation eines exakten Originalwerts bleibt erhalten.
Der Aufrufer kontrolliert den Commit; ein Rollback entfernt neu angelegte Zuordnungen.

Der Trigger `redaction_maps_immutable` verweigert jedes `UPDATE`. Ein Task mit Map kann wegen
des Fremdschluessels nicht geloescht werden. Aufbewahrung und ausdrueckliche Bereinigung folgen
in Etappe 16; automatische Loeschung ist hier nicht enthalten. Maps werden nicht ueber die
normalen Task-/Ergebnis-/Event-Serializer ausgegeben.

Der Downgrade nimmt einen exklusiven Tabellen-Lock und verweigert das Entfernen einer nicht
leeren Map-Tabelle. Originalwerte werden nicht still verworfen. Ein leerer Bestand laesst sich
auf Revision 0007 zurueckfuehren und erneut migrieren. Der fachliche Ablauf steht in
[REDAKTION.md](REDAKTION.md).

Alembic liegt im Projektroot (`alembic.ini`, `alembic/versions/`). Typischer Ablauf:

```bash
export DECK_DATABASE_URL='postgresql+psycopg://…/controldeck'
uv run alembic upgrade head
```

Downgrade (nur Dev/Test):

```bash
uv run alembic downgrade base
```

## Tests

- **Unit:** State-Machine und Transition-Helfer ohne Datenbank.
- **Integration:** nur gegen `DECK_TEST_DATABASE_URL`; der Datenbankname **muss auf `_test` enden** (Schutz vor destruktiven Laeufen auf Produktions-DBs). CI startet `postgres:17`, legt `controldeck_test` an, fuehrt `alembic upgrade` + `alembic check` aus.

Lokal ohne `DECK_TEST_DATABASE_URL` werden Integrationstests uebersprungen; `make test` bleibt gruen.

Alembic waehlt **nicht** still `DECK_TEST_DATABASE_URL`: fuer Tests `-x target=test` (siehe `tests/integration/conftest.py`). Sind Runtime- und Test-URL gleichzeitig gesetzt, bricht `alembic upgrade` ohne explizites Ziel ab.

## Betrieb (Hinweis)

Die produktive PostgreSQL-Instanz wird ausserhalb dieses Repos verwaltet. Agents legen keine DB-Rollen an und greifen nicht auf Server zu — nur Schema, Migrationen und Tests im Repository.

Alembic waehlt **nicht** still `DECK_TEST_DATABASE_URL`: fuer Tests `-x target=test` (siehe `tests/integration/conftest.py`). Sind Runtime- und Test-URL gleichzeitig gesetzt, bricht `alembic upgrade` ohne explizites Ziel ab.

## Integritaet

- CHECKs auf `task.v1` / `result.v1`-Felder in JSONB (inkl. NOT NULL auf Pflicht-Keys)
- Trigger `tasks_immutable_payload`: kein UPDATE von `payload`, `data_class`, `id`, `created_at`
- Trigger `events_append_only`: kein UPDATE/DELETE auf Events; FK `RESTRICT` (Tasks werden nicht geloescht)
- Spalten `cancel_requested`, `cancel_reason`; Events mit `actor`, `attempt`, `lease_id`
- Redaction-Maps: Taskbindung, eindeutige Originalhashes und Platzhalter, unveraenderliche Eintraege

## Runner-Steuerung (Revision 0009)

`settings` speichert Schluessel, JSONB-Wert und Aenderungszeit. Unbekannte Schluessel und
ungueltige Typen verhindern Claims. Der globale Admission-Lock serialisiert Claims gegen
Steuerungs-Aenderungen; Task-Locks folgen danach. Der Downgrade verweigert vorhandene
Einstellungen, damit Pause und Not-Aus nicht still verloren gehen.

`task.prepare` bindet Versandhash, Lane, Versuch, Lease und Artefaktpfad an den committed
Claim. Der Host prueft diese Metadaten vor Start und Ergebnisannahme. Details: [RUNNER.md](RUNNER.md).

## Lane-Gesundheit (Revision 0010)

`lane_status` speichert pro Lane den Status (`ok`, `unavailable`, `auth_required`, `paused`),
einen sicheren begrenzten Grundcode, `updated_at` und `last_success_at`. Der Upsert sperrt
die Zeile vor dem Lesen der DB-Uhr; parallele Beobachtungen koennen den Zeitstempel dadurch
nicht rueckwaerts setzen. Ein Fehler erhaelt den letzten positiven Zeitpunkt.

Ein `ok` mit Grund `simulation_ready` ist ausschliesslich eine erfolgreiche Fake-Pruefung,
keine Live-Bereitschaft. Eine erfolgreiche Live-Vorpruefung verwendet `live_ready`; ohne
verfuegbares Live-Profil bleibt der Zustand `unavailable`. `last_success_at` ist eine
Gesundheitsbeobachtung, kein Beleg
fuer einen abgeschlossenen Modelllauf. Der Downgrade verweigert eine nichtleere Tabelle.
Details: [LANES.md](LANES.md).

## UI-Sessions (Revision 0012)

`ui_sessions` speichert SHA-256-Hashes zufaelliger Session- und CSRF-Tokens, den
Authentifizierungszustand sowie Erstellungs-, Ablauf- und Widerrufszeit. Roh-Tokens,
Passwoerter und Chatverlaeufe werden nicht gespeichert. Ein Ablaufindex unterstuetzt die
Bereinigung. Der Passwort-Hash kommt aus lokaler Konfiguration und liegt nicht in dieser Tabelle.

`ui_login_state` ist eine durch CHECKs beschraenkte Singleton-Zeile fuer das globale
24-Stunden-Budget und die globale Login-Sperre des einen Owners. Ihre Zeilensperre
serialisiert alle Anmeldungen, sodass parallele Fehlversuche kein Limit umgehen.
Zeitentscheide verwenden die Datenbankuhr. Der Downgrade verweigert noch gueltige, nicht
widerrufene Owner-Sessions; vorher muessen diese widerrufen werden.

Migration `0014` verschiebt das 15-Minuten-Fenster in `ui_login_clients`: eine Zeile je
Client-Kennung mit SHA-256-Digest der Peer-Adresse (`client_hash`), `failures`,
`window_started_at` und `throttled_at`. Adressen selbst werden nicht gespeichert. Abgelaufene
Fenster werden beim Ausstellen neuer Login-Challenges bereinigt; `make unlock-login` leert die
Tabelle. Die Sperrreihenfolge bleibt Singleton, dann Session, dann Client-Zeile. Der Downgrade
verweigert, solange ein Client-Fenster noch aktiv gedrosselt ist.

Die Migration veraendert keine Task-/Result-Schemas und setzt keinen `data_class`-Default.
Details: [UI.md](UI.md).

## Wissenszustand (Etappe 14)

Migration `0013` ergaenzt lokale `task_context_snapshots` und `knowledge_exports`.
Snapshots binden den Auftrag an unveraenderliche UTF-8-Bytes, ausdrueckliche Datenklasse und
SHA-256. Updates sind durch einen DB-Trigger gesperrt; eine spaetere Retention darf alte
terminale Tasks ohne Lease beruecksichtigen. Exportbelege bleiben erhalten und erlauben die
idempotente Wiederaufnahme nach einem Fehler zwischen Host-DB und Wissens-DB. Ein Downgrade
mit vorhandenen Snapshots oder Exportbelegen wird abgewiesen.

Der vorgeschlagene Vertrag `deck_kb.v1` verwendet eine eigene Alembic-Kette und eine separate
`_test`-Datenbank mit pgvector. Er aendert keine bestehenden Wissens-Tabellen und bestaetigt
keine Kompatibilitaet einer produktiven Wissens-DB. Alle Datenklassen sind Pflichtfelder ohne
DB-Default. Einzelheiten: [WISSENS_DB.md](WISSENS_DB.md).

## Ergebnisherkunft (Revision 0018)

`results.execution_profile` hat einen CHECK auf `simulation`, `live`, `remote`, `unknown`.
Neue Zeilen ohne ausdrueckliche Herkunft erhalten `simulation`. Altzeilen erhalten
`unknown`, da ihr Profil nicht attestiert wurde. Das eingefrorene JSON-Schema bleibt gleich.

`lane_status.simulated_attempts` zaehlt alle angenommenen Versuche ohne Live-Nachweis,
also auch `remote` und `unknown`. Nur `live` veraendert `completed_attempts`, Fehlerserie,
Fehlerzeitpunkte und `last_attempt_success_at`. Die Ergebnisannahme und der Zaehler bleiben
in derselben gefencten Transaktion; ein abgelehntes oder zurueckgerolltes Ergebnis zaehlt nie.

Bisherige Zaehler aus Revision 0016 belegen keine Live-Herkunft. Die Migration uebertraegt
`completed_attempts` nach `simulated_attempts` und setzt die bisherigen Live-Zaehler und
Versuchszeitpunkte zurueck. Gesundheitsbeobachtungen, Ergebnisse und Events bleiben erhalten.
Health bezeichnet den neuen Zaehlerzeitraum mit `live_since_migration_0018`.
Ein Downgrade mit neuen Herkunftsbelegen oder nichtleerem Nicht-Live-Zaehler wird verweigert.
Nur eine leere Entwicklungs-/Testdatenbank ist fuer den vollstaendigen Roundtrip vorgesehen.
