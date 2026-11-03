# HTTP-API (Control Deck)

Etappe `04-fastapi-api` stellt die FastAPI-Schicht auf Basis der bestehenden DB-Module (`app/db`, `transition_task`, Queue) bereit. Basis-Pfad fuer versionierte Endpunkte: **`/api/v1`**. OpenAPI: `/api/v1/docs`, Schema: `/api/v1/openapi.json`.

## Konfiguration

| Variable | Zweck |
|----------|--------|
| `DECK_DATABASE_URL` | SQLAlchemy-URL fuer Laufzeit (Pflicht fuer `/readyz` und Task-Endpunkte) |
| `DECK_WORKER_TOKEN` | Gemeinsames Geheimnis fuer Worker (`Authorization: Bearer …` oder Header `X-Deck-Worker-Token`) |
| `DECK_OWNER_TOKEN` | Gemeinsames Geheimnis fuer Owner-Endpunkte (`Authorization: Bearer …`); Login folgt in Etappe 15 |
| `DECK_LEASE_TTL_SECONDS` | Default-Lease-Dauer beim Pull (Default `900`) |
| `DECK_HEARTBEAT_EXTEND_SECONDS` | Verlaengerung bei Heartbeat (Default `300`) |

Owner-Endpunkte verlangen **`DECK_OWNER_TOKEN`** als Bearer-Token (401 ohne gueltiges Token).

## Fehlerformat

Fehlerantworten verwenden **RFC 7807** (`Content-Type: application/problem+json`) mit Feldern `type`, `title`, `status`, `detail`, optional `instance` und `errors`.

## Owner-Endpunkte (`/api/v1/tasks`)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/tasks` | `task.v1` validieren; fehlende `data_class` → **422** (Pflichtfeld gemaess ADR 0005). Neuer Task startet in **`awaiting_approval`** (Freigabe noetig), Audit-Event `task.submit`. |
| `GET` | `/tasks` | Liste mit Query `status`, `data_class`, Pagination `limit` (1–200, Default 50), `offset`. |
| `GET` | `/tasks/{id}` | Detail inkl. `events` und `results`. |
| `POST` | `/tasks/{id}/approve` | Owner-Freigabe: `awaiting_approval` → `queued`, setzt `approved_at`. |
| `POST` | `/tasks/{id}/cancel` | Abbruch (Owner-Override ohne Lease). Laufende Tasks (`running`): `cancel_requested` gesetzt; Worker bemerkt Abbruch erst beim naechsten Heartbeat/Lease-Check (404). |

Interne Persistenz (`create_task_from_v1`, `POST /tasks`) legt Tasks immer in **`awaiting_approval`** an; **`queued`** ist nur nach Owner-Freigabe (`POST /tasks/{id}/approve` mit Bearer-Token) erreichbar.

## Worker-Endpunkte (`/api/v1/worker`)

Authentifizierung ueber `DECK_WORKER_TOKEN` (Pflicht).

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/worker/lease` | Pull aeltester freigegebener `queued`-Task (`FOR UPDATE SKIP LOCKED`), Transition `acquire`, Lease-Zeile. |
| `POST` | `/worker/heartbeat` | Lease verlaengern (`lease_id`, `task_id`, optional `extend_seconds`). |
| `POST` | `/worker/result` | `result.v1` validieren, speichern, Transition fenced per `lease_id` (auto-`start` aus `leased`). |

Dequeue beruecksichtigt **`requires_approval`** und **`approved_at`**: nur freigegebene Tasks in `queued` werden gezogen.

## Health

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/healthz` | Liveness (ohne DB). |
| `GET` | `/readyz` | Readiness inkl. `SELECT 1` gegen die DB. |

## Task-Status (Ergaenzung)

| Status | Bedeutung |
|--------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe |

Erlaubte Uebergaenge: siehe `app/db/state_machine.py` (`submit`, `approve`). Migration `20251103_0002` ergaenzt den Enum-Wert sowie Spalten `requires_approval` (Default `true`) und `approved_at`; **Bestand** aus Revision 0001 mit Status `queued` wird ohne Auto-Freigabe nach `awaiting_approval` verschoben.

Doppelte `task_id` bei `POST /tasks` → **409** (`application/problem+json`).

## Tests

- **Unit:** `TestClient` ohne DB (`/healthz`, Problem-JSON bei fehlender DB-Konfiguration).
- **Integration:** nur mit `DECK_TEST_DATABASE_URL` (Name endet auf `_test`); httpx/`TestClient` gegen migrierte Postgres-Instanz.

```bash
export DECK_TEST_DATABASE_URL='postgresql+psycopg://…/controldeck_test'
export DECK_WORKER_TOKEN='dev-worker-token'
export DECK_OWNER_TOKEN='dev-owner-token'
uv run pytest tests/integration/test_api.py -v
```
