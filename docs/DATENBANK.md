# Datenbank (Control Deck)

Etappe `03-db-schema` führt PostgreSQL als persistente Queue und Audit-Store ein. Die Verbindung erfolgt ausschliesslich über die Umgebungsvariable `DECK_DATABASE_URL` (SQLAlchemy-URL, z. B. `postgresql+psycopg://…/controldeck`). Es werden keine Zugangsdaten im Repository abgelegt.

## Tabellen

| Tabelle | Zweck |
|---------|--------|
| `tasks` | Unveränderlicher `task.v1`-Payload (JSONB), Laufstatus, `data_class`, Versuchszähler, Freigabe (`requires_approval`, `approved_at`), optionale Owner-Deadline (`deadline_at`) |
| `results` | `result.v1`-Snapshots pro Versuch (eindeutig pro `task_id` + `attempt`) |
| `events` | Append-only Audit-Log (Übergänge und ab `20261110_0064` taskfreie Gesprächs- und Gedächtniszeilen, Metadaten in JSONB) |
| `leases` | Pull-Lease pro Task (`lease_id`, `expires_at`, `heartbeat_at`, optional `holder`) |
| `settings` | Typisierte lokale Runner-Steuerung: Pause, Not-Aus, Lane-Pause, Parallelität |
| `redaction_maps` | Lokale, taskgebundene Zuordnung stabiler Platzhalter zu exakten Originalwerten |
| `lane_status` | Persistierte Lane-Gesundheit mit sicherem Grund und DB-Zeitstempeln |

### `data_class`

Pflichtfeld gemäss ADR 0005, ohne Default in Schema oder Datenbank. Revision `20251103_0003` entfernt den früheren DB-Default. Erlaubte Werte: `local_only`, `cloud_redacted`, `cloud_ok` (identisch zu `task.v1`).

### Task-Status

| Status | Bedeutung |
|--------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe (HTTP-API) |
| `queued` | Freigegeben, wartet auf Lease |
| `leased` | Runner hält Lease, Start steht aus |
| `running` | Ausführung aktiv |
| `review` | Automatische Abnahme bestanden oder mit `acceptance_failed` abgelehnt, wartet auf Owner-Entscheid |
| `done` | Erfolgreich abgeschlossen und gegebenenfalls vom Owner geprüft |
| `expired` | Explizite Owner-Deadline erreicht |
| `succeeded` | Historischer terminaler Status; bleibt für bestehende Zeilen erhalten |
| `failed` | Nicht behebbarer Abschluss |
| `blocked` | Schranke / Policy |
| `cancelled` | Abgebrochen |

Die erlaubten Übergänge sind in `app/db/state_machine.py` als reine Python-Logik definiert; jeder Übergang erzeugt ein Audit-Event (`task.<trigger>`).

Wichtige Pfade:

- `queued` → `leased` → `running` → `review` → `done`
- Ohne Review-Pflicht und ohne `human`-Regel: `running` → `done`
- Owner-Ablehnung: `review` → `failed`; Owner-Abbruch aus `review` → `cancelled`
- Retry oder Lease-Ablauf: zurück nach `queued` (`retry`, `lease_expired`), ohne Freigabe nach `awaiting_approval`, bei erschöpften Versuchen nach `failed`. Fehlt beim Lease-Ablauf ein Ergebnis, schreibt der Reaper genau eines mit `worker_unavailable`. Ein schon gespeichertes `timeout` wird nicht umetikettiert.
- `channel_receipts` speichert je Auftrag höchstens eine Annahme in die Warteschlange. Sie ist nicht `task.start`. Der Transport `missing` wird nie als zugestellt gespeichert. Die Frist ist 60 Sekunden ab `accepted_at` und kann mit einer übergebenen Uhr geprüft werden.
- Eine abgelaufene Owner-Deadline führt aus `awaiting_approval`, `queued`, `leased`, `running` oder `review` nach `expired`

## Routing-Laufzeitdaten

`tasks.plan` und `tasks.lane` enthalten ab Etappe 7 den versionierten Routing-Entscheid.
Der Auftrag bleibt unverändert. Claims speichern Lane und UTC-Budgettag in `task.acquire`;
dieses append-only Protokoll zählt auch Versuche ohne Ergebnis. Konfigurierte Tagesgrenzen
werden mit einem transaktionsgebundenen Advisory-Lock vor den Task-Locks geprüft.
Rollback verwirft Claim und Verbrauch gemeinsam. Es gibt für Etappe 7 keine neue Tabelle
oder Migration. Details: [ROUTING.md](ROUTING.md).

## Retry-Laufzeitdaten (Revision 0011)

