# Etappe 12 - Reinraum-Image und Lane Claude

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 11 gemergt ist
Branch-Vorschlag: `stage/12-reinraum-lane-claude`
Abhaengig von: VB-1 bis VB-3, Etappen 6, 8, 9, 10, 11; E6; Plan-Frage 5 (Policy-Restrisiko, Extra Usage aus)
Entscheidungen: E6, E7
Reviewer: Claude (gerade Etappe)
Abnahme laut Plan v1.1: Headless-Auftrag kommt als `result.v1` zurueck; eine Ablehnung durch den
Anbieter wird sauber als `policy_blocked` erkannt.

## 1 Ziel

Erste Cloud-Lane. Jeder Versuch laeuft in einem Wegwerf-Container (rootless Docker), der nur den
freigegebenen Payload, das Credentials-Volume des CLI und ausgehendes Internet sieht: keine
Host-Daten, kein Repo, kein DB-Zugang (ADR 0002, Plan-Leitentscheid 3). Das offizielle CLI wird
unveraendert mit `claude -p` aufgerufen (ADR 0006).

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| `docker/` enthaelt nur `.gitkeep` | belegt |
| `AttemptContext`, `LaneExecutor`, `lane_status`, `settings` | Vertraege README (Etappen 8, 9) |
| Payload liegt redigiert als `payload.sent.md` im Laufverzeichnis | Etappe 6 |
| Fehlerreaktion (Lane pausiert bei `policy_blocked`, markiert sich bei `auth_required`) | Etappe 10 |
| Plan Abschnitt 8: Drittanbieter-Harnesse brauchen seit April 2026 "Extra Usage"; Ablehnungen von `claude -p` mit fremdem Kontext sind berichtet | Plan v1.1, Annahme ohne eigene Pruefung |

## 3 Scope

**In:** Image `docker/lane-claude/`, Container-Start aus dem Runner, `ClaudeExecutor`, Ausgabe-Parser,
Fehlerzuordnung, Credentials-Volume und Login-Handbuch, Tests mit Fake-CLI, `docs/LANES.md` (Claude).

