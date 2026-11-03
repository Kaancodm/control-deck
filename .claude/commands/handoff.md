---
description: Write HANDOFF.md with the current state, open points, and the next step for the following session
argument-hint: [optional note for the next session]
---

Write or overwrite `HANDOFF.md` in the repository root. Note from the caller: $ARGUMENTS

Gather the facts first, do not guess:

1. `git status --short`, `git log --oneline -10`, and the current branch name.
2. The open pull request for this branch, if any (number, title, draft or not, CI state).
3. The latest ticket in `tasks/` that matches this branch, and which of its steps are done.
4. The last `make lint` and `make test` results from this session; if unknown, say so.

Write `HANDOFF.md` in German (de-CH, no Eszett, write `ss`), under 80 lines, with these sections:

```
# HANDOFF

## Stand
- Branch, letzter Commit, PR-Link und PR-Status
- Was in dieser Session gebaut und geprueft wurde (Befehle und Ergebnis)

## Offene Punkte
- Was bewusst nicht gemacht wurde, mit Grund
- Bekannte Risiken, TODOs im Code, fehlende Tests

## Naechster Schritt
- Genau ein konkreter naechster Schritt mit Dateipfaden und Abnahme-Befehl
- Danach: `/clear`, neue Session, `/plan-ticket` fuer die naechste Etappe
```

Rules: no secrets, no personal data, no IPs or hostnames. Do not commit or push; the owner
decides whether `HANDOFF.md` goes into the pull request. Finish by printing the file path.
