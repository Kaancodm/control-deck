# Uebergabe-Schemas v1

Control Deck tauscht Auftraege und Ergebnisse als JSON aus. Normative Definitionen (Repo und installiertes Wheel):

- `schemas/task.v1.json` (Entwickler-Referenz)
- `schemas/result.v1.json` (Entwickler-Referenz)
- `app/schemas/*.json` (gebundelt im Paket; Laufzeit-Laden via `importlib.resources`)

Spiegel in Python: `app/core/schemas.py`.

**Versionierungsregel:** `v1` wird **nie** geaendert. Erweiterungen erfolgen nur als neue Dateien
(`task.v2.json`, `result.v2.json`) neben v1. Alte Producer und Consumer muessen v1 weiterhin akzeptieren.

`additionalProperties: false` gilt auf allen Objektebenen.

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
