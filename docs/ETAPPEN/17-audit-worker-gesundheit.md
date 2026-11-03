# Etappe 17 - Audit-Ansicht, Worker-Gesundheit, Login-Ablauf-Warnung

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 16 gemergt ist
Branch-Vorschlag: `stage/17-audit-gesundheit`
Abhaengig von: Etappen 9 (`lane_status`), 11 (UI), 12, 13 (Lanes), 16 (Betrieb)
Entscheidungen: keine neuen
Reviewer: Codex (ungerade Etappe)
Abnahme laut Plan v1.1: der Ablauf eines CLI-Logins wird innert eines Laufs sichtbar.

## 1 Ziel

Der Owner sieht ohne CLI, was passiert ist und ob die Lanes gesund sind: eine Audit-Ansicht ueber
alle Events, je Lane den Zustand mit letztem Erfolg und Fehlerserie, ein Warnband bei abgelaufenem
Login, und einen erweiterten Gesundheitsendpunkt fuer den Betrieb.

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| `events` mit `actor`, `attempt`, `lease_id` | PR #5 (Annahme), VB-3 |
| `lane_status` mit `ok`, `unavailable`, `auth_required`, `paused`, `last_success_at` | Etappe 9 |
| `settings.lanes.<lane>.paused` | Etappe 8 |
| Health-Route in `app/api/routes/health.py` | PR #5 (Annahme) |
| UI-Grundgeruest mit Statusband | Etappe 11 |

## 3 Scope

**In:** `/ui/audit`, `/ui/lanes`, Warnband, Felder `consecutive_failures`, `last_error_code`,
`last_error_at` in `lane_status`, Health-JSON mit DB, Ollama, Lanes, CSV-Export der Audit-Liste,
Tests, `docs/UI.md`.

**Out:** Benachrichtigung per Mail oder Chat (Plan: "Owner wird informiert" heisst in v1 UI-Band;
Kanal ist optional Etappe 19 ueber n8n), Metriken-Server, Logdatei-Ansicht (Lane-Logs bleiben auf
Platte, Verweis ueber `log_ref`).

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | `/ui/audit`: alle Events, Filter nach Typ, Akteur, Lane, Zeitraum, Task; 100 je Seite; Export als CSV (`text/csv`, nur sichtbare Spalten, keine Payloads). |
| D2 | `/ui/lanes`: je Lane Zustand, Grund, `last_success_at`, `consecutive_failures`, letzter Fehlercode, Pause-Schalter (Etappe 11); Zeile rot bei `auth_required`, gelb bei `unavailable` oder `paused`. |
| D3 | Warnband auf jeder Seite, wenn eine Lane `auth_required` oder seit mehr als 24 h ohne Erfolg bei mindestens einem Versuch ist; Text nennt Lane und Handbuch-Schritt (`docs/OPERATIONS.md`). |
| D4 | Erkennung: Lanes schreiben `lane_status` **im selben Lauf**, in dem der Fehler auftritt (Etappen 9, 12, 13 liefern bereits `auth_required`); `consecutive_failures` wird im Rahmen (Etappe 8) hochgezaehlt und bei Erfolg auf 0 gesetzt. Kein zusaetzlicher Prueflauf noetig. |
| D5 | `GET /api/v1/health` (Owner-Token oder Session) liefert `db` (Verbindung, Migrationsstand), `ollama` (erreichbar, Modelle vorhanden), `lanes` (Zustand je Lane), `paused`; ohne Auth nur `{"status": "ok"}` ohne Details. |
| D6 | Audit-Ansicht zeigt nie Payload- oder Ausgabetext, nur Metadaten; Begruendungen (`reason`) werden angezeigt, da vom Owner selbst erfasst. |
| D7 | Tests: Integrationstest simuliert eine Lane-Antwort `auth_required` und prueft, dass die naechste UI-Antwort das Warnband traegt (Plan-Abnahme). |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `app/ui/routes/audit.py`, `app/ui/routes/lanes.py`, Templates `audit.html`, `lanes.html`, Teil-Template `warnband.html` | Ansichten |
| `app/workers/lane_status.py`, Migration `<datum>_000N_lane_status_counters.py` | neue Felder |
| `app/api/routes/health.py` | erweitertes Health-JSON |
| `tests/integration/test_audit_ui.py`, `tests/integration/test_lane_health.py` | Tests |
| `docs/UI.md`, `docs/OPERATIONS.md` | Doku |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | Lane meldet `auth_required` | `lane_status` sofort aktualisiert; naechste Seite zeigt Warnband mit Lane und Schritt |
| 2 | Drei Fehlversuche, dann Erfolg | `consecutive_failures` 3, dann 0; `last_success_at` gesetzt |
| 3 | Audit-Filter Typ, Akteur, Zeitraum | Treffer stimmen mit DB |
| 4 | CSV-Export | Spalten, Zeilen, kein Payload-Text |
| 5 | Health ohne Auth | nur Status; mit Auth: alle Felder |
| 6 | Ollama nicht erreichbar | Health `ollama.ok = false`, Lane `unavailable` |
| 7 | Leck-Test Audit-Ansicht | kein Payload- oder Ausgabetext |

## 7 Definition of Done

`make lint`, `make test` gruen; Smoke-Test durch den Owner (Login einer Lane absichtlich ablaufen
lassen oder simulieren, Warnband sichtbar); PR-Template mit Reviewer **Codex** und "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: `lane_status` oder `events.actor` fehlen; Etappe 8 keinen Zaehler fuer
Fehlserien zulaesst (dann Zaehler hier nachziehen und im PR vermerken, kein Stopp).

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Warnband wird ignoriert | zusaetzlich Zeile in `make status` (Etappe 16) und Health-Endpunkt |
| Audit-Liste waechst | Seitenweise, Index auf `created_at`, `task_id` |

## 10 Bewusst offen

- Benachrichtigungskanal ausserhalb der UI (optional Etappe 19).
- Aufbewahrung der Events bleibt unbegrenzt (ADR 0003); Archivierung spaeter.
- Abgleich mit Plan v1.1 bei Start der Etappe.
