# HTTP-API (Control Deck)

Die FastAPI-Schicht verwendet die bestehenden DB-Module (`app/db`, `transition_task`, Queue).
Etappe `05-data-gate` ergaenzt die Datenschranke an Erstellung, Freigabe und Lease.
Etappe 6 normalisiert die Scanner-Schattenkopie und drosselt wiederholte Lease-Poll-Fehler;
ihre Redaction-Maps erhalten keinen HTTP-Endpunkt.
Basis-Pfad fuer versionierte Endpunkte: **`/api/v1`**. OpenAPI: `/api/v1/docs`, Schema: `/api/v1/openapi.json`.

## Konfiguration

| Variable | Zweck |
|----------|--------|
| `DECK_DATABASE_URL` | SQLAlchemy-URL fuer Laufzeit (Pflicht fuer `/readyz` und Task-Endpunkte) |
| `DECK_WORKER_TOKEN_OLLAMA` | Eigenes Token fuer die Ollama-Lane (`Authorization: Bearer …` oder Header `X-Deck-Worker-Token`) |
| `DECK_WORKER_TOKEN_CLAUDE` | Eigenes Token fuer die Claude-Lane |
| `DECK_WORKER_TOKEN_CODEX` | Eigenes Token fuer die Codex-Lane |
| `DECK_OWNER_TOKEN` | Gemeinsames Geheimnis fuer Owner-Endpunkte (`Authorization: Bearer …`); Login folgt in Etappe 15 |
| `DECK_LEASE_TTL_SECONDS` | Default-Lease-Dauer beim Pull (Default `900`) |
| `DECK_HEARTBEAT_EXTEND_SECONDS` | Verlaengerung bei Heartbeat (Default `300`) |
| `DECK_ROUTER_FILE` | Optionale lokale Router-Konfiguration mit Regeln, Modellen und Tagesgrenzen |
| `DECK_INBOX_DIR` | Eingangsverzeichnis fuer Textdateien (Default `inbox`) |
| `DECK_BLOCKLIST_PATH` | Lokale UTF-8-Literalliste ausserhalb des Repositories; fuer Cloud-Auftraege Pflicht |

Owner-Endpunkte verlangen **`DECK_OWNER_TOKEN`** als Bearer-Token (401 ohne gueltiges Token).

## Fehlerformat

Fehlerantworten verwenden **RFC 7807** (`Content-Type: application/problem+json`) mit Feldern `type`, `title`, `status`, `detail`, optional `instance` und `errors`.

## Owner-Endpunkte (`/api/v1/tasks`)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/tasks` | `task.v1` und Datenschranke pruefen; fehlende `data_class` → **422** (Pflichtfeld gemaess ADR 0005). Zulaessige Tasks starten in **`awaiting_approval`**, Cloud-Tasks mit nicht geheimen Datenschutztreffern in **`blocked`**. |
| `POST` | `/tasks/dry-run` | Owner-Vorschau fuer Routing, Datenschranke und lokale Tagesbudgets; keine Speicherung, Reservierung oder Ausfuehrung. |
| `GET` | `/tasks` | Liste mit Query `status`, `data_class`, Pagination `limit` (1–200, Default 50), `offset`. |
| `GET` | `/tasks/failed` | Schreibgeschuetzte Fehlerliste mit `data_class`, `limit` und `offset`; keine Wiederbelebung und kein Deadline-Sweep. |
| `GET` | `/tasks/{id}` | Detail inkl. `events` und `results`. |
| `POST` | `/tasks/{id}/approve` | Datenschranke erneut pruefen; bei Erfolg `awaiting_approval` → `queued`, setzt `approved_at`. Neue Sperrtreffer fuehren zu `blocked`. |
| `POST` | `/tasks/{id}/review` | Owner-Entscheid fuer einen Task in `review`: Zustimmung → `done`, Ablehnung → `failed`. |
| `POST` | `/tasks/{id}/cancel` | Abbruch (Owner-Override ohne Lease). Laufende Tasks (`running`): `cancel_requested` gesetzt; Worker erhaelt beim naechsten Heartbeat den Entscheid `cancel`. |

Interne Persistenz (`create_task_from_v1`, `POST /tasks`) verwendet dieselbe Datenschranke.
Gespeicherte Tasks erhalten zuerst **`awaiting_approval`** und `task.submit`; bei einer
Datenschutzsperre folgt noch in derselben Transaktion **`blocked`** mit `task.block`.
**`queued`** ist nur nach Owner-Freigabe (`POST /tasks/{id}/approve` mit Bearer-Token) erreichbar.

