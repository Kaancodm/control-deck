# Etappe 14 - Wissens-DB-Anbindung

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 13 gemergt und Plan-Frage 4 beantwortet ist
Branch-Vorschlag: `stage/14-wissens-db`
Abhaengig von: Etappen 6 (Kontextbloecke im Payload), 9 (Ollama fuer Embeddings), 11 (Export-Knopf); Plan-Frage 4
Entscheidungen: E7 (Embedding-Modell nur in `router.yaml`), E11
Reviewer: Claude (gerade Etappe)
Abnahme laut Plan v1.1: Roundtrip (Export und spaeteres Laden); ein `local_only`-Chunk erscheint
nie in einem Cloud-Payload.

## 1 Ziel

Auftraege koennen ueber `context_refs` (`source = knowledge_db`) Wissens-Chunks laden; jeder Chunk
traegt eine Datenklasse und wird nur in Payloads injiziert, deren Klasse das erlaubt. Freigegebene
Ergebnisse (`done`) koennen manuell in die Wissens-DB exportiert werden (ADR 0007, Default
`local_only`). Vektorsuche mit pgvector, Embeddings ueber Ollama.

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| `context_refs[] = {source: knowledge_db, ref}` im Schema | belegt (`docs/SCHEMA.md`) |
| `KB_DB_URL` als Platzhalter | belegt (`.env.example`) |
| Payload-Builder nimmt `context_blocks` vor der Redaktion | Etappe 6 |
| CI-Postgres ohne pgvector | Annahme (`postgres:17`-Image) |
| Bestehende Wissens-DB des Owners: Tabellen, Spalten, Schreibrecht | Plan-Frage 4, offen |

## 3 Scope

**In:** Tabellen `kb_documents`, `kb_chunks` (pgvector), Klassenfilter, Retrieval, Embedding-Client,
Export-Endpunkt und UI-Knopf, Import-Skript, Tests, `docs/WISSENS_DB.md`.

