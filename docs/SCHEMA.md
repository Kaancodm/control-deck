# Uebergabe-Schemas v1

Control Deck tauscht Auftraege und Ergebnisse als JSON aus. Normative Definitionen (Repo und installiertes Wheel):

- `schemas/task.v1.json` (Entwickler-Referenz)
- `schemas/result.v1.json` (Entwickler-Referenz)
- `schemas/reference_case.v1.json` (Entwickler-Referenz, Referenz-Testfaelle)
- `app/schemas/*.json` (gebundelt im Paket; Laufzeit-Laden via `importlib.resources`)

Spiegel in Python: `app/core/schemas.py`.

**Versionierungsregel:** `v1` wird **nie** geaendert. Erweiterungen erfolgen nur als neue Dateien
(`task.v2.json`, `result.v2.json`) neben v1. Alte Producer und Consumer muessen v1 weiterhin akzeptieren.

`additionalProperties: false` gilt auf allen Objektebenen.

Die Task-Status `review`, `done`, `expired` und die API-/DB-Metadaten `deadline_at` erweitern **nicht** die eingefrorenen v1-Schemas. `deadline_at` wird nur beim Owner-Create neben dem vollstaendigen `task.v1` angenommen, separat gespeichert und in der Task-Antwort ausgegeben. Direktes Validieren als `task.v1` lehnt dieses Zusatzfeld weiterhin ab. `data_class` bleibt in jedem Task-Payload Pflicht, ohne impliziten Default.

---

## task.v1

| Feld | Typ | Pflicht | Beschreibung |
|------|-----|---------|--------------|
| `schema_version` | `"task.v1"` | ja | Konstante Versionskennung |
| `task_id` | UUID | ja | Eindeutige Auftrags-ID |
| `parent_task_id` | UUID \| null | ja | Parent bei Unterauftraegen, sonst null |
| `created_at` | date-time | ja | Erstellungszeitpunkt (ISO 8601) |
| `requested_by` | enum | ja | `ui`, `api`, `n8n`, `system` |
| `kind` | enum | ja | Aufgabentyp (research, summarize, …) |
| `title` | string | ja | Kurztitel, max. 120 Zeichen |
| `instructions` | string | ja | Arbeitsanweisung, min. 1 Zeichen |
| `inputs` | array | ja | Eingaben (Text oder Dateireferenz) |
| `context_refs` | array | ja | Verweise auf Wissenskontext |
| `data_class` | enum | ja | **Kein Default.** `local_only`, `cloud_redacted`, `cloud_ok` |
| `policy` | object | ja | Laufzeit- und Review-Policy |
| `acceptance` | array | ja | Abnahmeregeln fuer Ergebnisse |
| `fallback_chain` | array | ja | Min. 1 Eintrag, eindeutig, Werte: `ollama`, `claude`, `codex` |
| `labels` | object | ja | Freie string→string Metadaten |

### inputs[]

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `name` | string | Logischer Name |
| `type` | `text` \| `file` | Eingabetyp |
| `value` | string | Pflicht bei `text` |
| `path` | string | Pflicht bei `file`; relativ, kein `..`, nicht absolut |
| `mime` | string | Optional bei Dateien |

### context_refs[]

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `source` | `knowledge_db` | Kontextquelle |
| `ref` | string | Stabiler Referenzschluessel |

### policy

| Feld | Typ | Default | Beschreibung |
|------|-----|---------|--------------|
| `allowed_workers` | array \| null | null | Allow-List; null = keine Extra-Einschraenkung |
| `forbidden_workers` | array | [] | Deny-List |
| `max_attempts` | int 1–5 | 3 | Max. Versuche ueber die Kette |
| `timeout_s` | int 30–7200 | 900 | Timeout pro Versuch |
| `require_review` | bool | true | Menschliche Freigabe vor `done` |

### acceptance[]

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `type` | enum | must_contain, must_not_contain, regex, json_schema, max_chars, human |
| `value` | string | Optional, abhaengig vom Typ |

Die verbindliche Ergebnispruefung liegt in `app/core/acceptance.py`. Sie prueft `result.output.text` und berechnet die gespeicherten Checks selbst; ein vom Worker gemeldetes `passed: true` ersetzt keine Pruefung.