`tasks.retry_state` ist optionales versioniertes JSONB mit stabilem Policy-Cursor,
festgehaltener Lane, Rate-Limit-Zählern und taskweitem internem Retry-Zähler.
`tasks.next_attempt_at` ist der optionale nächste Ausführungszeitpunkt mit Zeitzone.
Beide Felder liegen ausserhalb des unveränderlichen `task.v1`. Die Queue und der atomare
Claim vergleichen den Zeitpunkt mit `clock_timestamp()`; der Claim entfernt ihn.
Der partielle Index `ix_tasks_queued_next_attempt` auf Zeitpunkt und Erstellungszeit
gilt nur für `queued`. Der Downgrade verweigert vorhandene Werte in beiden Spalten.
Verhalten und Fehlerliste: [ETAPPEN/10-fehlerpfade.md](ETAPPEN/10-fehlerpfade.md).

## Queue (SKIP LOCKED)

Runner ziehen Arbeit mit `SELECT … FOR UPDATE SKIP LOCKED` auf dem ältesten `queued`-Task (Index `status`, `created_at`). Die Funktion `dequeue_next_queued_task` nutzt zentral `transition_task` (Audit-Event, `attempt += 1`, `updated_at`) und ersetzt die Lease-Zeile re-acquire-sicher (ADR 0001, 0004). Abgelaufene Leases holt `reap_expired_leases` per `SKIP LOCKED` zurück in die Queue.

Erreicht eine Owner-Deadline erst nach dem begrenzten Sweep das Zeitlimit, behandelt der Reaper die Fencing-Ablehnung pro Lease und materialisiert `expired`. So bleiben die anderen Ergebnisse des Batches erhalten; sonstige Fencing-Fehler werden weiterhin gemeldet.

Persistenz neuer Aufträge: `create_task_from_v1` validiert `TaskV1`, leitet `id` / `data_class` aus dem Payload ab und startet in `awaiting_approval` (Owner-Freigabe via `approve_task` / HTTP-Approve).

Ein vorübergehend unlesbarer Anhang verbraucht keinen Versuch und erzeugt keine Lease.
`task.gate_error` protokolliert den Fehler ohne Statuswechsel. System-/Lease-Polls speichern
identische Fehler pro Task, Code und Quelle höchstens einmal in 60 Sekunden. Der Task-Lock
serialisiert diese Entscheidung; Zeitfenster und Event-Zeitstempel verwenden die aktuelle
DB-Uhr. Owner-Aktionen bleiben einzeln auditiert.

Worker-Übergänge aus `leased`/`running` erfordern die passende `lease_id` (Fencing). Retry respektiert `policy.max_attempts`; danach Dead-Letter via `failed`.

### Owner-Deadline und Review

`deadline_at` ist eine nullable Spalte mit Zeitzone und ohne Default. Nur ein expliziter Wert beim Owner-Create setzt sie; der unveränderliche `task.v1`-Payload und `policy.timeout_s` liefern keine Deadline. Eine Lease kann ablaufen, ohne dass der Task `expired` wird.

Die Ablaufprüfung verwendet die Datenbankzeit. `expire_overdue_tasks` verarbeitet überfällige nichtterminale Tasks in begrenzten Batches mit `FOR UPDATE SKIP LOCKED`; `expire_task_if_due` prüft einen einzelnen Task. Die Transition `expire` setzt `expired`, entfernt die Lease und schreibt ein Audit-Event. Zusätzliche SQL-Prädikate verhindern Acquire oder Abschluss nach der Deadline, auch wenn ein Sweep noch aussteht. Terminale Tasks bleiben unverändert.

Nach erfolgreicher Core-Acceptance steuert `policy.require_review` den Übergang nach `review` oder `done`. Eine `human`-Regel erzwingt `review`. Die Owner-Entscheide `review_approve` und `review_reject` sind auditierte Übergänge nach `done` beziehungsweise `failed`; Ergebnis-Snapshots bleiben unverändert.

## Migrationen

### Freigabe-Gate (Revision 0002 / 0004)

Revision `20251103_0002` führt `awaiting_approval` ein. **Legacy-Bestand:**

- **`queued` ohne `approved_at`** → `awaiting_approval` (Owner-Freigabe nötig).
- **`leased` / `running` ohne `approved_at`** → `approved_at` bleibt **NULL** (keine Auto-Freigabe); bei Lease-Ablauf oder Retry leitet die Laufzeit nach `awaiting_approval`.

Laufzeit: `transition_task` leitet unapproved Requeues von `lease_expired`/`retry` nach `awaiting_approval` statt in ein blockiertes `queued`.

Revision `20251103_0004` korrigiert idempotent DBs, die noch die **ursprüngliche** 0002-Massen-Freigabe (`requires_approval = false`, gesetztes `approved_at` auf allen Zeilen) angewendet haben — inklusive **`leased` / `running`**.

