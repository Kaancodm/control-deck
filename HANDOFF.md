# HANDOFF

Stand: 2026-11-04. Uebergabe aus der Session "Live-Stack-Review". Langfassung mit
Begruendungen und SHAs: `docs/SESSIONS/2026-11-04-live-stack-review.md`.

## Stand

- Branch dieser Uebergabe: `claude/session-handoff-docs-on6j3g`, Basis `main@a33cc3f` (#37 gemergt).
- `main` enthaelt die Etappen 1 bis 17, die P1-Nacharbeit (#37), Mobile-UX (#35) und die
  Referenzfaelle 013/014 (#36). Etappe 18 ist nicht gestartet, kein Tag, Version bleibt `0.1.0`.
- Live-Stack Ollama (ADR 0009) liegt als Draft-Kette vor: #39 -> #40 -> #41 -> #42 -> #44 -> #45
  -> #46 -> #47, darauf #48 (Lasttest). Alle auf `main@a33cc3f` rebasiert, `quality` und
  GitGuardian auf den Heads gruen. #43 (Parallel-ADR) wurde zugunsten von #39 zurueckgezogen.
- Aus dem Review entstanden zwei eigenstaendige Draft-PRs gegen `main`:
  - #49 `fix/runner-artifacts-after-commit`: `result.json` und Lane-Log erst nach dem Commit,
    neuer Fehlercode `artifact_publication_failed`. 3 neue Integrationstests, 22 gruen.
  - #50 `test/operations-user-unit-verify`: systemd-Verifikation im User-Scope mit
    Negativkontrolle, aus #39 ausgelagert. 41 Unit-Tests gruen.
- #38 `plan/04-07-inbetriebnahme`: vier Bautickets A bis D in `tasks/`, Review durch Fable offen.
- In dieser Session wurden keine `make lint` oder `make test` Laeufe auf `main` ausgefuehrt;
  die Zahlen oben stammen aus den PR-Beschreibungen und der CI.

## Offene Punkte

- Kein PR ist gemergt. Jeder Merge braucht ein explizites GO des Owners.
- Reihenfolge fuer Merges ist vorgeschlagen, nicht entschieden: zuerst #49 und #50 (klein,
  gegen `main`), dann #39 ff. in Kettenfolge, #48 zuletzt. #38 ist Planung und unabhaengig.
- Blocker 2 aus #38 (Timername und Manager fuer B2) ist offen; nichts raten.
- Echte Modelllaeufe, Owner-Canary (`make canary`), Server-Konfiguration und Restore-Probe
  sind Owner-Nachweise und wurden nicht durchgefuehrt.
- Folge-Issue auf dem Stack (`docs/FOLGE_ISSUE_RUNNER_2026-11-04.md`): Wiederaufnahme
  unvollstaendig veroeffentlichter Artefakte. #49 schliesst die Ursache, nicht das Aufraeumen.
- Browser-Tests (Playwright/Chromium) laufen nur in CI zuverlaessig; lokal je nach Umgebung rot.
- Setup-Script der Cloud-Umgebung scheitert bei `createdb` (Passwort-Auth fuer `root` auf
  Port 5432). Fuer Doku-Arbeit irrelevant, fuer Integrationstests im Container zu beheben.

## Naechster Schritt

- Review von #49 lesen und GO oder Aenderungswunsch geben. Pruefpfad:
  `app/workers/runner.py` (Reihenfolge Commit -> Artefakte), `docs/RUNNER.md`,
  `tests/integration/test_host_runner.py`.
  Abnahme-Befehl auf dem Branch `fix/runner-artifacts-after-commit`:

  ```bash
  make lint
  uv run pytest tests/integration/test_host_runner.py
  ```

- Danach: `/clear`, neue Session, `/plan-ticket` fuer die naechste Etappe oder das naechste
  Ticket aus #38 (A: `tasks/04-a-kb-runtime-migration.md`).
