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
| Entwicklung | Claude plant (Fable ueber `/advisor`); Cursor baut; Claude baut nur komplexe Auftraege, wenn der Auftrag das ausdruecklich sagt (Opus 5.5, Effort medium); Gemini Pro ist Lead Full-Stack Developer und Pair-Programming-Partner, gesteuert von Grok Bot, ohne Merge-Recht und nicht Security-Head; nur Grok Bot mergt nach einmaligem Ja des Owners (ADR 0008, Nachtrag 2026-11-12). If anything conflicts, AGENTS.md wins. |

Details: `docs/DECISIONS/0001-*.md` bis `0008-*.md`.

## Datenfluss (10 Schritte)

1. **Anlegen und Validierung** — UI, API, n8n oder System erzeugt `task.v1`. Pflichtfelder inkl. `data_class` werden gegen JSON Schema und Pydantic geprueft. Ungueltige Auftraege werden abgewiesen.
2. **Datenschranke** — `app/privacy` scannt Inhalte, Metadaten und sichere Textdateien vor Persistenz. Geheimnisse werden in jeder Datenklasse ohne Speicherung abgewiesen. Nicht geheime Treffer sind in `local_only` zulaessig; Bei `cloud_redacted` erlaubt die sichere Runner-Anbindung unterstuetzte Treffer; andere Cloud-Treffer bleiben `blocked`. Vor Freigabe und Lease werden veraenderliche Dateien und Blockliste erneut geprueft. Der Scanner verwendet eine NFKC-Schattenkopie, meldet aber Originalpositionen. Details: [DATENSCHRANKE.md](DATENSCHRANKE.md).
3. **Router** — Pure Funktion berechnet effektive Worker-Kette aus `fallback_chain`, `policy` und `data_class`. Regeln, Modelle und lokale Tagesbudgets schraenken die Auswahl weiter ein. Das Ergebnis wird neben dem unveraenderlichen Task in `plan` und `lane` persistiert. Ein reiner Dry-run reserviert nichts; atomare Budgetpruefung erfolgt beim Claim. Details: [ROUTING.md](ROUTING.md).
4. **Runner-Lane (Pull)** — Worker-Prozesse ziehen freigegebene `queued`-Tasks per Lease aus Postgres (`SKIP LOCKED`). Lease TTL und Heartbeat verhindern Doppelarbeit.
5. **Payload bauen** — Etappe 6 stellt `app/redaction` bereit: vollstaendigen Text rendern, bei `cloud_redacted` unterstuetzte Angaben durch stabile taskgebundene Platzhalter ersetzen, erneut scannen und `payload.sent.md` mit SHA-256 privat und unveraenderlich ablegen. Maps bleiben lokal in `redaction_maps`; der Builder verweigert Cloud-Lanes fuer `local_only`. Der Host-Runner bindet Vorbereitung an Versuch, Lane und Lease; Freigabe-Dateihashes und committed Maps sind Pflicht. Kontext aus `context_refs` wird erst in Etappe 14 klassengefiltert geladen; bis dahin wird er abgewiesen. Details: [REDAKTION.md](REDAKTION.md).
6. **Ausfuehrung** — Reinraum-Container (rootless Docker) oder Ollama-HTTP je nach Worker und `data_class`. Keine bezahlten APIs zur Laufzeit.
7. **Heartbeat / Timeout / Abbruch** — Runner meldet Fortschritt; Ueberschreitung von `timeout_s` oder Operator-Abbruch (`cancel_requested`) beendet den Versuch.
8. **Ergebnis einsammeln** — stdout/stderr, Artefakte und Metriken werden zu `result.v1` zusammengefuehrt. Der lokale Rueckabbildungsbaustein aus Etappe 6 loest nur bekannte Platzhalter desselben Tasks auf. Der Host bildet Ergebnisse vor Abnahme lokal zurueck und prueft Identitaet und Vorbereitung erneut.
9. **Validierung und Abnahme** — `result.v1` wird validiert; `app/core/acceptance.py` wertet die Regeln gegen `output.text` aus und ersetzt unbestaetigte Worker-Checks. Automatische Fehlschlaege (`acceptance_failed`) fuehren ohne Retry nach `review`; der Owner entscheidet.
10. **Freigabe und Review** — Owner-Freigabe vor Ausfuehrung (`awaiting_approval` → `queued`). Nach erfolgreicher Abnahme folgt `review`, dann Owner-Entscheid nach `done` oder `failed`. Nur `policy.require_review = false` ohne `human`-Regel erlaubt direkt `done`. Export in die Wissens-DB bleibt eine spaetere Etappe (ADR 0007).

