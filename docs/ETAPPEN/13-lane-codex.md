# Etappe 13 - Lane Codex

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 12 gemergt ist
Branch-Vorschlag: `stage/13-lane-codex`
Abhaengig von: Etappe 12 (Reinraum, `run_in_cleanroom`), VB-6 (Pfadregeln fuer Dateiausgaben)
Entscheidungen: E6, E7
Reviewer: Codex (ungerade Etappe)
Abnahme laut Plan v1.1: wie Etappe 12 (Headless-Auftrag kommt als `result.v1` zurueck; Ablehnung
wird als `policy_blocked` erkannt).

## 1 Ziel

Zweite Cloud-Lane ueber das offizielle CLI mit `codex exec --json` im Reinraum. Zusaetzlich zu
Etappe 12: Dateiausgaben aus dem Arbeitsverzeichnis des Containers werden kontrolliert ins
Laufverzeichnis uebernommen (`output.files` mit SHA-256).

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| `run_in_cleanroom`, Fehlerzuordnungs-Muster, Fake-CLI-Testansatz | Etappe 12 |
| Plan Abschnitt 8: Abo-Zugang per "Sign in with ChatGPT", fuer Server `codex login --device-auth`; `codex exec` ist standardmaessig read-only, `--sandbox workspace-write` erlaubt Aenderungen, `--json` liefert Ereignisse | Plan v1.1, Annahme ohne eigene Pruefung |
| `result.v1.output.files[] = {name, path, sha256}`, Pfade relativ ohne `..` | `docs/SCHEMA.md`, VB-6 |

## 3 Scope

**In:** Image `docker/lane-codex/`, `CodexExecutor`, Ereignis-Parser, Dateiuebernahme, Login-Handbuch,
Tests, `docs/LANES.md` (Codex).

**Out:** Git-Operationen im Container (kein Clone, kein Commit, kein Push), Netzzugriff ausser dem
Anbieter-Endpunkt (nicht erzwingbar, siehe 9), Installation von Abhaengigkeiten im Container.

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | Image wie Etappe 12 (Digest, festgenagelte CLI-Version, Nutzer `lane`), Credentials-Volume `deck-codex-creds`. |
| D2 | Aufruf: `codex exec --json` mit Prompt aus `/in/payload.sent.md` ueber die Standardeingabe; Arbeitsverzeichnis `/work` (tmpfs); Sandbox-Stufe `read-only` als Default. |
| D3 | `--sandbox workspace-write` nur, wenn `task.kind == code` **und** `task.labels["codex_sandbox"] == "workspace-write"`; sonst read-only. Netzzugriff des CLI-Sandbox-Modus bleibt aus, soweit die Version das erlaubt. |
| D4 | Ereignisstrom: letzte Nachricht des Agenten wird `output.text`; Fehlerereignisse liefern den Code (Zuordnung wie Etappe 12 D6, Muster in `codex_errors.py`); unvollstaendiger Strom → `internal`. |
| D5 | Dateiuebernahme: nach dem Lauf werden Dateien aus `/work` (nur regulaere Dateien, Groesse je Datei max. 5 MB, gesamt max. 50 MB, keine Symlinks) nach `runs/<task_id>/<attempt>/out/` kopiert; `output.files[].path` relativ zu `out/`, Pruefung ueber `app/core/paths.py`; SHA-256 je Datei. Mehr als das Limit → `internal` mit Grund. |
| D6 | Dateiausgaben werden vor der Uebernahme mit dem Scanner (Etappe 5) geprueft; `block`-Treffer → Dateien verworfen, `status = blocked`, `policy_blocked`. |
| D7 | Login: `make lane-login LANE=codex` startet `codex login --device-auth` im Container mit dem Volume; Owner bestaetigt im Browser. Keine Tokens im Repo. |
| D8 | Modell je `kind` aus `config/router.yaml` (`lanes.codex.models`), kein Name im Code (E7). |
| D9 | Timeout, Abbruch, Log, `lane_status`, Pause bei `policy_blocked`: identisch zu Etappe 12. |
| D10 | Tests ueber Fake-CLI (`tests/e2e/fake_codex_cli.py`), das den Ereignisstrom und optional Dateien in `/work` erzeugt; Docker-Tests mit Marker; realer Lauf mit `DECK_E2E_CODEX=1`. |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `docker/lane-codex/Dockerfile`, `entrypoint.sh`, `VERSION` | Image |
| `app/workers/lanes/codex.py`, `codex_errors.py`, `app/workers/outputs.py` | Executor, Zuordnung, Dateiuebernahme |
| `app/workers/prompts/codex_v1.md` | Vorlage: Auftrag, erlaubte Aenderungen, Ausgabe als Diff oder Datei |
| `Makefile` | `lane-image LANE=codex`, `lane-login LANE=codex` |
| `tests/unit/test_codex_executor.py`, `tests/unit/test_outputs.py`, `tests/e2e/test_codex_lane.py`, `tests/e2e/fake_codex_cli.py` | Tests |
| `docs/LANES.md`, `.env.example` | Doku, `DECK_E2E_CODEX` |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | Fake-CLI mit gueltigem Ereignisstrom, keine Dateien | `result.v1` valide, `output.files = []` |
| 2 | Fake-CLI schreibt zwei Dateien in `/work` | `out/` enthaelt beide, `sha256` stimmt, Pfade relativ |
| 3 | Datei mit Traversal-Namen oder Symlink | verworfen, Fehler mit Grund, kein Schreiben ausserhalb `out/` |
| 4 | Dateigroesse ueber Limit | `internal`, keine Teiluebernahme |
| 5 | Datei mit Schluessel-Muster | `blocked`, `policy_blocked`, Dateien verworfen |
| 6 | `kind = code` ohne Label | Sandbox read-only; mit Label `workspace-write` | Argumente geprueft |
| 7 | Ablehnung, fehlender Login, Timeout, Abbruch | wie Etappe 12 Tests 2 bis 5 |
| 8 | Server (`DECK_E2E_CODEX=1`): Referenzfall `kind = code` mit kleinem Diff | `result.v1` zurueck, Diff in `output.text` oder `out/`, Protokoll im PR |

## 7 Definition of Done

`make lint`, `make test` gruen; Docker-Tests mit rootless Docker gruen; Serverlauf durch den Owner;
`docs/LANES.md` ergaenzt (Login per Geraetecode, Sandbox-Regeln, Dateilimits); PR-Template mit
Reviewer **Codex** und "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: Etappe 12 nicht gemergt ist; die festgenagelte CLI-Version keinen
JSON-Ereignismodus oder keine Sandbox-Stufen bietet; Device-Login im Abo nicht verfuegbar ist.

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Agent veraendert mehr als beauftragt | read-only Default, Dateilimits, Diff statt stilles Schreiben, `review` Standard |
| Dateiausgaben tragen sensible Inhalte zurueck | Scanner-Pruefung D6, Payload bleibt redigiert |
| Netzzugriff des CLI im Container nicht einschraenkbar | Reinraum ohne Host-Zugang, kein DB-Zugang; Restrisiko dokumentieren |

## 10 Bewusst offen

- Ob `workspace-write` ueberhaupt in v1 freigeschaltet wird (Default: nur mit Label).
- Lanes 19 (Cursor ueber Cloud-Agent-API) und 20 (Grok) bleiben ausserhalb des Plans bis Klaerung.
- Abgleich mit Plan v1.1 bei Start der Etappe (Flag-Namen, CLI-Version).