Revision `20251103_0005` entzieht DBs, die die alte Auto-Freigabe in 0002/0004 erhalten haben, `approved_at` wieder, wenn kein Audit-Event `task.approve` existiert.

Revision `20251103_0006` setzt bei nicht-terminalen Tasks mit `requires_approval = false`, `approved_at IS NULL` und ohne `task.approve`-Event `requires_approval = true` (idle `queued` → `awaiting_approval`). Laufzeit-Requeue und Dequeue verlangen ein gesetztes `approved_at`.

### Review und Deadline (Revision 0007)

Revision `20251103_0007` ergänzt die Enum-Werte `review`, `done`, `expired` und `tasks.deadline_at`. Es gibt keinen Deadline-Backfill und keine automatische Umdeutung alter `succeeded`-Tasks.

Der partielle Index `ix_tasks_active_deadline` auf `(deadline_at, id)` enthält nur nichtterminale Tasks mit gesetzter Deadline. Die aktuelle Zeit wird beim Sweep geprüft und ist kein Bestandteil des Index-Prädikats. Der Downgrade entfernt den Index vor der Rückumwandlung des Status-Enums.

Ein Downgrade wird verweigert, solange Tasks oder Audit-Events einen der drei neuen Status verwenden oder eine Owner-Deadline gespeichert ist. Die betreffenden Daten müssen vorher explizit migriert werden; Audit-Events werden nicht automatisch umgeschrieben oder gelöscht. Nach erfolgreicher Prüfung werden die neuen Enum-Werte und die Deadline-Spalte entfernt.

### Lokale Redaction-Maps (Revision 0008)

Revision `20251103_0008` ergänzt ausschliesslich `redaction_maps`; die eingefrorenen Task- und
Ergebnisformate bleiben unverändert.

| Spalte | Bedeutung |
|--------|-----------|
| `id` | UUID als Primärschlüssel |
| `task_id` | Fremdschlüssel auf `tasks.id`, `ON DELETE RESTRICT` |
| `placeholder` | Taskgebundener Platzhalter `[[CD_<task_id.hex>_<KIND>_<N>]]` |
| `kind` | `EMAIL`, `PHONE`, `IBAN`, `AHV`, `IP` oder `TERM` |
| `original` | Exakter Originalwert, lokal im Klartext |
| `original_sha256` | SHA-256 der UTF-8-Bytes des Originalwerts |
| `first_attempt` | Positiver Versuch, in dem die Zuordnung erstmals gespeichert wurde |
| `created_at` | Erstellungszeitpunkt mit Zeitzone |

Eindeutig sind `(task_id, placeholder)` und `(task_id, original_sha256)`. Der Hash ermöglicht
den Vergleich auch langer Originalwerte ohne einen B-Tree-Index auf dem Rohtext. Die Datenbank
prüft den Hash gegen den Originalwert sowie Kind, positive Versuchszahl und Taskbindung des
Platzhalters. Ein Hashkonflikt mit einem anderen Originalwert wird vom Repository abgewiesen.
Der Index `ix_redaction_maps_task_id` unterstützt das taskgebundene Laden.

`redact_for_task` sperrt zuerst die Task-Zeile mit `FOR UPDATE`, lädt deren Map und ergänzt
neue Zuordnungen atomar. Damit verwenden parallele Vorbereitungen und spätere Versuche
dieselben Platzhalter. Die erste Klassifikation eines exakten Originalwerts bleibt erhalten.
Der Aufrufer kontrolliert den Commit; ein Rollback entfernt neu angelegte Zuordnungen.

Der Trigger `redaction_maps_immutable` verweigert jedes `UPDATE`. Ein Task mit Map kann wegen
des Fremdschlüssels nicht gelöscht werden. Aufbewahrung und ausdrückliche Bereinigung folgen
in Etappe 16; automatische Löschung ist hier nicht enthalten. Maps werden nicht über die
normalen Task-/Ergebnis-/Event-Serializer ausgegeben.

Der Downgrade nimmt einen exklusiven Tabellen-Lock und verweigert das Entfernen einer nicht
leeren Map-Tabelle. Originalwerte werden nicht still verworfen. Ein leerer Bestand lässt sich
auf Revision 0007 zurückführen und erneut migrieren. Der fachliche Ablauf steht in
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

