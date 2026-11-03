# Architektur (Control Deck)

Dieses Dokument beschreibt die Zielarchitektur und Leitentscheide fuer Control Deck.
Die Etappe `01-scaffold-schema` liefert nur Schemas, Validierung und Doku — noch keine Laufzeit.

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
3. **Router** — Pure Funktion berechnet effektive Worker-Kette aus `fallback_chain`, `policy` und `data_class`. Ergebnis wird mit dem unveraenderlichen Task persistiert.
4. **Runner-Lane (Pull)** — Worker-Prozesse ziehen Auftraege per Lease aus Postgres (`SKIP LOCKED`). Lease TTL und Heartbeat verhindern Doppelarbeit.
5. **Payload bauen** — Kontext aus `context_refs` wird klassengefiltert geladen. Bei `cloud_redacted` Pseudonymisierung; bei `local_only` kein Cloud-Worker. `payload.sent.md` wird revisionssicher abgelegt.
6. **Ausfuehrung** — Reinraum-Container (rootless Docker) oder Ollama-HTTP je nach Worker und `data_class`. Keine bezahlten APIs zur Laufzeit.
7. **Heartbeat / Timeout / Abbruch** — Runner meldet Fortschritt; Ueberschreitung von `timeout_s` oder Not-Aus beendet den Versuch.
8. **Ergebnis einsammeln** — stdout/stderr, Artefakte und Metriken werden zu `result.v1` zusammengefuehrt; Rueckabbildung von Redaction-Platzhaltern wo noetig.
9. **Validierung und Abnahme** — `result.v1` wird validiert; `acceptance`-Regeln werden ausgewertet. `review` oder `failed` → naechstes Kettenglied bis `max_attempts`.
10. **Freigabe und Export** — Bei `require_review` wartet der Task auf menschliches GO. Erst danach optional Export in die Wissens-DB (Eintraege default `local_only`).

## Task-Zustaende

| Zustand | Bedeutung |
|---------|-----------|
| `queued` | Validiert, wartet auf Lease |
| `blocked` | Datenschranke oder Policy verhindert Ausfuehrung |
| `leased` | Ein Runner haelt Lease, noch nicht gestartet |
| `running` | Ausfuehrung aktiv (Heartbeat pflicht) |
| `review` | Ergebnis vorliegt, menschliche Freigabe offen |
| `done` | Erfolgreich abgeschlossen (ggf. nach Review) |
| `failed` | Kette erschoepft oder nicht behebbarer Fehler |
| `cancelled` | Manuell oder Not-Aus abgebrochen |
| `expired` | Lease oder Gesamt-TTL abgelaufen ohne Abschluss |

### Erlaubte Uebergaenge (Auszug)

| Von | Nach | Ausloeser |
|-----|------|-----------|
| — | `queued` | Validierung OK, Schranke OK |
| — | `blocked` | Schranke / Policy |
| `queued` | `leased` | Runner acquire |
| `leased` | `queued` | Lease abgelaufen ohne Start |
| `leased` | `running` | Runner start |
| `running` | `review` | Erfolg + `require_review` |
| `running` | `done` | Erfolg ohne Review-Pflicht |
| `running` | `queued` | Fehlversuch, naechster Worker |
| `running` | `failed` | Kette erschoepft |
| `*` | `cancelled` | Operator / Not-Aus |
| `leased`, `running` | `expired` | TTL ueberschritten |

Vollstaendige Orchestrierung folgt in spaeteren Etappen.
