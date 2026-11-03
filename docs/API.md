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
| `POST` | `/tasks/{id}/review` | Owner-Entscheid fuer einen Task in `review`: Zustimmung → `done`, Ablehnung → `failed`. |
| `POST` | `/tasks/{id}/cancel` | Abbruch (Owner-Override ohne Lease). Laufende Tasks (`running`): `cancel_requested` gesetzt; Worker bemerkt Abbruch erst beim naechsten Heartbeat/Lease-Check (404). |

Interne Persistenz (`create_task_from_v1`, `POST /tasks`) legt Tasks immer in **`awaiting_approval`** an; **`queued`** ist nur nach Owner-Freigabe (`POST /tasks/{id}/approve` mit Bearer-Token) erreichbar.

### Owner-Deadline

`POST /tasks` akzeptiert neben den bisherigen `task.v1`-Feldern optional `deadline_at` als ISO-8601-Zeitpunkt **mit Zeitzone** oder `null`. Beispiel fuer diese zusaetzlichen API-Metadaten:

```json
{"deadline_at": "2030-01-01T12:00:00Z"}
```

Das Beispiel ergaenzt einen vollstaendigen `task.v1`-Body; `data_class` bleibt Pflicht. Die API entfernt `deadline_at` vor der Schema-Validierung und speichert den Wert als separate Task-Spalte. Der unveraenderliche `task.v1`-Payload enthaelt ihn nicht. Task-Listen und Task-Details geben `deadline_at` separat zurueck. Es gibt in dieser Etappe keinen Endpunkt zum nachtraeglichen Aendern der Deadline.

Eine beim Anlegen bereits erreichte oder vergangene Deadline wird anhand der DB-Zeit mit **422** abgewiesen, bevor ein Task oder Audit-Event gespeichert wird. `null` oder ein fehlendes Feld bleiben erlaubt.

Ohne explizite Owner-Deadline laeuft ein Task **nicht** ab. Weder `policy.timeout_s` noch Lease-TTL oder Heartbeats erzeugen eine Deadline. Erst wenn das gesetzte `deadline_at` erreicht ist, kann ein nichtterminaler Task nach `expired` wechseln. Die Ablaufpruefung erfolgt bei API-/Queue-Zugriffen; SQL-Fencing verhindert auch vor dem naechsten Sweep, dass ueberfaellige Arbeit neu geleast oder mit einem spaeten Ergebnis abgeschlossen wird. Ein Ablauf entfernt die Lease und erzeugt `task.expire`.

### Ergebnis-Review

```json
{"approved": true, "reason": "Abnahme erfuellt"}
```

`approved` ist ein Pflichtfeld vom Typ Boolean; `reason` ist optional (maximal 2000 Zeichen). `false` lehnt das Ergebnis ab und beendet den Task mit `failed`. Eine Entscheidung ist ausschliesslich aus `review` erlaubt; ein wiederholter Entscheid oder ein anderer Ausgangsstatus ergibt **409**. Der Worker-Token berechtigt nicht zum Review. Entscheide erzeugen `task.review_approve` beziehungsweise `task.review_reject` und veraendern den gespeicherten Ergebnis-Snapshot nicht. Ein Export in die Wissens-DB ist hier nicht enthalten.

## Worker-Endpunkte (`/api/v1/worker`)

Authentifizierung ueber `DECK_WORKER_TOKEN` (Pflicht).

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/worker/lease` | Pull aeltester freigegebener `queued`-Task (`FOR UPDATE SKIP LOCKED`), Transition `acquire`, Lease-Zeile. |
| `POST` | `/worker/heartbeat` | Lease verlaengern (`lease_id`, `task_id`, optional `extend_seconds`). |
| `POST` | `/worker/result` | `result.v1` validieren, Acceptance im Core pruefen, Ergebnis speichern und fenced per `lease_id` wechseln (auto-`start` aus `leased`). |

Dequeue verlangt ein gesetztes **`approved_at`** (Owner-Freigabe); Tasks in `queued` ohne Freigabe werden nicht gezogen.

Bei `result.v1.status = succeeded` berechnet `app/core/acceptance.py` die Checks aus `task.acceptance` und `result.output.text` selbst; vom Worker gemeldete Checks ersetzen diese Pruefung nicht. Ein automatischer Fehlschlag wird als Ergebnis mit `status = failed` und `error.code = acceptance_failed` gespeichert. Der Task geht bei verbleibenden Versuchen zurueck in die Queue, sonst nach `failed`.

Die Acceptance laeuft vor dem Task-Row-Lock auf dem unveraenderlichen Auftrag. Danach werden Status, Versuch, Lease und Deadline erneut geprueft; zwischenzeitliche Heartbeats, Abbrueche oder Neuzuweisungen bleiben wirksam. Das Retry-/Fail-Event verwendet den normalisierten `result_status = failed` und den Grund `acceptance_failed`.

Sind alle automatischen Regeln erfuellt, folgt standardmaessig `review`. Nur `policy.require_review = false` **ohne** `human`-Regel erlaubt direkt `done`. Eine `human`-Regel verlangt immer den Owner-Entscheid. `result.v1.status` bleibt dabei unveraendert das Worker-Ergebnisformat (`succeeded`, `failed`, `blocked`, `cancelled`); `review`, `done` und `expired` sind Task-Status.

Ein reiner Lease-Ablauf fuehrt weiterhin zu Retry, gegebenenfalls `awaiting_approval` ohne bestehende Freigabe oder `failed` bei erschoepften Versuchen. Er ist kein Grund fuer `expired`.

## Health

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/healthz` | Liveness (ohne DB). |
| `GET` | `/readyz` | Readiness inkl. `SELECT 1` gegen die DB. |

## Task-Status (Ergaenzung)

| Status | Bedeutung |
|--------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe |
| `review` | Automatische Abnahme bestanden, wartet auf Owner-Entscheid |
| `done` | Abgeschlossen nach Review oder explizit ohne Review-Pflicht |
| `expired` | Explizite Owner-Deadline erreicht; terminal |
| `succeeded` | Historischer terminaler Task-Status; neue Erfolge verwenden `review` oder `done` |

Erlaubte Uebergaenge: siehe `app/db/state_machine.py` (`submit`, `approve`). Migration `20251103_0002` ergaenzt den Enum-Wert sowie Spalten `requires_approval` (Default `true`) und `approved_at`; **Bestand** aus Revision 0001 mit Status `queued` wird ohne Auto-Freigabe nach `awaiting_approval` verschoben.

Migration `20251103_0007` ergaenzt `review`, `done`, `expired` und die optionale Spalte `deadline_at`. Bestehende `succeeded`-Tasks behalten ihren historischen Status.

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