Die Hauptkette hat genau einen Kopf, `20261110_0064`. `20261108_0062` verbindet
`20261108_0020` und `20261108_0061`, beide nach `20261104_0018`. `20261108_0063`
haengt nur die Wake-Tabelle `routine_outbox` an. `20261110_0064` haengt an
`20261108_0063`. Ein Upgrade von `20261104_0018`, von einer Datenbank auf
`20261108_0020` und eine leere Datenbank enden auf `20261110_0064`. `alembic
downgrade -1` ist an der Verbindung `20261108_0062` mehrdeutig und läuft nicht.
`alembic downgrade 20261108_0020` entfernt nur die Verbindung und laesst beide
Eltern samt ihren Tabellen stehen. `alembic downgrade -1` von `20261108_0063`
entfernt nur `routine_outbox` und laesst `20261108_0062` stehen. `alembic
downgrade -1` vom Kopf ist der Downgrade von `20261110_0064` und verweigert,
solange Gesprächs- oder taskfreie Audit-Zeilen existieren (siehe unten). Die
Wissens-Kette `alembic_kb` mit `kb_0001` bleibt eine eigene Script-Location und
eine eigene Versionstabelle.

## Tests

- **Unit:** State-Machine und Transition-Helfer ohne Datenbank.
- **Integration:** nur gegen `DECK_TEST_DATABASE_URL`; der Datenbankname **muss auf `_test` enden** (Schutz vor destruktiven Läufen auf Produktions-DBs). CI startet `postgres:17`, legt `controldeck_test` an, führt `alembic upgrade` + `alembic check` aus.

Lokal ohne `DECK_TEST_DATABASE_URL` werden Integrationstests übersprungen; `make test` bleibt grün.

Alembic wählt **nicht** still `DECK_TEST_DATABASE_URL`: für Tests `-x target=test` (siehe `tests/integration/conftest.py`). Sind Runtime- und Test-URL gleichzeitig gesetzt, bricht `alembic upgrade` ohne explizites Ziel ab.

## Betrieb (Hinweis)

Die produktive PostgreSQL-Instanz wird ausserhalb dieses Repos verwaltet. Agents legen keine DB-Rollen an und greifen nicht auf Server zu — nur Schema, Migrationen und Tests im Repository.

Alembic wählt **nicht** still `DECK_TEST_DATABASE_URL`: für Tests `-x target=test` (siehe `tests/integration/conftest.py`). Sind Runtime- und Test-URL gleichzeitig gesetzt, bricht `alembic upgrade` ohne explizites Ziel ab.

## Integrität

- CHECKs auf `task.v1` / `result.v1`-Felder in JSONB (inkl. NOT NULL auf Pflicht-Keys)
- Trigger `tasks_immutable_payload`: kein UPDATE von `payload`, `data_class`, `id`, `created_at`
- Trigger `events_append_only`: kein UPDATE/DELETE auf Events; FK `RESTRICT` (Tasks werden nicht gelöscht). Ab `20261110_0064` darf `task_id` leer sein; `ck_events_task_or_conversation` trennt Task-Zeilen von taskfreien Zeilen
- Spalten `cancel_requested`, `cancel_reason`; Events mit `actor`, `attempt`, `lease_id`
- Redaction-Maps: Taskbindung, eindeutige Originalhashes und Platzhalter, unveränderliche Einträge

## Runner-Steuerung (Revision 0009)

`settings` speichert Schlüssel, JSONB-Wert und Änderungszeit. Unbekannte Schlüssel und
ungültige Typen verhindern Claims. Der globale Admission-Lock serialisiert Claims gegen
Steuerungs-Änderungen; Task-Locks folgen danach. Der Downgrade verweigert vorhandene
Einstellungen, damit Pause und Not-Aus nicht still verloren gehen.

`task.prepare` bindet Versandhash, Lane, Versuch, Lease und Artefaktpfad an den committed
Claim. Der Host prüft diese Metadaten vor Start und Ergebnisannahme. Details: [RUNNER.md](RUNNER.md).

## Lane-Gesundheit (Revision 0010)

`lane_status` speichert pro Lane den Status (`ok`, `unavailable`, `auth_required`, `paused`),
einen sicheren begrenzten Grundcode, `updated_at` und `last_success_at`. Der Upsert sperrt
die Zeile vor dem Lesen der DB-Uhr; parallele Beobachtungen können den Zeitstempel dadurch
nicht rückwärts setzen. Ein Fehler erhält den letzten positiven Zeitpunkt.

Ein `ok` mit Grund `simulation_ready` ist ausschliesslich eine erfolgreiche Fake-Prüfung,
keine Live-Bereitschaft. Eine erfolgreiche Live-Vorprüfung verwendet `live_ready`; ohne
verfügbares Live-Profil bleibt der Zustand `unavailable`. `last_success_at` ist eine
Gesundheitsbeobachtung, kein Beleg
für einen abgeschlossenen Modelllauf. Der Downgrade verweigert eine nichtleere Tabelle.
Details: [LANES.md](LANES.md).

## UI-Sessions (Revision 0012)