| Regel | Bedeutung von `value` |
|-------|----------------------|
| `must_contain` | Teiltext, der enthalten sein muss |
| `must_not_contain` | Teiltext, der nicht vorkommen darf |
| `regex` | Regulaerer Ausdruck gegen den Ausgabetext |
| `json_schema` | JSON-Schema als JSON-String; der Ausgabetext muss gueltiges JSON sein und das Schema erfuellen |
| `max_chars` | Nichtnegative maximale Zeichenzahl als String |
| `human` | Optionaler Hinweis fuer die menschliche Abnahme; erzwingt `review` |

Fehlende oder ungueltige Werte automatischer Regeln schlagen fehl. Regex- und JSON-Schema-Pruefungen laufen isoliert: Prozessstart und Imports haben ein eigenes Limit von zehn Sekunden. Nach der Bereitschaftsmeldung stehen fuer Payload-Uebertragung und Auswertung weitere zwei Sekunden zur Verfuegung. Ein Timeout gilt als Fehlschlag und wird mit der betroffenen Phase, aber ohne Auftragsinhalt protokolliert. Die Auswertung erfolgt vor dem Task-Row-Lock; der Abschluss prueft anschliessend den aktuellen Laufzeitstand erneut. JSON-Schemas duerfen lokale Referenzen verwenden; externe Referenzen werden nicht nachgeladen. Es gibt dabei weder Dateizugriffe auf Ergebnis-Artefakte noch Netzwerkzugriffe.

Eine `human`-Regel bleibt bis zum Owner-Entscheid als ausstehende Abnahme sichtbar. Sie ist kein automatischer Fehlschlag, verhindert aber direktes `done` auch bei `policy.require_review = false`. Ein automatischer Fehlschlag erzeugt `acceptance_failed` und fuehrt ohne Retry nach `review`, wo der Owner entscheidet; erst bestandene automatische Regeln erlauben `done`.

---

## result.v1

| Feld | Typ | Pflicht | Beschreibung |
|------|-----|---------|--------------|
| `schema_version` | `"result.v1"` | ja | Konstante Versionskennung |
| `task_id` | UUID | ja | Zugehoeriger Auftrag |
| `attempt` | int ≥ 1 | ja | Versuchsnummer |
| `worker` | enum | ja | `ollama`, `claude`, `codex` |
| `status` | enum | ja | succeeded, failed, cancelled, blocked |
| `output` | object | ja | Text und Dateiliste |
| `checks` | array | ja | Auswertung der Acceptance-Regeln |
| `usage` | object | ja | Token- und Laufzeitmetriken |
| `timing` | object | ja | started_at, finished_at |
| `error` | object \| null | ja | Fehlerdetail; siehe Regeln unten |
| `redaction` | object | ja | Pseudonymisierung |
| `payload_hash` | string | ja | SHA-256 hex (64 Zeichen) |
| `log_ref` | string | ja | Relativer Pfad zum Laufzeitlog |

Der Ergebnisstatus beschreibt den Versuch, nicht den Task-Lebenszyklus: Ein akzeptiertes `succeeded`-Ergebnis fuehrt je nach Review-Pflicht zum Task-Status `review` oder `done`. Historische Tasks mit Status `succeeded` bleiben lesbar. Owner-Review schreibt Audit-Events und veraendert den gespeicherten Versuch nicht.

### output

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `text` | string | Hauptausgabe |
| `files` | array | `{name, path, sha256}` |

### usage

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `input_tokens` | int | Optional |
| `output_tokens` | int | Optional |
| `wall_seconds` | number | Pflicht, Wandzeit |

### error (wenn nicht null)

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `code` | enum | worker_unavailable, auth_required, rate_limited, timeout, policy_blocked, acceptance_failed, internal |
| `message` | string | Kurze Fehlerbeschreibung |

### Regeln status ↔ error

- `status == failed` ⇒ `error` **muss** gesetzt sein.
- `status == succeeded` ⇒ `error` **muss** null sein.
- Andere Status: `error` null oder gesetzt je nach Semantik (Validator erzwingt vor allem failed/succeeded).

### redaction

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `applied` | bool | Wurde pseudonymisiert? |
| `placeholders` | int | Anzahl Platzhalter |

---

## Validierung

- Programmatisch: `app/core/validate.py` (`validate_task`, `validate_result`)
- CLI: `uv run python scripts/validate.py task|result <datei.json>`

Beide Pruefpfade (JSON Schema und Pydantic) muessen uebereinstimmen; siehe Unit-Tests.