### Retry-Metadaten und Fehlerliste

Task-Zusammenfassungen enthalten ausserhalb des eingefrorenen Auftrags die aktuelle
`lane`, `next_attempt_at` und `retry_state` (jeweils optional). `retry_state` hat Version 1
und enthaelt `cursor`, `pinned_worker`, `rate_limits` je Lane und `internal_retries`.
Die Werte sind Beobachtungsdaten und koennen nicht ueber den Task-Body gesetzt werden.

`GET /tasks/failed` antwortet mit `{items, total, limit, offset}`. Ein Element enthaelt die
Task-Zusammenfassung und `last_error_code`; dieser ist ein validierter Fehlercode des
letzten lokal gespeicherten Ergebnisses oder `null`. Lokale Acceptance-Fehler erscheinen
als `acceptance_failed`, auch wenn der Worker Erfolg gemeldet hatte. Die Liste liefert
keine Provider-Rohmeldungen, Outputs oder Auftragsinhalte und erfordert Owner-Auth.
Es gibt keinen Retry-/Unblock-Endpunkt fuer terminale Tasks. Fehlervertrag und Grenzen:
[ETAPPEN/10-fehlerpfade.md](ETAPPEN/10-fehlerpfade.md).

### Routing-Vorschau

`POST /tasks/dry-run` validiert denselben Body wie das Anlegen, einschliesslich optionaler
Owner-Deadline. Es liefert nur sichere Entscheidungsmetadaten. Der Auftrag und gefundene
Rohwerte erscheinen nicht in der Antwort. Die Vorschau ist keine Freigabe und reserviert
kein Tagesbudget. Der Claim prueft Konfiguration, Schranke und Verbrauch erneut.
Regeln, Antwortbedeutung und Grenzen: [ROUTING.md](ROUTING.md).

### Datenschranke

| Situation bei `POST /tasks` | Antwort / Wirkung |
|----------------------------|-------------------|
| Erkannte Schluessel oder Zugangsdaten, jede Datenklasse | **422**, kein Task und kein Audit-Event gespeichert |
| Personendaten oder geschuetzte Kennungen in `local_only` | **201**, `awaiting_approval` |
| Nicht geheime Treffer in `cloud_ok` oder nicht unterstuetzte Treffer in `cloud_redacted` | **201**, `blocked`, keine Lease moeglich |
| Unterstuetzte Treffer in `cloud_redacted` | **201**, `awaiting_approval`; vor Auslieferung lokal pseudonymisiert |
| Unsichere, unlesbare oder nicht unterstuetzte Datei; Groessenlimit; `context_refs` | **422**, keine Speicherung |
| Fehlende Blocklist-Konfiguration fuer Cloud oder fehlerhafte konfigurierte Liste | **503**, keine Speicherung |

Die API meldet bei Datenschutztreffern ausschliesslich Kategorie, Regel, strukturelles Feld und
Zeichenpositionen. Fehlerantworten enthalten keine gefundenen Rohwerte oder lokalen Dateipfade.
Der originale Auftrag bleibt bei nicht geheimen Treffern lokal gespeichert und fuer den Owner
in der Task-Ansicht sichtbar; die Treffer-Metadaten ersetzen keine Pseudonymisierung.

Dateien und Blockliste werden vor Freigabe und Lease erneut gelesen. Ein neuer Treffer oder
dauerhafter Dateifehler setzt einen vorhandenen Task auf `blocked`; die Freigabeantwort ist **200** mit
diesem Status. Die Queue ueberspringt solche Tasks und prueft den naechsten freigegebenen Auftrag.
Ein Konfigurationsfehler ergibt **503** und erteilt keine Freigabe oder Lease fuer den betroffenen Task.
Ein bereits `blocked` gesetzter Task laesst sich nicht mit `approve` entsperren (**409**).

`file_unavailable` ist voruebergehend: Freigabe **409** bei unveraendertem Status, beim Lease-Pull
Ueberspringen des Tasks fuer diesen Aufruf. `task.gate_error` enthaelt sichere Fehler-Metadaten;
Versuchszahl und Lease bleiben unveraendert. Bei erfolgreicher Freigabe speichert `task.approve`
je gelesener Datei den Input-Index und SHA-256 unter `detail.file_hashes`.
Identische Lease-Poll-Fehler werden pro Task, Fehlercode und Quelle hoechstens einmal in
60 Sekunden protokolliert. Die Drosselung verwendet DB-Zeit und Task-Lock; Owner-Freigabeversuche
bleiben einzeln auditiert.