`ui_sessions` speichert SHA-256-Hashes zufälliger Session- und CSRF-Tokens, den
Authentifizierungszustand sowie Erstellungs-, Ablauf- und Widerrufszeit. Roh-Tokens,
Passwörter und Chatverläufe werden nicht gespeichert. Ein Ablaufindex unterstützt die
Bereinigung. Der Passwort-Hash kommt aus lokaler Konfiguration und liegt nicht in dieser Tabelle.

`ui_login_state` ist eine durch CHECKs beschränkte Singleton-Zeile für das globale
24-Stunden-Budget und die globale Login-Sperre des einen Owners. Ihre Zeilensperre
serialisiert alle Anmeldungen, sodass parallele Fehlversuche kein Limit umgehen.
Zeitentscheide verwenden die Datenbankuhr. Der Downgrade verweigert noch gültige, nicht
widerrufene Owner-Sessions; vorher müssen diese widerrufen werden.

Migration `0014` verschiebt das 15-Minuten-Fenster in `ui_login_clients`: eine Zeile je
Client-Kennung mit SHA-256-Digest der Peer-Adresse (`client_hash`), `failures`,
`window_started_at` und `throttled_at`. Adressen selbst werden nicht gespeichert. Abgelaufene
Fenster werden beim Ausstellen neuer Login-Challenges bereinigt; `make unlock-login` leert die
Tabelle. Die Sperrreihenfolge bleibt Singleton, dann Session, dann Client-Zeile. Der Downgrade
verweigert, solange ein Client-Fenster noch aktiv gedrosselt ist.

Die Migration verändert keine Task-/Result-Schemas und setzt keinen `data_class`-Default.
Details: [UI.md](UI.md).

## Wissenszustand (Etappe 14)

Migration `0013` ergänzt lokale `task_context_snapshots` und `knowledge_exports`.
Snapshots binden den Auftrag an unveränderliche UTF-8-Bytes, ausdrückliche Datenklasse und
SHA-256. Updates sind durch einen DB-Trigger gesperrt; eine spätere Retention darf alte
terminale Tasks ohne Lease berücksichtigen. Exportbelege bleiben erhalten und erlauben die
idempotente Wiederaufnahme nach einem Fehler zwischen Host-DB und Wissens-DB. Ein Downgrade
mit vorhandenen Snapshots oder Exportbelegen wird abgewiesen.

Der vorgeschlagene Vertrag `deck_kb.v1` verwendet eine eigene Alembic-Kette und eine separate
`_test`-Datenbank mit pgvector. Er ändert keine bestehenden Wissens-Tabellen und bestätigt
keine Kompatibilität einer produktiven Wissens-DB. Alle Datenklassen sind Pflichtfelder ohne
DB-Default. Einzelheiten: [WISSENS_DB.md](WISSENS_DB.md).

## Ergebnisherkunft (Revision 0018)

`results.execution_profile` hat einen CHECK auf `simulation`, `live`, `remote`, `unknown`.
Neue Zeilen ohne ausdrückliche Herkunft erhalten `simulation`. Altzeilen erhalten
`unknown`, da ihr Profil nicht attestiert wurde. Das eingefrorene JSON-Schema bleibt gleich.

`lane_status.simulated_attempts` zählt ausschliesslich `simulation`; `remote_attempts`
und `unknown_attempts` halten Remote- und unbekannte Herkunft getrennt.
`completed_attempts`, `last_attempt_at` und `last_attempt_success_at` bleiben Live-Nachweise.
Fehler und blockierte Ergebnisse jeder Herkunft erhöhen die Fehlerserie und aktualisieren
Fehlercode/-zeit. Nur ein angenommener erfolgreicher Live-Versuch setzt die Serie zurück.
Ergebnisannahme und Zähler bleiben in derselben gefencten Transaktion; abgelehnte,
doppelte oder zurückgerollte Ergebnisse zählen nie.

Alte Zähler aus Revision 0016 belegen keine Live-Herkunft. Migration 0018 übernimmt
`completed_attempts` nach `unknown_attempts`, setzt den Live-Abschlusszähler auf null
und leert die beiden Live-Versuchszeitpunkte. Die Fehlerhistorie bleibt erhalten;
Altdaten werden nicht zu Simulation umklassifiziert. Gesundheitsbeobachtungen,
Ergebnisse und Events bleiben erhalten. Health bezeichnet den Live-Zeitraum mit
`live_since_migration_0018`; die Fehlerhistorie umfasst alle Herkünfte.

`probe_generation` wird vor jeder externen Probe unter Row-Lock erhöht und vor dem I/O
committet. `probe_pending` verhindert zwischenzeitliche Live-Bereitschaft. Nur das Ergebnis
der aktuellen Generation darf die Beobachtung ersetzen; Pause und andere neue Beobachtungen
invalidieren ältere Proben ebenfalls. `updated_at` bleibt beim Abschluss der Probe deren
Startzeit. Ein Probenabschluss ohne vorhandene Zeile wird abgewiesen und legt keine
Beobachtung an. `live_available` verlangt zusätzlich `ok`, `live_ready`, eine aktivierte,
nicht pausierte Lane und ein Alter ab null bis ausschliesslich 30 Sekunden.

