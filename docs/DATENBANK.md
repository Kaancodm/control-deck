# Datenbank (Control Deck)

Etappe `03-db-schema` fuehrt PostgreSQL als persistente Queue und Audit-Store ein. Die Verbindung erfolgt ausschliesslich ueber die Umgebungsvariable `DECK_DATABASE_URL` (SQLAlchemy-URL, z. B. `postgresql+psycopg://…/controldeck`). Es werden keine Zugangsdaten im Repository abgelegt.

## Tabellen

| Tabelle | Zweck |
|---------|--------|
| `tasks` | Unveraenderlicher `task.v1`-Payload (JSONB), Laufstatus, `data_class`, Versuchszaehler |
| `results` | `result.v1`-Snapshots pro Versuch (eindeutig pro `task_id` + `attempt`) |
| `events` | Append-only Audit-Log (Uebergaenge, Metadaten in JSONB) |
| `leases` | Pull-Lease pro Task (`lease_id`, `expires_at`, `heartbeat_at`, optional `holder`) |

### `data_class`

Pflichtfeld in `task.v1` gemaess ADR 0005 (kein Schema-Default). Erlaubte Werte: `local_only`, `cloud_redacted`, `cloud_ok`. Legacy: die Spalte behaelt DB-Default `local_only` fuer bestehende Zeilen; neue Payloads muessen `data_class` explizit setzen.

### Task-Status

| Status | Bedeutung |
|--------|-----------|
| `queued` | Validiert, wartet auf Lease |
| `leased` | Runner haelt Lease, Start steht aus |
| `running` | Ausfuehrung aktiv |
| `succeeded` | Erfolgreich abgeschlossen |
| `failed` | Nicht behebbarer Abschluss |
| `blocked` | Schranke / Policy |
| `cancelled` | Abgebrochen |

Die erlaubten Uebergaenge sind in `app/db/state_machine.py` als reine Python-Logik definiert; jeder Uebergang erzeugt ein Audit-Event (`task.<trigger>`).

Wichtige Pfade:

- `queued` → `leased` → `running` → Terminal (`succeeded`, `failed`, `blocked`, `cancelled`)
- Retry oder Lease-Ablauf: zurueck nach `queued` (`retry`, `lease_expired`)

## Queue (SKIP LOCKED)

Runner ziehen Arbeit mit `SELECT … FOR UPDATE SKIP LOCKED` auf dem aeltesten `queued`-Task (Index `status`, `created_at`). Die Funktion `dequeue_next_queued_task` nutzt zentral `transition_task` (Audit-Event, `attempt += 1`, `updated_at`) und ersetzt die Lease-Zeile re-acquire-sicher (ADR 0001, 0004). Abgelaufene Leases holt `reap_expired_leases` per `SKIP LOCKED` zurueck in die Queue.

Persistenz neuer Auftraege: `create_task_from_v1` validiert `TaskV1` und leitet `id` / `data_class` aus dem Payload ab (DB-CHECKs auf Konsistenz).

Worker-Uebergaenge aus `leased`/`running` erfordern die passende `lease_id` (Fencing). Retry respektiert `policy.max_attempts`; danach Dead-Letter via `failed`.

## Migrationen

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

Die produktive Instanz (Port 5433, Datenbank `controldeck`) wird ausserhalb dieses Repos verwaltet. Agents legen keine DB-Rollen an und greifen nicht auf Server zu — nur Schema, Migrationen und Tests im Repository.