Etappe 8 verbindet den lokalen Payload-Builder mit der Worker-API. Diese liefert nur
vorbereiteten Versandtext und sichere Versuchsdaten. Unterstuetzte Treffer bei
`cloud_redacted` sind zulaessig; Secrets, Benutzerverzeichnisse und interne Hostnamen bleiben
gesperrt. Maps erscheinen nicht in Task-, Ergebnis- oder Event-Antworten. Der Host vergleicht
die final gelesenen Anhaenge gegen Freigabe-Hashes und committed Maps vor der Auslieferung.
Details: [RUNNER.md](RUNNER.md), [REDAKTION.md](REDAKTION.md).
Erkennungskatalog, Datei-Limits und Konfiguration: [DATENSCHRANKE.md](DATENSCHRANKE.md).

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

Die Identitaet folgt ausschliesslich aus dem Token der Lane. Nur konfigurierte Lanes
koennen sich authentifizieren; das fruehere gemeinsame `DECK_WORKER_TOKEN` wird nicht
mehr akzeptiert. Alle konfigurierten Lane-Tokens muessen verschieden sein und duerfen
nicht dem Owner-Token entsprechen. Eine mehrdeutige Konfiguration sperrt Worker- und
Owner-Authentifizierung mit **503**. Eine reine Owner-Konfiguration bleibt zulaessig.
Ungueltige oder widerspruechliche Header ergeben **401**. Die Pflichtangabe `worker`
beziehungsweise `result.worker` muss zur authentifizierten Lane passen, sonst **403**
vor jedem Task-Zugriff. Anbieter erhalten keine dieser Host-Zugangsdaten.

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/worker/lease` | Pflichtfeld `worker`; nur zugeordnete Lane, Budget und Runner-Steuerung pruefen; Payload vorbereiten und vor Antwort committen. |
| `POST` | `/worker/start` | `task_id`, `attempt`, `lease_id`, `worker`; vorbereitetes `leased` explizit nach `running`, kein automatischer Start bei Ergebnisannahme. |
| `POST` | `/worker/heartbeat` | Dieselbe Identitaet, optional `extend_seconds`; committed Entscheid `continue`, `cancel`, `expired` oder `lost_lease`. |
| `POST` | `/worker/result` | `result.v1` und `lease_id`; Lane, Versuch, Versandhash und Redaction-Metadaten pruefen, lokal rueckabbilden und fenced annehmen. |

Dequeue verlangt ein gesetztes **`approved_at`** und ein auditierbares Owner-Freigabe-Event.
Auch direkte Queue-Aufrufe muessen die Lane ausdruecklich angeben; es gibt keinen Default.
Die Lease-Antwort enthaelt weder Originalauftrag noch fruehere Ergebnisse oder Maps.
`local_only` wird ausschliesslich an Ollama geclaimt; die beiden frueheren `xfail`-Tests
zur Worker-Zuordnung bestehen jetzt ohne Ausnahme.

Jeder authentifizierte Lease-Pull bereinigt abgelaufene Leases in einer eigenen committed
Transaktion vor dem Claim. Dadurch wird nach einem Worker-Absturz belegte Parallelitaet
wieder frei. Ein noch aktiver, unbestaetigter Abbruch behaelt seine Kapazitaet bis zur
Bestaetigung oder zum Lease-Ablauf. Ein Konflikt mit einem vorhandenen Payload-Artefakt
blockiert den betroffenen Task mit auditiertem Grund `artifact_conflict`; derselbe Pull
prueft anschliessend den naechsten Task, begrenzt auf 32 Vorbereitungsversuche.

Owner-Steuerung: `GET /api/v1/control/settings` und `PATCH /api/v1/control/settings` mit
`{"changes": {"paused": true}}`. Bekannte Schluessel: `paused`, `stop_requested`,
`max_parallel` (1 bis 16), `lanes.ollama.paused`, `lanes.claude.paused`, `lanes.codex.paused`.
Pause betrifft neue Claims; Not-Aus setzt zusaetzlich Abbruchanforderungen fuer aktive
Versuche. Standardparallelitaet ist 1. Details und Inbetriebnahmegrenzen: [RUNNER.md](RUNNER.md).

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
| `blocked` | Datenschutz- oder Policy-Sperre; terminal, kein Freigabe-Override |
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
export DECK_WORKER_TOKEN_OLLAMA='dev-ollama-token'
export DECK_WORKER_TOKEN_CLAUDE='dev-claude-token'
export DECK_WORKER_TOKEN_CODEX='dev-codex-token'
export DECK_OWNER_TOKEN='dev-owner-token'
uv run pytest tests/integration/test_api.py -v
```
