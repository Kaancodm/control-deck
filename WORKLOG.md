# Worklog

Append-only. Regeln: siehe `AGENTS.md`. Zeiten: Europe/Zurich.

## 2026-11-12 22:25 — Cursor — Phase Rollenangleich

PR #92, Commits `9624e51` und `bc04edd`. Dateien: `AGENTS.md`, `CLAUDE.md`, `COORDINATION.md` und die genannten Regeltexte.
Check: `make lint && make test`, Exit 0. 2801 passed, 1082 skipped, 1 Starlette-Warnung, 90.67s.
Offen: GO fuer PR #92, Egress-Proxy, Ollama-Modelle. Risiko: SuperGrok nicht mehr als Reviewer benannt.

## 2026-11-12 22:39 — Cursor — Phase eine Stelle und Start-Check

PR #92. `AGENTS.md`, `CLAUDE.md`, `COORDINATION.md`, `Makefile`, `prompts/`, `LEADERBOARD.md`, `WORKLOG.md`, `scripts/check_rules.py`, `tests/unit/test_check_rules.py`.
Start-Check (Modell, Effort, Kontext, Doppelarbeit) geht an Grok Bot (Atik). Ohne dessen OK keine Arbeit.
Check: `make lint && make test`, Exit 0. 2806 passed, 1082 skipped, 1 Starlette-Warnung, 87.51s. Dieser Log-Satz kam danach.
Offen: GO fuer PR #92, Egress-Proxy, Ollama-Modelle. Risiko: Engineering Lead und Hermes ohne Rolle in `AGENTS.md`.

## 2026-11-12 22:48 — Cursor — Phase CI

PR #92, Commit `643e620`. Keine Produktänderung. Leaderboard-Zeile Cursor auf gruen.
Check: `gh pr checks 92`. quality pass (7m40s), CodeFactor pass, GitGuardian pass.
Offen: GO fuer den Entwurf, Egress-Proxy, Ollama-Modelle. Risiko: keines aus diesem CI-Lauf.

## 2026-11-12 23:40 — Cursor — Phase AGENTS.md wiederhergestellt

Commit `38e6cf6` ersetzte `AGENTS.md` durch `@AGENTS.md`. Die Rollenzeilen fuer Kaan und Engineering Lead standen nicht im Diff.
Wiederhergestellt aus `6e1de94`, plus die Hermes-Rolle aus `prompts/hermes.md`: lokaler Agent, nur lokales Ollama, keine Keys.
Check: `make lint && make test`, Exit 0. 2806 passed, 1082 skipped, 1 Starlette-Warnung, 90.68s.
Offen: GO fuer PR #92. Risiko: Kaan und Engineering Lead bleiben ohne eigene Rolle.

## 2026-11-12 23:45 — Cursor — Phase Rollen Kaan und Engineering Lead

Commit `6a6be1b` auf PR #92. Kaan ist Sammler, gegenueber allen AIs, gesteuert von Grok Bot (Atik). Engineering Lead koordiniert den PR-Loop, schreibt keinen Code, merget nicht.
Leaderboard-Zeile Engineering Lead angepasst. Keine weitere Datei.
Check: `make lint && make test`, Exit 0. 2806 passed, 1082 skipped, 1 Starlette-Warnung, 85.91s.
Offen: GO fuer PR #92. Risiko: keines aus diesem Diff.

## 2026-11-12 23:55 — Cursor — Phase GO-Liste nach Merge

PR #92 ist gemergt: `a105245`, mergedAt 2026-11-12T22:51:01Z, mergedBy Kaancodm. Quelle: `gh pr view 92`.
`CLAUDE.md` nennt den Merge nicht mehr als offenes GO. `COORDINATION.md` und `LEADERBOARD.md` auf erledigt.
Check: `make lint && make test`, Exit 0. 2806 passed, 1082 skipped, 1 Starlette-Warnung, 85.93s.
Offen: Egress-Proxy, Ollama-Modelle. Risiko: keines.

## 2026-11-12 23:58 — Cursor — Phase PR #93 auf der GO-Liste

Entwurf #93. `CLAUDE.md` Punkt 1 fragt das Ja fuer diesen Entwurf. Egress-Proxy ist Punkt 2, Ollama-Modelle Punkt 3.
Check: `make lint` nach dieser Zeile, Exit 0. Die volle Suite steht im Eintrag darueber.
Offen: GO fuer PR #93, Egress-Proxy, Ollama-Modelle. Risiko: keines.

