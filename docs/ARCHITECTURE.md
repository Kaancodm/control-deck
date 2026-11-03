# Architektur (Control Deck)

Dieses Dokument beschreibt die Zielarchitektur und Leitentscheide fuer Control Deck.

## Leitentscheide (Kurz)

| Thema | Entscheid |
|-------|-----------|
| Queue | PostgreSQL mit `FOR UPDATE SKIP LOCKED` (ADR 0001) |
| Prozesse | Kern auf Host (systemd), Ausfuehrung im Reinraum-Container (ADR 0002) |
| Auftrag | Unveraenderlich; Laufzeit in `runs/` und Events (ADR 0003) |
| Dispatch | Pull mit Lease und Heartbeat; Router als Funktion (ADR 0004) |
| Datenschutz | `data_class` Pflicht; Schranken vor Routing (ADR 0005) |
| Worker | Nur offizielle CLIs, keine API-Keys (ADR 0006) |
| Freigabe | Mensch als letzte Instanz; Wissens-DB nach GO (ADR 0007) |
| Entwicklung | Builder Cursor; Review anderer Anbieter (ADR 0008) |

Details: `docs/DECISIONS/0001-*.md` bis `0008-*.md`.

## Datenfluss (10 Schritte)

1. **Anlegen und Validierung** — UI, API, n8n oder System erzeugt `task.v1`. Pflichtfelder inkl. `data_class` werden gegen JSON Schema und Pydantic geprueft. Ungueltige Auftraege werden abgewiesen.
2. **Datenschranke** — Vor Persistenz scannt die Schranke Inhalte und Metadaten. Treffer gegen Blocklisten oder Policy fuehren zu `blocked` statt Queue-Eintrag (oder sofortigem Abbruch).
3. **Router** — Pure Funktion berechnet effektive Worker-Kette aus `fallback_chain`, `policy` und `data_class`. Ergebnis wird mit dem unveraenderlichen Task persistiert (`plan`, `lane` — spaetere Etappen).
4. **Runner-Lane (Pull)** — Worker-Prozesse ziehen freigegebene `queued`-Tasks per Lease aus Postgres (`SKIP LOCKED`). Lease TTL und Heartbeat verhindern Doppelarbeit.
5. **Payload bauen** — Kontext aus `context_refs` wird klassengefiltert geladen. Bei `cloud_redacted` Pseudonymisierung; bei `local_only` kein Cloud-Worker. `payload.sent.md` wird revisionssicher abgelegt.
6. **Ausfuehrung** — Reinraum-Container (rootless Docker) oder Ollama-HTTP je nach Worker und `data_class`. Keine bezahlten APIs zur Laufzeit.
7. **Heartbeat / Timeout / Abbruch** — Runner meldet Fortschritt; Ueberschreitung von `timeout_s` oder Operator-Abbruch (`cancel_requested`) beendet den Versuch.
8. **Ergebnis einsammeln** — stdout/stderr, Artefakte und Metriken werden zu `result.v1` zusammengefuehrt; Rueckabbildung von Redaction-Platzhaltern wo noetig.
9. **Validierung und Abnahme** — `result.v1` wird validiert; `acceptance`-Regeln werden ausgewertet. Retry bis `max_attempts`, danach `failed`.
10. **Freigabe und Export** — HTTP-API: Owner-Freigabe (`awaiting_approval` → `queued`). `policy.require_review` steuert spaetere Review-Etappen; Export in die Wissens-DB nach GO (ADR 0007).

## Task-Zustaende (Plan v1.1, Etappen 3–4)

Massgeblich ist die Implementierung in `app/db/state_machine.py` (PostgreSQL-Enum `task_state` per Migration).

| Zustand | Bedeutung |
|---------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe (HTTP-API) |
| `queued` | Freigegeben, wartet auf Lease |
| `blocked` | Datenschranke oder Policy |
| `leased` | Runner haelt Lease, Start steht aus |
| `running` | Ausfuehrung aktiv (Heartbeat) |
| `succeeded` | Erfolgreich abgeschlossen (Review-/Export-Etappen folgen) |
| `failed` | Kette erschoepft oder nicht behebbarer Fehler |
| `cancelled` | Abgebrochen (Operator oder Runner) |

Geplant fuer spaetere Etappen (noch nicht im Enum): `review`, `done`, `expired` gemaess Gesamt-TTL und menschlicher Abnahme nach Erfolg — siehe Plan v1.1 Abschnitt 5.

### Erlaubte Uebergaenge (Auszug)

| Von | Nach | Ausloeser |
|-----|------|-----------|
| — | `awaiting_approval` | API submit |
| — | `queued` | System enqueue (interne Pfade) |
| — | `blocked` | Schranke / Policy |
| `awaiting_approval` | `queued` | Owner approve |
| `queued` | `leased` | Runner acquire |
| `leased` | `running` | Runner start |
| `leased` | `queued` | Lease abgelaufen (solange `attempt < max_attempts`) |
| `leased` | `failed` | Lease abgelaufen bei erschoepften Versuchen |
| `running` | `succeeded` / `failed` / `queued` | Ergebnis / Retry |
| `*` (pre-run) | `cancelled` | Operator cancel |
| `leased` / `running` | `cancelled` | Operator `cancel_requested` + Runner/Reaper |

Worker-Uebergaenge mit Lease nutzen atomisches SQL-Fencing (`status`, `lease_id`, Lease-Ablauf).
