# Control Deck

Lokale Steuerzentrale fuer KI-gestuetzte Aufgaben — ohne bezahlte APIs zur Laufzeit.
Dieses Repository enthaelt das Grundgeruest, Uebergabe-Schemas (`task.v1`, `result.v1`) und Validierung.

**Status:** FastAPI-API (Etappe 04) und PostgreSQL-Queue vorhanden; Worker-Laufzeit folgt in spaeteren Etappen.

## Zweck

Control Deck nimmt Auftraege entgegen, prueft Datenschutz- und Policy-Regeln, routet sie ueber eine
Worker-Fallback-Kette (Ollama lokal, Claude/Codex per CLI) und sammelt Ergebnisse in einem
standardisierten Format. Menschliche Freigabe ist standardmaessig vorgesehen.

## Projektstruktur

| Pfad | Inhalt |
|------|--------|
| `app/api`, `app/ui` | Geplante HTTP- und UI-Schicht (Platzhalter) |
| `app/core` | Schemas, Validierung, kuenftige Router-Logik |
| `app/db`, `app/workers` | Persistenz und Worker-Anbindung (Platzhalter) |
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

Weitere Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SCHEMA.md](docs/SCHEMA.md).

## Agenten

Siehe [AGENTS.md](AGENTS.md) fuer Regeln fuer automatisierte Mitwirkende.