## 2026-11-13 00:03 — Cursor — Phase CI PR #93

PR #93, Commit `a9b0569`. Keine Produktänderung. Leaderboard-Zeile Cursor auf gruen.
Check: `gh pr checks 93`. quality pass (8m3s), CodeFactor pass, GitGuardian pass.
Offen: GO fuer den Entwurf, Egress-Proxy, Ollama-Modelle. Risiko: keines aus diesem CI-Lauf.

## 2026-11-13 00:20 — Cursor — Phase GO-Liste nach Merge #93

PR #93 ist gemergt: `a123d40`, mergedAt 2026-11-12T23:16:19Z, mergedBy Kaancodm. Quelle: `gh pr view 93`.
`CLAUDE.md` fragt den Merge nicht mehr. Dieser Entwurf steht selbst nicht auf der Liste, damit die Frage nicht wieder auftaucht.
Check: `make lint && make test`, Exit 2. 2805 passed, 1 failed (`test_process.py`, pidfd EINVAL), 1082 skipped, 86.26s.
Zweiter Lauf derselben 4 Tests: 4 passed, 1.62s. Kein Bezug zu den Doc-Dateien.
Offen: Egress-Proxy, Ollama-Modelle. Risiko: der Prozess-Test ist einmal geflakt.

## 2026-11-24 10:58 — Claude Opus 5.5 — Weitblick Stufe 2 (lokal)

Branch `feat/weitblick-stufe2`, nicht committet (keine Git-Identitaet), nicht gepusht. Nur `weitblick/`, GO-Punkt 3 in `CLAUDE.md`.
Neu: Hindsight-Spiegel (Bank weitblick, Modus chunks), Nachtlauf, S-K80- und Vertragspruefer, `SERVERPRUEFUNG.md`.
Check: `python3 -m unittest discover -s weitblick/tests -v`: Ran 60 tests, OK, 0 failed, 0 skipped. `scripts/check_rules.py` Exit 0.
Offen: F2, F5, S-K80 und T5 nur auf dem Server; T8-Restore braucht GO; Ruff lokal nicht verfuegbar.

## 2026-11-24 12:00 — Builder — Weitblick Stufe 2 Review-Fix (PR #98)

Branch `feat/weitblick-stufe2`. Retain-Feld `mode` entfernt, F2 gegen
Hindsight v1.2.1 dokumentiert, T4 und Vertrag angepasst, SERVERPRUEFUNG und
S-K80 um Chunk-Pfad ergaenzt. PR auf Draft gestellt. Kein Merge.
Offen: F2/F5/S-K80 auf dem Server; Schritt 5 der Serverpruefung braucht Kaans Ja.

## 2026-11-24 12:50 — Builder — Weitblick Stufe 2 Nachzug (PR #98, Draft)

Chunk-Pruefpunkt als offen ausgewiesen. Aufgegebene Eintraege nach 3 Versuchen
mit Zaehlung, Exit ungleich 0 und Meldung. Kein Merge, PR bleibt Draft.

## 2026-11-24 13:17 — Builder — Weitblick Stufe 2 Nachzug (PR #98, Draft)

401/403 als Konfigurationsfehler (Exit 3), kein Versuch, Budget-Rollback.
Kein Merge, PR bleibt Draft.

## 2026-11-24 13:23 — Builder — Weitblick Stufe 2 Haertung (PR #98, Draft)

README: Versuchslimit 3, aufgegeben, Zuruecksetzen. Tests fuer Mid-Run-401/403,
sonstige Fehler als Versuch, Exit-Codes, Budget-Reset. Kein Merge, PR Draft.

## 2026-11-24 22:59 — Builder — Steuerdateien auf Stand von main

PR #98 gemergt 24.11.2026 13:44 CET (`979b89b`, mergedAt 2026-11-24T12:44:16Z, mergedBy Kaancodm). Quelle: `gh pr view 98`.
PR #99 gemergt 24.11.2026 20:32 CET (`75318c9`, mergedAt 2026-11-24T19:32:53Z, mergedBy Kaancodm). Quelle: `gh pr view 99`.
CLAUDE.md, COORDINATION.md, LEADERBOARD.md, WORKLOG.md, README.md, CHANGELOG.md und `docs/ETAPPEN/README.md` nachgefuehrt. Kein Produktcode, kein Merge.
Check: `uv run ruff check --no-cache .`, `ruff format --check`, `mypy app/`, `scripts/check_rules.py` Exit 0. `uv run pytest`: 2806 passed, 1082 skipped, 1 Starlette-Warnung, 85.72s. gitleaks auf dem Diff: keine Funde. `git diff --check` Exit 0.
Offen: Etappe 18 Abnahme und Tag v1.0, Weitblick-Serverpruefung, Egress-Proxy, Ollama-Modelle.

