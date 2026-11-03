# Etappe 17 - Audit-Ansicht, Worker-Gesundheit, Login-Ablauf-Warnung

Status: Umsetzung als gestapelter Draft-PR #32; finale Pruefnachweise in `docs/STAND.md` und im PR
Branch: `stage/17-audit-health`, Basis `stage/16-operations`; kein Agent-Merge
Abhaengig von: Etappen 9 (`lane_status`), 11 (UI), 12, 13 (Lanes), 16 (Betrieb)
Entscheidungen: keine neuen
Reviewer: Codex (ungerade Etappe)
Abnahme laut Plan v1.1: der Ablauf eines CLI-Logins wird innert eines Laufs sichtbar.

## Verbindlicher Umsetzungsstand

Audit vereint sichere Auftrags-, Betriebs- und Auth-Metadaten, keine freien Rohgruende.
CSV bleibt auf eine sichtbare Seite begrenzt. Migration `0016` zaehlt endgueltig angenommene
Ergebnisse atomar; Zaehler beginnen mit der Migration. Gesundheitsbeobachtungen und
Ausfuehrungserfolge sind getrennt. Warnband und geschuetzte Lane-Ansicht lesen gespeicherte
Beobachtungen, ohne Anbieteraufrufe. Auth-Fehler werden mit Simulationen im selben Lauf geprueft.
Das Session-Cookie gilt auch fuer den ausschliesslich lesenden Gesundheitsendpunkt;
Schreib- und Worker-APIs erhalten keine neuen Session-Rechte. Alle Live-Profile bleiben gesperrt,
Anbieter-Kontingente unbekannt. Echte Logins, Betriebsrechte und Serverpruefungen bleiben
**Inbetriebnahme**. Externes Review erfolgt durch einen anderen Anbieter.

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
| D2 | `/ui/lanes`: je Lane Zustand, sicherer Grund, Vorpruefung und angenommener Erfolg getrennt, Zaehler seit Migration 0016, letzter Fehler und Pause-Schalter. Rot bei `auth_required`, gelb bei `unavailable` oder `paused`. Unklassifizierte Fehler bleiben unbekannt. |
| D3 | Warnband auf jeder Owner-Seite bei `auth_required` oder fehlendem Erfolgsnachweis seit mindestens 24 h nach einem Claim. Text nennt Lane, Zaehlergrenze und Verweis zur Lane-Ansicht; kein erfundener historischer Nachweis. |
| D4 | Erkennung: Lanes schreiben `lane_status` **im selben Lauf**, in dem der Fehler auftritt (Etappen 9, 12, 13 liefern bereits `auth_required`); `consecutive_failures` wird im Rahmen (Etappe 8) hochgezaehlt und bei Erfolg auf 0 gesetzt. Kein zusaetzlicher Prueflauf noetig. |
| D5 | `GET /api/v1/health` (Owner-Token oder Session) liefert `db` (Verbindung, Migrationsstand), `ollama` und `lanes` (gespeicherte lokale Beobachtungen), `paused`, `stop_requested` und Zaehlergrenze. Keine aktuellen Erreichbarkeits- oder Modellproben; ohne Credentials nur `{"status": "ok"}` ohne Details und DB-Zugriff. |
| D6 | Audit zeigt ausschliesslich begrenzte Metadaten aus festen Freigabelisten. Freie Gruende, Payload-, Ausgabe- und Zugangsdaten werden nie uebernommen. |
| D7 | Tests: Integrationstest simuliert eine Lane-Antwort `auth_required` und prueft, dass die naechste UI-Antwort das Warnband traegt (Plan-Abnahme). |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `app/ui/routes.py`, Templates `audit.html`, `lanes.html`, Partial `lane_warnings.html` | geschuetzte Ansichten und Warnband |
| `app/audit/` | sichere Projektion, begrenzte SQL-Abfrage, CSV und gemeinsam genutzte Gesundheitsdaten |
| `app/db/worker_ops.py`, `app/workers/lane_status.py`, Migration `20251103_0016_lane_attempt_counters.py` | neue Felder, Zaehlen nach letztem Fencing |
| `app/api/routes/health.py` | erweitertes Health-JSON |
| `tests/integration/test_audit_views.py`, `test_audit_queries.py`, `test_attempt_health.py`, `test_audit_browser.py` | SQL-, HTTP- und Browser-Tests |
| `docs/UI.md`, `docs/API.md`, `docs/STAND.md` | Doku und Pruefnachweise |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | Lane meldet `auth_required` | `lane_status` sofort aktualisiert; naechste Seite zeigt Warnband mit Lane und Schritt |
| 2 | Drei Fehlversuche, dann Erfolg | `consecutive_failures` 3, dann 0; `last_attempt_success_at` gesetzt; Vorpruefung unveraendert |
| 3 | Audit-Filter Typ, Akteur, Zeitraum | Treffer stimmen mit DB |
| 4 | CSV-Export | Spalten, Zeilen, kein Payload-Text |
| 5 | Health ohne Auth | nur Status; mit Auth: alle Felder |
| 6 | Gespeicherte Ollama-Beobachtung `unavailable` | Health zeigt diese lokale Beobachtung; keine Live-Verfuegbarkeit behauptet |
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
| Warnband wird ignoriert | zusaetzlich Lane-Ansicht und geschuetzter Health-Endpunkt |
| Audit-Liste waechst | Seitenweise, Index auf `created_at`, `task_id` |

## 10 Bewusst offen

- Benachrichtigungskanal ausserhalb der UI (optional Etappe 19).
- Aufbewahrung der Events bleibt unbegrenzt (ADR 0003); Archivierung spaeter.
- Inbetriebnahme: echte Login-Ablauf- und Live-Lane-Nachweise, Betriebsrechte und Gesamt-Abnahme.
