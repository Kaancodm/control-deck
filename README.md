# Control Deck

Lokale Steuerzentrale fuer KI-gestuetzte Aufgaben — ohne bezahlte APIs zur Laufzeit.
Dieses Repository enthaelt API, PostgreSQL-Queue, Uebergabe-Schemas (`task.v1`, `result.v1`),
Abnahme, Datenschranke und lokale Pseudonymisierung.

**Status:** Etappe 07 ergaenzt den reinen Router, lokale Regeln, Modellkonfiguration,
Tagesbudgets und Dry-run. Der Host-Runner folgt in Etappe 8. Aktueller Arbeitsstand: [docs/STAND.md](docs/STAND.md).

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
| `app/routing` | Reiner Router, lokale Regeln und Modellkonfiguration |
| `app/redaction` | Pseudonymisierung, lokale Maps, Payload-Bau und Versand-Artefakte |
| `app/ui`, `app/workers` | Oberflaeche und Worker-Laufzeit (Platzhalter) |
| `schemas/` | JSON Schema Draft 2020-12 fuer `task.v1` und `result.v1` |
| `config/` | Router-Konfiguration ohne echte Modellnamen oder Zugangsdaten |
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
Der lokale Payload-Builder fuer `cloud_redacted` ist in [docs/REDAKTION.md](docs/REDAKTION.md)
beschrieben. Bis zur sicheren Runner-Anbindung bleiben Cloud-Treffer an den bestehenden
HTTP-/Queue-Grenzen gesperrt.

Routing und Tagesbudgets: [docs/ROUTING.md](docs/ROUTING.md).

Weitere Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SCHEMA.md](docs/SCHEMA.md),
[docs/API.md](docs/API.md). Inhaltliche Referenzfaelle schreibt der Owner; der aktuelle Bestand
und offene Platzhalter stehen in [docs/REFERENCE_CASES.md](docs/REFERENCE_CASES.md).

## Agenten

Siehe [AGENTS.md](AGENTS.md) fuer Regeln fuer automatisierte Mitwirkende.