## 2026-11-24 23:17 — Builder — Soll zu PR #101 (STAND und Changelog)

`docs/STAND.md` auf den Stand von `main` nachgezogen: #83, #73 und #32 gemergt; Live-Stack #39/#40/#44 seit 05.11. auf `main`; Weitblick und #96 bis #99 ergaenzt. Quelle: `git log` und `gh pr view`.
CHANGELOG: `retain_extraction_mode=chunks` als Bank- oder Server-Einstellung; Zurueckgeben nur bei `reserviert` ohne `sendet`, sonst `unklar` und offen.
Check: ruff, format, mypy und `scripts/check_rules.py` Exit 0. `uv run pytest`: 2806 passed, 1082 skipped, 1 Starlette-Warnung, 83.55s. gitleaks auf dem Diff: keine Funde. `git diff --check` Exit 0.
Offen: Abnahme und Tag v1.0, Weitblick-Serverpruefung, Abgleich mit Draft #100. Kein Merge.

## 2026-11-24 23:27 — Builder — GO-Liste in #101 auf «alle em»

`CLAUDE.md`: Liste lautet «Nichts offen». Entscheid 24.11.2026 `alle em`: Egress-Proxy bleibt aus, Ollama-Bestand bleibt. Merge-Vermerk zu #98/#99 bleibt als Info, nicht als GO-Punkt. Wortlaut an #100 angelehnt, #100 unveraendert.
`COORDINATION.md`: Ollama- und Egress-Zeilen sowie Weitblick-Abschnitt nachgezogen. `docs/STAND.md` ebenso. README fuehrte die Punkte nicht als offen.
Check: ruff, format, mypy und `scripts/check_rules.py` Exit 0. `uv run pytest`: 2806 passed, 1082 skipped, 1 Starlette-Warnung, 83.33s. gitleaks auf dem Diff: keine Funde. `git diff --check` Exit 0.
Offen: Abnahme und Tag v1.0, Weitblick-Schritte 1 bis 4 nicht ausgefuehrt, Schritt 5 und S-K80 ohne Ja. Kein Merge.
## 2026-11-24 23:31 — Cursor — Issue #80 Korrektur leeren

Branch `cursor/80-korrektur-leeren-0407`. `clear_fact_bounds` leert `valid_to` und `superseded_by` nur ueber eine neue Freigabezeile. Keine Vorarbeit auf main, in Branches oder PRs.
Check: `make lint` Exit 0 (ruff, 480 formatiert, mypy 167, check_rules). `pytest` gegen `controldeck_test`: 3748 passed, 143 skipped, 1 Starlette-Warnung, 263.35s.
Offen: Draft-PR, drei Reviews, kein Merge. Risiko: #78 hasht den gespeicherten Entscheid; Leeren und Entscheidwechsel bleiben zwei Korrekturen.

## 2026-11-24 23:31 — Cursor — M1b Dialog-Thread #77