Der Downgrade hält Results und Lane-Status exklusiv gesperrt. Er verweigert nicht-`unknown`
Result-Herkunft, jeden Herkunfts-/Live-Zähler, Probe-Generation und erhaltene Fehler- oder
Live-Versuchszeitpunkte. So kann ein späteres Upgrade keine bestehenden Nachweise neu
einordnen. Ein evidenzfreier Roundtrip über 0017 erhält die Login-Kapazitätsspalten.

## Kanäle und Gedächtnis (Revision 20261110_0064)

Revision `20261110_0064` hängt an `20261108_0063`. Sie legt die Ablage für
Kanal-Nachrichten, Themen, Freigaben und Fakten an und macht `events.task_id`
sowie `events.to_status` nullable. `task.v1` und `result.v1` bleiben
unverändert. Verhalten: [KANAELE.md](KANAELE.md). Entscheide:
[ADR 0010](DECISIONS/0010-kanaele-und-gedaechtnis.md).

Fremdschlüssel dieser Revision stehen auf `ON DELETE RESTRICT`. Ein Downgrade
löscht keine Zeilen.

### `assistant_channel_accounts`

Allowlist, Primärschlüssel `(channel, account_label)`.

| Spalte | Bedeutung |
|--------|-----------|
| `channel` | `deck_ui`, `slack`, `n8n`, `grok`, `claude`, `claude_code` (`ck_assistant_channel_accounts_channel`) |
| `account_label` | `^[a-z][a-z0-9_]{0,31}$` (`ck_assistant_channel_accounts_label`) |
| `active` | `false` lehnt jeden Turn ab |
| `allowed_data_classes` | nicht leeres Text-Array, Teilmenge von `local_only`, `cloud_redacted`, `cloud_ok` (`ck_assistant_channel_accounts_classes`) |
| `created_at` | `clock_timestamp()` |

`ck_assistant_channel_accounts_cloud_ceiling`: bei `slack`, `n8n`, `grok` und
`claude` kommt `local_only` im Array nicht vor.
`ck_assistant_channel_accounts_claude_code_local`: `claude_code` ist exakt
`ARRAY['local_only']`. Die Migration legt nur `('deck_ui', 'owner', true)` an,
mit allen drei Klassen.

### `memory_approvals`

Append-only. Primärschlüssel `id` (UUID). Eindeutig `(kind, target_id)`
(`uq_memory_approvals_target`). `target_id` hat keinen Fremdschlüssel.

| Spalte | Bedeutung |
|--------|-----------|
| `kind` | `message`, `fact`, `thread_topic`, `thread_link` |
| `decision` | `approved` oder `rejected` |
| `shown_sha256` | 64 Hexzeichen der versionierten UTF-8-Nutzlast, nicht der Text-Hash |
| `approver_ref` | 64 Hexzeichen. sha256 über das Präfix `deck-memory-approver`, ein NUL-Byte und den Session-Digest. Nicht der Digest, kein Nachschlageschlüssel, kein Fremdschlüssel (`ck_memory_approvals_approver_ref`) |
| `created_at` | `clock_timestamp()` |

Die Freigabe prüft das rohe UI-Cookie über `get_ui_session`: Owner, kein
Widerruf, Ablauf, Passwort-Fingerabdruck, Leerlaufzeit, Zeile gesperrt.
Trigger `trg_memory_approvals_append_only` ruft `memory_approvals_append_only()`
und verweigert jedes `UPDATE` und `DELETE` mit `memory_approvals is append-only`.

### `assistant_threads`

| Spalte | Bedeutung |
|--------|-----------|
| `id` | UUID, Primärschlüssel |
| `title` | 1 bis 200 Zeichen (`ck_assistant_threads_title`) |
| `title_origin` | `owner` oder `assistant_proposed` |
| `data_class` | `local_only`, `cloud_redacted`, `cloud_ok` |
| `status` | `proposed`, `active`, `rejected` |
| `approval_id` | FK auf `memory_approvals.id`. Bei `proposed` leer, sonst gesetzt (`ck_assistant_threads_decision`) |
| `created_at` | `clock_timestamp()` |

### `assistant_thread_links`

FK auf `assistant_threads`, auf `memory_approvals` und auf
`(channel, account)` in `assistant_channel_accounts`.

