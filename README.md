# Control Deck

[![CodeFactor](https://www.codefactor.io/repository/github/kaancodm/control-deck/badge/main)](https://www.codefactor.io/repository/github/kaancodm/control-deck/overview/main)

Lokale Steuerzentrale für KI-gestützte Aufgaben — ohne bezahlte APIs zur Laufzeit.
Dieses Repository enthält API, PostgreSQL-Queue, Übergabe-Schemas (`task.v1`, `result.v1`),
Abnahme, Datenschranke und lokale Pseudonymisierung.

**Status:** Etappe 11 ergänzt eine themebare Owner-Oberfläche mit Passwort-Login,
Freigaben, Reviews, sicheren Dateien und lokalem Chat mit unverbindlichen Entwürfen.
Host-Runner und Chat-Tests sind simuliert; echte Anbieteraufrufe bleiben aus.
Bedienung: [docs/UI.md](docs/UI.md). Arbeitsstand: [docs/STAND.md](docs/STAND.md).

## Zweck

Control Deck nimmt Aufträge entgegen, prüft Datenschutz- und Policy-Regeln, routet sie über eine
Worker-Fallback-Kette (Ollama lokal, Claude/Codex per CLI) und sammelt Ergebnisse in einem
standardisierten Format. Menschliche Freigabe ist standardmässig vorgesehen.

## Projektstruktur

| Pfad | Inhalt |
|------|--------|
| `app/api` | Owner-/Worker-API, Freigabe, Review und Health |
| `app/core` | Schemas, Validierung und automatische Abnahme |
| `app/db` | Persistenz, Statusübergänge, Queue und Leases |
| `app/privacy` | Scanner, lokale Blockliste und sichere Textdateien |
| `app/routing` | Reiner Router, lokale Regeln und Modellkonfiguration |
| `app/redaction` | Pseudonymisierung, lokale Maps, Payload-Bau und Versand-Artefakte |
| `app/workers` | Host-Runner, sichere Vorbereitung, Steuerung und Simulation |
| `app/ui` | Themebare Jinja-Oberfläche, Login, sichere Dateien und lokaler Chat |
| `app/services` | Gemeinsame Owner-Aktionen für API und Oberfläche |
| `schemas/` | JSON Schema Draft 2020-12 für `task.v1` und `result.v1` |
| `config/` | Router-Konfiguration ohne echte Modellnamen oder Zugangsdaten |
| `tests/` | Unit-, Integrations- und Referenztests |
| `docs/` | Architektur, Schema-Doku, Architecture Decision Records |
| `scripts/` | CLI-Hilfen (z. B. Schema-Validator) |
| `docker/`, `deploy/` | Spätere Container- und Deploy-Artefakte |

## Entwicklung

Voraussetzung: [uv](https://docs.astral.sh/uv/) und Python 3.12.

```bash
uv sync
make lint
make test
uv run python scripts/validate.py task tests/unit/examples/task_valid_01.json
```

Cloud-Aufträge benötigen eine ausdrücklich konfigurierte lokale Blockliste ausserhalb des
Repositories. Geheimnisse werden in jeder Datenklasse vor der Speicherung abgewiesen.
Konfiguration und Grenzen: [docs/DATENSCHRANKE.md](docs/DATENSCHRANKE.md).
Der lokale Payload-Builder für `cloud_redacted` ist in [docs/REDAKTION.md](docs/REDAKTION.md)
beschrieben. Der Runner liefert ausschliesslich geprüften Versandtext aus; unterstützte Treffer werden
bei `cloud_redacted` lokal ersetzt. Details: [docs/RUNNER.md](docs/RUNNER.md).

Routing und Tagesbudgets: [docs/ROUTING.md](docs/ROUTING.md).
Ollama-Simulation, Lane-Gesundheit und der explizit freigeschaltete Live-HTTP-Vertrag:
[docs/LANES.md](docs/LANES.md).
Fehlervertrag, Wartezeiten und Owner-Fehlerliste:
[docs/ETAPPEN/10-fehlerpfade.md](docs/ETAPPEN/10-fehlerpfade.md).

Weitere Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SCHEMA.md](docs/SCHEMA.md),
[docs/API.md](docs/API.md). Inhaltliche Referenzfälle schreibt der Owner; der aktuelle Bestand
und offene Platzhalter stehen in [docs/REFERENCE_CASES.md](docs/REFERENCE_CASES.md).

## Agenten

Abhängigkeiten: [DEPENDENCIES.md](DEPENDENCIES.md).
Änderungen je Etappe: [CHANGELOG.md](CHANGELOG.md).

Siehe [AGENTS.md](AGENTS.md) für Regeln für automatisierte Mitwirkende.