**Out:** Lane Codex (13), Dateiausgaben des Containers (`output.files` bleibt leer, siehe 10),
Aenderungen am CLI oder an dessen Konfiguration, API-Keys, Abrechnungs-Optionen des Kontos.

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | Basis-Image per Digest festgenagelt; CLI-Version festgenagelt und in `docker/lane-claude/VERSION` dokumentiert; Nutzer `lane` (UID 10001), kein Root, kein Paketmanager im Endimage. |
| D2 | Start: `docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory <aus router.yaml lanes.claude.limits, Default 2g> --cpus 2 --tmpfs /work --mount type=bind,src=<run_dir>/payload.sent.md,dst=/in/payload.sent.md,readonly --mount type=volume,src=deck-claude-creds,dst=/home/lane/.claude`. Standard-Bridge-Netz: ausgehendes Internet ja, Host-Dienste nicht erreichbar (E6). Keine `DECK_*`-Variablen im Container. |
| D3 | Einstieg: Skript liest `/in/payload.sent.md` und uebergibt ihn per Standardeingabe an `claude -p --output-format json --max-turns <N>` mit `--model <aus router.yaml lanes.claude.models.<kind> oder .default>`; `N` Default 1. Werkzeuge im CLI deaktiviert (keine Datei- oder Shell-Aktionen); die exakten Flag-Namen werden gegen `--help` der festgenagelten Version geprueft und in `docs/LANES.md` festgehalten. |
| D4 | Rootless-Pflicht: `docker info` muss rootless melden, sonst startet die Lane nicht (`lane_status = unavailable`, Grund). |
| D5 | Timeout: `min(policy.timeout_s, lanes.claude.timeout_s)`; Ablauf → `docker kill`, `error.code = timeout`. `cancel_requested` beim Heartbeat → `docker kill`, `status = cancelled`. |
| D6 | Fehlerzuordnung aus Exit-Code, JSON-Feldern und stderr-Mustern: nicht angemeldet oder 401 → `auth_required` (Lane setzt `lane_status = auth_required`, kein weiterer Pull); 400 oder Fehlertext zu Nutzungsbedingungen, Harness, Extra Usage → `policy_blocked` (Lane setzt `settings.lanes.claude.paused = true`, Event, UI-Band); 429 → `rate_limited`; Container-OOM oder anderer Exit → `internal`. Muster liegen in einer Tabelle `app/workers/lanes/claude_errors.py` mit Tests. |
| D7 | Modell nur aus `config/router.yaml`; es duerfen nur Modelle eingetragen sein, die im Abo enthalten sind (Owner-Pflicht, Hinweis in `docs/LANES.md`); kein Modellname im Code (E7). |
| D8 | Login einmalig durch den Owner: `make lane-login LANE=claude` startet den Container interaktiv mit dem Credentials-Volume und ruft den Login des CLI auf. Kein Token im Repo, in `.env` oder in Logs. "Extra Usage" bleibt im Konto aus (Plan). |
| D9 | Ergebnis: `output.text` aus dem JSON-Feld der Antwort; `usage` aus den Nutzungsfeldern, falls vorhanden; `log_ref` auf `claude.log` (JSON pro Zeile: Phase, Exit-Code, Dauer, Modell, Vorlagen-Hash; nie Prompt oder Antwort). `validate_result` vor Rueckgabe. |
| D10 | Tests ohne Docker ueber `command_prefix`-Injektion: ein Fake-CLI (`tests/e2e/fake_claude_cli.py`) erzeugt die JSON-Ausgaben der Faelle; Docker-Tests mit Marker `docker` werden ohne rootless Docker uebersprungen; realer Lauf nur mit `DECK_E2E_CLAUDE=1` in `make test-server`. |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `docker/lane-claude/Dockerfile`, `entrypoint.sh`, `VERSION` | Image |
| `app/workers/sandbox.py` | `run_in_cleanroom(image, run_dir, volume, limits, timeout, cancel) -> SandboxResult` (stdout, stderr, exit, dauer) |
| `app/workers/lanes/claude.py`, `claude_errors.py` | Executor, Zuordnung |
| `app/workers/prompts/claude_v1.md` | Vorlage (Daten-Markierung, Platzhalter-Hinweis) |
| `Makefile` | `lane-image LANE=claude`, `lane-login LANE=claude` |
| `tests/unit/test_claude_executor.py`, `tests/e2e/test_claude_lane.py`, `tests/e2e/fake_claude_cli.py` | Tests |
| `docs/LANES.md`, `.env.example` | Doku, `DECK_E2E_CLAUDE` |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | Fake-CLI liefert gueltige JSON-Antwort | `result.v1` valide, `status = succeeded`, `payload_hash` aus Kontext |
| 2 | Fake-CLI simuliert Ablehnung (400, Policy-Text) | `policy_blocked`; `settings.lanes.claude.paused = true`; Event; kein weiterer Pull |
| 3 | Fake-CLI simuliert fehlenden Login | `auth_required`; `lane_status = auth_required` |
| 4 | Fake-CLI haengt | `timeout`, Prozess beendet |
| 5 | `cancel_requested` waehrend des Laufs | `cancelled`, Container beendet |
| 6 | Docker (Marker): im Container ist das Root-Dateisystem schreibgeschuetzt, keine `DECK_*`-Variable, Verbindung zu einem Host-Port scheitert, `/in/payload.sent.md` nicht beschreibbar | alle vier geprueft |
| 7 | `docker info` nicht rootless | Lane startet nicht, `unavailable` |
| 8 | Log-Leck-Test | kein Prompt- oder Antworttext im Log |
| 9 | Server (`DECK_E2E_CLAUDE=1`): ein `cloud_redacted`-Referenzfall ueber die Lane | `result.v1` zurueck, Platzhalter unveraendert, Protokoll im PR |

## 7 Definition of Done

`make lint`, `make test` gruen; Docker-Tests lokal mit rootless Docker gruen (Protokoll); Serverlauf
durch den Owner; `docs/LANES.md` mit Login-Schritt, Flag-Tabelle, Fehlerzuordnung; PR-Template mit
Reviewer **Claude** und "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: kein rootless Docker auf dem Zielsystem bestaetigt ist; Plan-Frage 5
unbeantwortet ist; das festgenagelte CLI keinen nicht-interaktiven JSON-Modus bietet; E6 anders
entschieden wird; Etappe 10 die Lane-Pause nicht umsetzt.

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Anbieter-Policy aendert sich, Aufrufe werden abgelehnt | `policy_blocked` pausiert die Lane, Kette faellt auf Codex oder Ollama zurueck |
| Login laeuft ab | `auth_required`, Etappe 17 zeigt Warnung, Handbuch-Schritt |
| Container erreicht Host | Standard-Bridge, Test 6, keine Host-Mounts ausser Payload |
| Kosten durch separat abgerechnete Modelle | nur Abo-Modelle in `router.yaml`, Extra Usage aus |

## 10 Bewusst offen

- Plan-Frage 5: akzeptiert der Owner das Policy-Restrisiko; Extra Usage bleibt aus.
- `output.files` fuer die Claude-Lane (Dateien aus `/work`): nicht in v1.
- Standardwert `--max-turns` und ob Werkzeuge je `kind` erlaubt werden.
- Abgleich mit Plan v1.1 bei Start der Etappe (Flag-Namen, CLI-Version).
