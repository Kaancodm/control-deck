# Control Deck

Lokale Steuerzentrale fuer KI-gestuetzte Aufgaben — ohne bezahlte APIs zur Laufzeit.
Dieses Repository enthaelt API, PostgreSQL-Queue, Uebergabe-Schemas (`task.v1`, `result.v1`),
Abnahme und Datenschranke.

**Status:** Etappe 05 prueft Auftraege und Textdateien vor Speicherung, Freigabe und Lease.
Status-Review und Owner-Deadlines sind vorhanden; Redaction, Routing und Worker-Laufzeit folgen.

## Zweck

Control Deck nimmt Auftraege entgegen, prueft Datenschutz- und Policy-Regeln, routet sie ueber eine
Worker-Fallback-Kette (Ollama lokal, Claude/Codex per CLI) und sammelt Ergebnisse in einem
standardisierten Format. Menschliche Freigabe ist standardmaessig vorgesehen.

## Projektstruktur

| Pfad | Inhalt |
|------|--------|
| `app/api` | Owner-/Worker-API, Freigabe, Review und Health |
| `app/core` | Schemas, Validierung und automatische Abnahme |
| `app/db` | Persistenz, Statusuebergaenge, Queue und Leases |
| `app/privacy` | Scanner, lokale Blockliste und sichere Textdateien |
| `app/ui`, `app/workers` | Oberflaeche und Worker-Laufzeit (Platzhalter) |
| `schemas/` | JSON Schema Draft 2020-12 fuer `task.v1` und `result.v1` |
| `config/` | Konfiguration (Platzhalter) |
| `tests/` | Unit-, Integrations- und Referenztests |
| `docs/` | Architektur, Schema-Doku, Architecture Decision Records |
| `scripts/` | CLI-Hilfen (z. B. Schema-Validator) |
| `docker/`, `deploy/` | Spaetere Container- und Deploy-Artefakte |

## Entwicklung

Voraussetzung: [uv](https://docs.astral.sh/uv/) und Python 3.12.

```bash
uv sync
make lint
make test
uv run python scripts/validate.py task tests/unit/examples/task_valid_01.json
```

Cloud-Auftraege benoetigen eine ausdruecklich konfigurierte lokale Blockliste ausserhalb des
Repositories. Geheimnisse werden in jeder Datenklasse vor der Speicherung abgewiesen.
Konfiguration und Grenzen: [docs/DATENSCHRANKE.md](docs/DATENSCHRANKE.md).

Weitere Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SCHEMA.md](docs/SCHEMA.md),
[docs/API.md](docs/API.md). Inhaltliche Referenzfaelle schreibt der Owner; der aktuelle Bestand
und offene Platzhalter stehen in [docs/REFERENCE_CASES.md](docs/REFERENCE_CASES.md).

## Agenten

Siehe [AGENTS.md](AGENTS.md) fuer Regeln fuer automatisierte Mitwirkende.