## Task-Zustaende

Massgeblich ist die Implementierung in `app/db/state_machine.py` (PostgreSQL-Enum `task_state` per Migration).

| Zustand | Bedeutung |
|---------|-----------|
| `awaiting_approval` | Validiert, wartet auf Owner-Freigabe (HTTP-API) |
| `queued` | Freigegeben, wartet auf Lease |
| `blocked` | Datenschranke oder Policy |
| `leased` | Runner haelt Lease, Start steht aus |
| `running` | Ausfuehrung aktiv (Heartbeat) |
| `review` | Automatische Abnahme bestanden oder mit `acceptance_failed` abgelehnt; wartet auf Owner-Entscheid |
| `done` | Erfolgreich abgeschlossen, gegebenenfalls nach menschlicher Abnahme |
| `expired` | Explizite Owner-Deadline erreicht; terminal |
| `succeeded` | Historischer terminaler Status; kein Ziel neuer erfolgreicher Versuche |
| `failed` | Kette erschoepft oder nicht behebbarer Fehler |
| `cancelled` | Abgebrochen (Operator oder Runner) |

`deadline_at` ist optionale, vom Owner beim Anlegen gesetzte API-/DB-Metadaten und kein Feld des eingefrorenen `task.v1`. Nur diese Deadline kann `expired` ausloesen. Ohne Deadline gibt es keinen Gesamt-TTL. `policy.timeout_s` und Lease-Ablauf betreffen einen Versuch und sind kein Task-Ablauf.

### Erlaubte Uebergaenge (Auszug)

| Von | Nach | Ausloeser |
|-----|------|-----------|
| — | `awaiting_approval` | API submit |
| `awaiting_approval` | `queued` | Owner approve |
| `awaiting_approval` / `queued` | `blocked` | Datenschranke (`block`) |
| `queued` | `leased` | Runner acquire |
| `leased` | `running` | Runner start |
| `leased` | `queued` | Lease abgelaufen (solange `attempt < max_attempts`) |
| `leased` | `failed` | Lease abgelaufen bei erschoepften Versuchen |
| `running` | `review` | Erfolgreiche automatische Abnahme; Review-Pflicht oder `human`-Regel |
| `running` | `review` | `acceptance_failed` (Event `task.review`), kein Retry |
| `running` | `done` | Erfolgreiche Abnahme, `require_review = false`, keine `human`-Regel |
| `running` | `failed` / `queued` | Fehler / Retry, begrenzt durch `max_attempts` |
| `review` | `done` / `failed` | Owner `review_approve` / `review_reject` |
| `review` | `cancelled` | Owner cancel |
| `awaiting_approval` / `queued` / `leased` / `running` / `review` | `expired` | Explizite Owner-Deadline erreicht |
| `*` (pre-run) | `cancelled` | Operator cancel |
| `leased` / `running` | `cancelled` | Operator `cancel_requested` + Runner/Reaper |

Worker-Uebergaenge mit Lease nutzen atomisches SQL-Fencing (`status`, `lease_id`, Lease-Ablauf, Owner-Deadline). Die Ablaufpruefung bei API-/Queue-Zugriffen verwendet die DB-Zeit, entfernt Leases und erzeugt `task.expire`. Bereits terminale Tasks werden nicht nachtraeglich auf `expired` gesetzt.
