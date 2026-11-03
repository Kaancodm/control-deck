# Datenbank (Control Deck)

Etappe `03-db-schema` fuehrt PostgreSQL als persistente Queue und Audit-Store ein. Die Verbindung erfolgt ausschliesslich ueber die Umgebungsvariable `DECK_DATABASE_URL` (SQLAlchemy-URL, z. B. `postgresql+psycopg://…/controldeck`). Es werden keine Zugangsdaten im Repository abgelegt.

## Tabellen

| Tabelle | Zweck |
|---------|--------|
| `tasks` | Unveraenderlicher `task.v1`-Payload (JSONB), Laufstatus, `data_class`, Versuchszaehler, Freigabe (`requires_approval`, `approved_at`), optionale Owner-Deadline (`deadline_at`) |
| `results` | `result.v1`-Snapshots pro Versuch (eindeutig pro `task_id` + `attempt`) |
| `events` | Append-only Audit-Log (Uebergaenge, Metadaten in JSONB) |
| `leases` | Pull-Lease pro Task (`lease_id`, `expires_at`, `heartbeat_at`, optional `holder`) |

### `data_class`

Pflichtfeld gemaess ADR 0005, ohne Default in Schema oder Datenbank. Revision `20251103_0003` entfernt den frueheren DB-Default. Erlaubte Werte: `local_only`, `cloud_redacted`, `cloud_ok` (identisch zu `task.v1`).

### Task-Status

| Status | Bedeutung |
|--------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe (HTTP-API) |
| `queued` | Freigegeben, wartet auf Lease |
| `leased` | Runner haelt Lease, Start steht aus |
| `running` | Ausfuehrung aktiv |
| `review` | Automatische Abnahme bestanden, wartet auf Owner-Entscheid |
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

## Queue (SKIP LOCKED)

Runner ziehen Arbeit mit `SELECT … FOR UPDATE SKIP LOCKED` auf dem aeltesten `queued`-Task (Index `status`, `created_at`). Die Funktion `dequeue_next_queued_task` nutzt zentral `transition_task` (Audit-Event, `attempt += 1`, `updated_at`) und ersetzt die Lease-Zeile re-acquire-sicher (ADR 0001, 0004). Abgelaufene Leases holt `reap_expired_leases` per `SKIP LOCKED` zurueck in die Queue.

Persistenz neuer Auftraege: `create_task_from_v1` validiert `TaskV1`, leitet `id` / `data_class` aus dem Payload ab und startet in `awaiting_approval` (Owner-Freigabe via `approve_task` / HTTP-Approve).

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

Ein Downgrade wird verweigert, solange Tasks oder Audit-Events einen der drei neuen Status verwenden oder eine Owner-Deadline gespeichert ist. Die betreffenden Daten muessen vorher explizit migriert werden; Audit-Events werden nicht automatisch umgeschrieben oder geloescht. Nach erfolgreicher Pruefung werden die neuen Enum-Werte und die Deadline-Spalte entfernt.

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