`state` ist `proposed`, `active` oder `rejected`, mit derselben Kopplung an
`approval_id` wie das Thema (`ck_assistant_thread_links_decision`).
`external_thread_id` hat 1 bis 200 Zeichen und kein `://`
(`ck_assistant_thread_links_external_scheme`).
Partieller Unique-Index `uq_assistant_thread_links_external` auf
`(channel, account, external_thread_id)` WHERE `state <> 'rejected'`.
Abgelehnte Zeilen bleiben. Index `ix_assistant_thread_links_thread_id`.

### `assistant_messages`

Eine Zeile pro Nachricht. `seq` ist `BIGINT GENERATED ALWAYS AS IDENTITY` und
eindeutig (`uq_assistant_messages_seq`).

| Spalte | Bedeutung |
|--------|-----------|
| `thread_id` | nullable, FK auf `assistant_threads` |
| `external_thread_id` | varchar(200), NOT NULL, kein `://` |
| `external_message_id` | varchar(200), nullable, kein `://`. Deck-Antworten tragen das Präfix `deck-reply:`; eingehende Turns dürfen es nicht setzen |
| `role` | `owner` oder `assistant` |
| `text` | 1 bis 16000 Zeichen |
| `text_sha256` | muss `encode(sha256(convert_to(text, 'UTF8')), 'hex')` sein |
| `data_class` | die drei Klassen |
| `state` | `quarantined`, `approved`, `rejected` |
| `reply_for_message_id` | nullable, FK auf dieselbe Tabelle |
| `in_reply_to_message_id` | nullable, FK auf dieselbe Tabelle |
| `received_at` | `clock_timestamp()` |

Herkunft, NOT NULL ausser `source_ref_sha256` und `approval_id`: `source_date`,
`source_channel`, `source_account`, `source_ref` (1 bis 300 Zeichen, kein
`://`), `method` (`deck_ui`, `live_push`, `import_file`, `owner_typed`),
`evidence_status` (`bewiesen`, `unbewiesen`, `abgelehnt`). `bewiesen` nur mit
`approval_id` (`ck_assistant_messages_proven_needs_approval`). Dieselben
Herkunfts-Checks gelten für `memory_facts` mit dem Präfix `memory_facts`.

`ck_assistant_messages_decision`:

- `quarantined`: `approval_id` leer und `evidence_status = unbewiesen`
- `approved`: `approval_id` gesetzt, Nachweis `bewiesen` oder `unbewiesen`
- `rejected`: `approval_id` gesetzt und `evidence_status = abgelehnt`

`ck_assistant_messages_reply_channel`: gesetztes `reply_for_message_id` nur bei
Rolle `assistant` und Kanal `deck_ui` oder `slack`.
`ck_assistant_messages_reply_not_self`: nicht die eigene Id.
`uq_assistant_messages_reply_for`: höchstens eine Zeile pro gesetzter
Ziel-Id. Mehrere `NULL` kollidieren darauf nicht.

Dedup: `uq_assistant_messages_external_id` auf
`(source_channel, source_account, external_message_id)`. Mehrere `NULL` in
`external_message_id` kollidieren darauf nicht. Partieller
Unique-Index `uq_assistant_messages_fallback` auf
`(source_channel, source_account, external_thread_id, role, text_sha256, source_date)`
WHERE `external_message_id IS NULL`. Weitere Indizes:
`ix_assistant_messages_thread_seq` auf `(thread_id, seq)`,
`ix_assistant_messages_quarantine` auf `seq` WHERE `state = 'quarantined'`.
`ck_assistant_messages_external_scheme` verbietet `://` in beiden
Aussen-Kennungen. Der partielle Fallback-Index behält `source_date`: ohne
Nachrichten-ID ist derselbe Text zu einer späteren Quellzeit ein eigener
Quarantäne-Turn. Die Quelluhr ordnet nicht.

### Inhaltssperren

`memory_content_immutable()` hängt als `BEFORE UPDATE OR DELETE FOR EACH ROW`
an `assistant_messages`, `memory_facts`, `assistant_threads` und
`assistant_thread_links` (`trg_<tabelle>_content`). `DELETE` wirft
`memory_content_immutable`. `UPDATE` wirft denselben Fehler, wenn eine
Inhaltsspalte sich ändert:

- Nachrichten: `text`, `text_sha256`, `data_class`, `role`, Aussen-Kennungen,
  `source_date`, Kanal, Konto, `source_ref`, `source_ref_sha256`, `method`
- Fakten: `original_sentence`, `subject`, `predicate`, `object`, `data_class`,
  `source_message_id` und dieselben Herkunftsspalten
- Themen: `data_class` und `title` (der Titel steckt in den gezeigten Bytes)
- Verknüpfungen: `channel`, `account`, `external_thread_id`

