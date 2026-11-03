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

Pflichtfeld gemaess ADR 0005, Datenbank-Default **`local_only`**. Erlaubte Werte: `local_only`, `cloud_redacted`, `cloud_ok` (identisch zu `task.v1`).

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

Runner ziehen Arbeit mit `SELECT … FOR UPDATE SKIP LOCKED` auf dem aeltesten `queued`-Task (Index `status`, `created_at`). Die Funktion `dequeue_next_queued_task` setzt den Status auf `leased` und legt eine Lease-Zeile an (ADR 0001, 0004).

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

- **Unit:** State-Machine ohne Datenbank (`tests/unit/test_state_machine.py`).
- **Integration:** GitHub Actions startet `postgres:17`; Tests setzen `DECK_DATABASE_URL` und pruefen Migrationen sowie SKIP LOCKED mit zwei Sessions.

Lokal ohne Postgres werden Integrationstests uebersprungen; `make test` bleibt gruen.

## Betrieb (Hinweis)

Die produktive Instanz (Port 5433, Datenbank `controldeck`) wird ausserhalb dieses Repos verwaltet. Agents legen keine DB-Rollen an und greifen nicht auf Server zu — nur Schema, Migrationen und Tests im Repository.