Branch `cursor/m1b-dialog-thread-f58e`. Dialog schreibt `ingest_turn` und `record_deck_reply`. Prompt aus `load_memory_context` (Ziel `ollama`). Quarantaene bleibt draussen, `num_ctx` 4096.
Check: `make lint` Exit 0. `make test` mit Testdatenbank `controldeck_test`: 3754 passed, 143 skipped, 1 Starlette-Warnung, 266.00s.
Offen: Entwurf, kein Merge. `persist` gibt die Antwort nicht frei (Frage zu #78 F5). Risiko: Konflikt mit #78 an der Dialog-UI, mit #76 an Lader und Doku.

## 2026-11-24 23:36 — Cursor — M1b Entwurf #102

Draft-PR https://github.com/Kaancodm/control-deck/pull/102 gegen main. Branch `cursor/m1b-dialog-thread-f58e`, Commit `0b8dbf3`. Kein Merge.
Check: gitleaks 8.30.1 auf `75318c9..0b8dbf3`, keine Funde. CI beim Oeffnen noch laufend.
Offen: fuenf Fragen im PR, darunter #78 F5. Risiko: #78 Dialog-UI, #76 Lader und Doku, #80 `approval.py` (hier unberuehrt).

## 2026-11-24 23:40 — Cursor — Issue #80 CI

PR #103, HEAD `8890579`. Checks: quality pass (7m6s), CodeFactor pass, GitGuardian pass.
Leaderboard Cursor auf gruen. Kein Merge.
Offen: drei Reviews und das Merge-GO. Risiko: keines aus diesem CI-Lauf.

## 2026-11-24 23:58 — Cursor — Issue #80 Nachzug

PR #103, Branch `cursor/80-korrektur-leeren-0407`. `clear_fact_bounds` prueft die Quelle wie `correct_fact`: abgelehnte Quelle ist `source_rejected`, nichts geschrieben. Randfaelle: not_found, not_decided, veralteter Hash, zwei konkurrierende Korrekturen, Kette, abgelaufene und idle Session, Rollback nach flush. Leaderboard Cursor bleibt gelb.
Check: `make lint` Exit 0 (ruff, 480 formatiert, mypy 167, check_rules). `pytest` gegen `controldeck_test`: 3752 passed, 143 skipped, 1 Starlette-Warnung, 248.77s.
Offen: drei Reviews und das Merge-GO. Kein Merge. Risiko: #78 muss die Quellpruefung beim Leeren eines freigegebenen Faktums mitrechnen.

## 2026-11-24 23:58 — Cursor — Auftragsfreie Redaktion #76

Branch `cursor/order-free-redaction-60ba` ab `main` `52c690c`. Cloud-Ziele erhalten `cloud_redacted` nur mit Platzhalter je Aufruf. `privacy_hold`, Geheimnisse und fehlende Blockliste bleiben draussen. Dialogpfad unveraendert auf `ollama`.
Check: `make lint` Exit 0. `make test` mit Testdatenbank `controldeck_test`: 3893 passed, 5 skipped, 1 Starlette-Warnung, 292.63s.
Offen: Entwurf gegen main, kein Merge. Map wird nicht gespeichert. Risiko: Doku in `docs/KANAELE.md` und ADR 0010, Aenderungen dort klein.

## 2026-11-25 00:02 — Cursor — Entwurf #104

Draft-PR https://github.com/Kaancodm/control-deck/pull/104 gegen main. Branch `cursor/order-free-redaction-60ba`, Commit `847691c`. Kein Merge.
Check: gitleaks 8.30.1 auf `origin/main..847691c`, keine Funde.
Offen: CI. Empfehlung: Map je Aufruf lassen, Dialog auf `ollama` lassen.

## 2026-11-25 00:07 — Cursor — Issue #80 main eingeholt

Merge-Commit von `origin/main` `52c690c` (#102) in `cursor/80-korrektur-leeren-0407`. Kein Rebase, kein Force-Push. Beide Staende in Koordination, Leaderboard und Worklog. Cursor bleibt gelb.
Check: `make lint` Exit 0 (ruff, 482 formatiert, mypy 168, check_rules). `pytest` gegen `controldeck_test`: 3761 passed, 143 skipped, 1 Starlette-Warnung, 249.49s.
Offen: quality auf #103, drei Reviews, Merge-GO. Kein Merge des PR. Risiko: keines aus den Konfliktdateien.

## 2026-11-25 00:10 — Cursor — Issue #78 Gedaechtnis-Inbox

Branch `cursor/78-deck-gedaechtnis-a1b0`, Entwurf PR #105, Basis `cursor/80-korrektur-leeren-0407`, gestapelt auf #103. `/ui/memory`. Vertrag von `clear_fact_bounds` unveraendert. `persist` gibt die Deck-Antwort nicht frei; die Freigabe sitzt in der Inbox.
Check: `make lint` Exit 0. `pytest` 3769 passed, 140 skipped, 252.06s. gitleaks 8.30.0 keine Funde.
Offen: Entwurf bis Security, Codequalitaet und Plan-Passung. Kein Merge.

## 2026-11-25 00:21 — Cursor — Issue #80 main #104 eingeholt

Merge-Commit von `origin/main` `e771dc4` (#104) in `cursor/80-korrektur-leeren-0407`. Kein Rebase, kein Force-Push. Stand von #102 und #104 aus main, Stand von #103 bleibt. Cursor bleibt gelb.
Check: `make lint` Exit 0 (ruff, 482 formatiert, mypy 168, check_rules). `pytest` gegen `controldeck_test`: 3762 passed, 143 skipped, 1 Starlette-Warnung, 251.43s.
Offen: quality auf #103, drei Reviews, Merge-GO. Kein Merge des PR. Risiko: keines aus den Konfliktdateien.

## 2026-11-25 00:39 — Cursor — Issue #78 Basis nachgezogen

Merge-Commit von `origin/cursor/80-korrektur-leeren-0407` `f39b0d0` in `cursor/78-deck-gedaechtnis-a1b0`. Kein Rebase, kein Force-Push. `clear_fact_bounds` unveraendert. `source_rejected` bleibt ein UI-Code und schreibt nichts. `persist` gibt die Deck-Antwort nicht frei.
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `pytest` gegen `controldeck_test`: 3780 passed, 140 skipped, 1 Starlette-Warnung, 264.06s. gitleaks 8.30.0 auf `origin/cursor/80-korrektur-leeren-0407..HEAD` mit `-m`: 4 Commits, keine Funde.
Offen: Entwurf, quality nach dem Push, drei Reviews. Kein Merge. Risiko: Freigabe nur ueber `/ui/memory`.

## 2026-11-25 01:14 — Cursor — Issue #78 F5 und Inbox

`persist` gibt nur die angezeigte Deck-Antwort frei, ueber sha256 der gezeigten Bytes. Ein falscher oder veralteter Hash schreibt nichts. Ohne `persist` bleibt der Tausch in Quarantaene. `privacy_hold` bleibt gesetzt. Die Inbox filtert `quarantined` und `proposed`, bevor sie auf 30 kuerzt, und zeigt «weitere vorhanden».
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `pytest` gegen `controldeck_test`: 3784 passed, 140 skipped, 1 Starlette-Warnung, 256.63s. gitleaks 8.30.0 auf `origin/cursor/80-korrektur-leeren-0407..HEAD` mit `-m`: 5 Commits, keine Funde.
Offen: quality nach dem Push, drei Reviews. Kein Merge. Risiko: die Owner-Zeile bleibt in der Quarantaene.

## 2026-11-25 01:29 — Builder — main in #101 nachziehen

Merge-Commit von `origin/main` `5d1de27` in `docs/steuerdateien-stand-99`. Kein Rebase, kein Force-Push.
#102 (`52c690c`, 24.11.2026 23:55 CET), #104 (`e771dc4`, 25.11.2026 00:13 CET) und #103 (`5d1de27`, 25.11.2026 01:24 CET) sind auf main. #100 geschlossen, nicht gemergt (Kaan em). #105 (Issue #78) offener Entwurf. GO-Liste bleibt «Nichts offen»; «Entwurf M1b mergen» als erledigt festgehalten.
Check: `uv run ruff check --no-cache .`, `ruff format --check`, `mypy app/`, `scripts/check_rules.py` Exit 0. `uv run pytest`: 2815 passed, 1090 skipped, 1 Starlette-Warnung, 86.84s.
Offen: #105 in Arbeit, Weitblick-Schritte 1 bis 4 nicht ausgefuehrt, Schritt 5 und S-K80 ohne Ja, Abnahme und Tag v1.0. Kein Merge von #101.

## 2026-11-25 01:30 — Cursor — Issue #78 main eingeholt

Merge-Commit von `origin/main` `5d1de27` (#103) in `cursor/78-deck-gedaechtnis-a1b0`. Kein Rebase, kein Force-Push. Basis von #105 wird main. F5 und die Inbox-Kappung bleiben.
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `pytest` gegen `controldeck_test`: 3784 passed, 140 skipped, 1 Starlette-Warnung, 258.63s. gitleaks 8.30.0 auf `origin/main..HEAD` mit `-m`: 14 Commits, keine Funde.
Offen: quality nach dem Push, drei Reviews. Kein Merge. Risiko: keines aus den Konfliktdateien.

## 2026-11-25 01:46 — Builder — #105-Stand und #83-Zeit in #101

#105-HEAD mit `gh pr view 105` geprueft: `d03211f` (Fix `5d3ef3a`, danach Merge von main). Entwurf gegen main, Re-Review offen. In `CLAUDE.md`, `COORDINATION.md` und `docs/STAND.md` nachgezogen. #83-Mergezeit auf 20:12 CET (mergedAt 2026-11-10T19:12:00Z). STAND-Titel auf 25.11.2026.
Check: ruff, format, mypy und `scripts/check_rules.py` Exit 0. `uv run pytest`: 2815 passed, 1090 skipped, 1 Starlette-Warnung, 84.95s.
Offen: Re-Review von #105, Weitblick-Schritte 1 bis 4 nicht ausgefuehrt, Schritt 5 und S-K80 ohne Ja. Kein Merge von #101.

## 2026-11-25 02:04 — Cursor — Issue #78 persist und Markierung

`persist` gibt eine Antwort mit `privacy_hold` nicht frei; die Markierung bleibt in der Quarantaene sichtbar. Der Hash auf diesem Weg ist formal, aus der eben gespeicherten Zeile. Ein falscher Hash auf eine Nicht-Antwort bleibt `not_found`. `_act` ist in kurze Funktionen geteilt.
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `pytest` gegen `controldeck_test`: 3786 passed, 140 skipped, 1 Starlette-Warnung, 261.01s. gitleaks 8.30.0 `protect --staged`: keine Funde.
Offen: quality nach dem Push. Kein Merge. Risiko: die Vorab-Freigabe prueft die Bytes nicht in einem zweiten Blick.

## 2026-11-25 02:23 — Cursor — Issue #78 main #101 eingeholt

Merge-Commit von `origin/main` `d900b91` (#101) in `cursor/78-deck-gedaechtnis-a1b0`. Kein Rebase, kein Force-Push. Beide Staende in Changelog, Koordination, Leaderboard und Worklog. #105: Re-Review 01:51 ok (Auftrag Atik 25.11.2026 02:16), Nachbesserung `f7548d4`, Kurz-Recheck offen.
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `pytest` gegen `controldeck_test`: 3786 passed, 140 skipped, 1 Starlette-Warnung, 260.09s. gitleaks 8.30.0 auf `origin/main..0c354d1`: 11 Commits, keine Funde.
Offen: quality, CodeFactor und GitGuardian nach dem Push. Kurz-Recheck. Kein Merge. Risiko: keines aus den Konfliktdateien.

## 2026-11-25 02:35 — Cursor — Issue #78 CI nach main #101

Geprueft auf `78dab07`. quality pass, 7m28s, Lauf 45525590936. CodeFactor pass. GitGuardian pass. `mergeable` MERGEABLE, Entwurf bleibt.
Check: `gh pr checks 105`.
Offen: Kurz-Recheck. Kein Merge. Risiko: keines aus diesem Lauf.

## 2026-11-25 11:44 — Cursor — Rollen E-056

`AGENTS.md`, Koepfe von `tasks/*.md` und `tasks/TEMPLATE.md` auf E-056. Merge nur durch Atik nach dem GO von Kaan (E-055). #105 als gemergt 25.11.2026 02:54 (`71731c8`) in `docs/STAND.md`, `COORDINATION.md`, der GO-Liste und `CHANGELOG.md`.
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `make test`: 2822 passed, 1104 skipped, 1 Starlette-Warnung, 81.73s. Keine Testdatenbank gesetzt.
Offen: Merge dieses PR nur durch Atik nach dem GO von Kaan. Kein Auto-Merge, kein Force-Push. Risiko: aeltere Rollensaetze ausserhalb dieser Dateien bleiben stehen; `AGENTS.md` gilt.

## 2026-11-26 03:05 — Cursor — Rollen E-056 Rest

Branch `cursor/e056-rollen-rest` ab main `15f10a2`. In tasks 04 bis 08 heissen die Umsetzungsbranches `codex/` statt `stage/`. HANDOFF, REVIEW, Prompts, ADR 0008, ARCHITECTURE und ETAPPEN/README folgen E-056.
Check: `make lint` Exit 0 (ruff, 490 formatiert, mypy 172, check_rules). `make test`: 2822 passed, 1104 skipped, 1 Starlette-Warnung, 82.10s. Keine Testdatenbank gesetzt.
Offen: Merge nur durch Atik nach dem GO von Kaan. Kein Auto-Merge, kein Force-Push. Kein CHANGELOG-Eintrag.