Erlaubt bleibt nur der eine Entscheid aus dem offenen Zustand (`quarantined`
bzw. `proposed`): `state`/`status`, `evidence_status`, `approval_id`, bei
Verknüpfungen `thread_id`, bei Fakten zusätzlich `valid_from`, `valid_to` und
`superseded_by`. Danach sind diese Spalten eingefroren (siehe Nachtrag unten);
bei Nachrichten wechselt `thread_id` nur von leer auf ein Thema. `TRUNCATE` feuert diese
Zeilentrigger nicht. `memory_approvals` behält den eigenen Append-only-Trigger.

### `memory_facts`

`original_sentence` 1 bis 4000 Zeichen. `subject` und `predicate` varchar(200),
`object` varchar(1000), jedes mindestens ein Zeichen (`ck_memory_facts_triple`).
`source_message_id` FK auf `assistant_messages`. `data_class` wie oben.
`valid_from` und `valid_to` nullable; `valid_to` leer oder nicht vor
`valid_from` (`ck_memory_facts_validity`). `superseded_by` FK auf dieselbe
Tabelle, nicht die eigene Id (`ck_memory_facts_not_self`). `status`
`proposed`, `approved` oder `rejected` mit derselben Nachweis-Kopplung wie
Nachrichten (`ck_memory_facts_decision`). `created_at` ist `clock_timestamp()`.

Indizes: `ix_memory_facts_source_message`, `ix_memory_facts_approved` auf
`(data_class, created_at)` WHERE `status = 'approved' AND superseded_by IS NULL`.

### `events`

`events.task_id` (UUID, bestehender FK auf `tasks.id`) und `events.to_status`
(`task_state`) werden nullable. Der Trigger `trg_events_append_only` bleibt.

CHECK `ck_events_task_or_conversation`:

```text
(task_id IS NOT NULL AND to_status IS NOT NULL)
OR (task_id IS NULL AND to_status IS NULL AND from_status IS NULL
    AND (event_type LIKE 'assistant.%' OR event_type LIKE 'memory.%'))
```

Eine Task-Zeile braucht `task_id` und `to_status`; `from_status` bleibt dort
nullable. Eine taskfreie Zeile hat leeres `task_id`, leeres `to_status`,
leeres `from_status` und einen Typ `assistant.*` oder `memory.*`. Partieller
Index `ix_events_conversation_created_at` auf `created_at` WHERE
`task_id IS NULL`. Die Audit-Auswahl in `app/audit/repository.py` filtert
`task_id IS NOT NULL`.

### Downgrade

Der Migrationskopf nennt Test- und Entwicklungsdatenbanken. Der Code prüft den
Datenbanknamen nicht. Er setzt `lock_timeout` auf 10 s (begrenzt ein
einseitiges Warten; einen Zyklus löst Postgres über die Deadlock-Erkennung,
dann rollt der Downgrade zurück) und sperrt mit
`LOCK TABLE ... IN ACCESS EXCLUSIVE MODE` in dieser Reihenfolge: `assistant_channel_accounts`, `assistant_thread_links`,
`assistant_threads`, `assistant_messages`, `memory_facts`, `memory_approvals`
und zuletzt `events`. Danach zählt er Zeilen in `events` mit `task_id IS NULL`
und bricht ab, wenn welche existieren:

`events_taskless_rows: conversation audit rows exist with task_id NULL; refusing to restore NOT NULL. Rows are never deleted by this migration.`

Dann zählt er jede Inhaltstabelle. Die erste nicht leere Tabelle bricht ab mit
`conversation_rows: {tabelle} is not empty; refusing to drop owner memory.` Es wird nichts gelöscht. Nur wenn beide Prüfungen leer sind,
entfernt er die Inhaltstrigger und `memory_content_immutable()`, dann
`ix_events_conversation_created_at` und den CHECK, setzt `to_status` und
`task_id` wieder auf NOT NULL und droppt die neuen Tabellen, den
Append-only-Trigger und die Funktion `memory_approvals_append_only`.

### Nachtrag `20261110_0064` (Review-Runde 3)

- Der Trigger `memory_content_immutable` friert zusaetzlich Entscheid-Spalten ein:
  Nachrichten (`state`, `evidence_status`, `approval_id` nach `quarantined`,
  `thread_id` nur von leer auf gesetzt), Fakten (`status`, `evidence_status`,
  `approval_id`, `valid_from`, `valid_to`, `superseded_by` nach `proposed`), Themen und Verknuepfungen (`status`/`state`,
  `approval_id`, bei Verknuepfungen `thread_id`, nach `proposed`).
- Downgrade setzt `lock_timeout` auf 10 s und sperrt (ACCESS EXCLUSIVE)
  `assistant_channel_accounts`, dann `assistant_thread_links`, `assistant_threads`, `assistant_messages`,
  `memory_facts`, `memory_approvals` und zuletzt `events` (siehe «Downgrade» oben).