**Out:** Chunking-Optimierung, Re-Ranking, automatischer Export, Schreiben in fremde Tabellen der
bestehenden Wissens-DB (bis Plan-Frage 4 geklaert), zweite Meinung durch Klassifikator (Plan: optional).

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | Eigene Tabellen im Schema `deck_kb` der ueber `KB_DB_URL` erreichbaren Datenbank; keine bestehenden Tabellen anfassen. `CREATE EXTENSION IF NOT EXISTS vector` in der Migration; Dimension ueber `alembic -x kb_dim=<n>` (Default 768) und gleich dem Eintrag `lanes.ollama.embeddings.dim` in `router.yaml`. |
| D2 | `kb_documents(id, title, source, data_class NOT NULL DEFAULT 'local_only', source_task_id UNIQUE NULL, created_at)`; `kb_chunks(id, document_id FK, ord, text, embedding vector(dim))`; CHECK auf die drei Klassen. |
| D3 | Klassenmatrix (Zeile = Auftrag, Spalte = Chunk): `local_only`-Auftrag darf `local_only`, `cloud_redacted`, `cloud_ok`; `cloud_redacted`-Auftrag darf `cloud_redacted`, `cloud_ok`; `cloud_ok`-Auftrag darf nur `cloud_ok`. Die Plan-Formulierung "derselben oder tieferen Klasse" wird so ausgelegt, dass nie ein Chunk eine Klasse verlaesst (sichere Richtung). |
| D4 | Retrieval: `ref` ist eine Dokument-ID oder ein Tag (`tag:<name>`); Embedding der `instructions` ueber `POST {OLLAMA_URL}/api/embed` (Modell aus `router.yaml`); Top-k (Default 5, max 20 aus `labels["kb_top_k"]`) per Kosinus-Distanz, danach Klassenfilter D3 in SQL (nicht erst in Python). |
| D5 | Injektion als Bloecke `### Kontext <doc-id>#<ord> (<data_class>)` in den Payload-Abschnitt `## Kontext`; Bloecke laufen durch Scanner und Redaktion (Etappe 6). |
| D6 | Export nur aus `done`: `POST /api/v1/tasks/{id}/export` (Owner) und UI-Knopf (Etappe 11 Detailseite); Klasse = `task.data_class`, der Owner kann strenger waehlen; Default-Vorschlag `local_only`; Chunking in Abschnitte von max. 1500 Zeichen; Event `kb.export`; ein Export je Task (UNIQUE). |
| D7 | Import lokaler Markdown-Dateien: `scripts/kb_import.py <ordner> --data-class <klasse>` (Pflichtangabe, kein Default) fuer Startinhalte; Dateien aus `inbox/` nur nach Scanner-Pruefung. |
| D8 | Embedding-Aufrufe nur host-seitig an Ollama; kein Cloud-Embedding. |
| D9 | Tests: Matrix-Unit-Tests (9 Faelle) ohne DB; Integrationstests mit pgvector werden uebersprungen, wenn die Erweiterung fehlt (Hinweis im Log); vollstaendig in `make test-server`. |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `app/kb/__init__.py`, `app/kb/models.py`, `app/kb/repo.py`, `app/kb/retrieval.py`, `app/kb/embeddings.py`, `app/kb/policy.py` | Tabellen, Suche, Klassenmatrix |
| `alembic_kb/` (eigene Alembic-Umgebung fuer `KB_DB_URL`) oder zweiter Zweig in `alembic/env.py` mit `-x target=kb` | Migration; Entscheidung im PR begruenden |
| `app/workers/payload.py` | `context_blocks` aus `app/kb/retrieval.py` |
| `app/api/routes/kb.py`, `app/ui/routes/tasks.py` | Export |
| `scripts/kb_import.py` | Import |
| `tests/unit/test_kb_policy.py`, `tests/integration/test_kb.py`, `tests/e2e/test_kb_payload.py` | Tests |
| `docs/WISSENS_DB.md`, `docs/DATENBANK.md` | Doku |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | Matrix 3 mal 3 | genau die sechs erlaubten Kombinationen aus D3 |
| 2 | `cloud_redacted`-Auftrag, Dokument mit `local_only`-Chunks und `cloud_ok`-Chunks | nur `cloud_ok`-Chunks im Payload; `payload.sent.md` enthaelt keinen `local_only`-Text (Plan-Abnahme) |
| 3 | `cloud_ok`-Auftrag, nur `cloud_redacted`-Chunks | Payload ohne Kontext, Hinweis im Event |
| 4 | Export aus `done`, dann Laden ueber `tag` in neuem Auftrag | Roundtrip, Text identisch |
| 5 | Export aus `review` | 409 |
| 6 | Export zweimal | zweiter Versuch idempotent (kein Duplikat) |
| 7 | Chunk-Text mit Schluessel-Muster beim Import | abgelehnt (Scanner) |
| 8 | Ollama nicht erreichbar beim Embedding | Auftrag erhaelt `worker_unavailable` vor dem Lauf, kein Payload geschrieben |

## 7 Definition of Done

`make lint`, `make test` gruen; `make test-server` mit pgvector gruen; `docs/WISSENS_DB.md` mit
Matrix, Export-Regeln, Import; PR-Template mit Reviewer **Claude** und "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: Plan-Frage 4 offen ist (insbesondere Schreibrecht und ob die bestehende
Wissens-DB eine Datenklasse je Eintrag kennt); pgvector auf der Zielinstanz fehlt; die
Embedding-Dimension nicht in `router.yaml` steht.

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Falsch klassifizierte Chunks | Default `local_only`, Export nur manuell, Matrix in SQL |
| Kontext vergroessert Payload ueber `num_ctx` | Top-k-Limit, Zeichenbudget je Block (Default 6000 gesamt) |
| Zwei Datenbanken driften | eigene Migrationskette, `alembic check` fuer beide in `make test-server` |

## 10 Bewusst offen

- Plan-Frage 4: Schema und Schreibrecht der bestehenden Wissens-DB; bis dahin nur `deck_kb`.
- Embedding-Modell und Dimension (E7, Owner traegt in `router.yaml` ein).
- Chunking-Parameter; Re-Indexierung bei Modellwechsel.
- Abgleich mit Plan v1.1 bei Start der Etappe.
