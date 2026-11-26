# Agent rules (Control Deck)

These rules apply to all automated contributors. If anything conflicts, AGENTS.md wins.
`CLAUDE.md` and `COORDINATION.md` follow this file and must not contradict it.

## Checkpoint

- Lead bots read `AGENTS.md`, `CLAUDE.md`, and `COORDINATION.md` before every run.
- Every agent and every lead bot updates `Wartet auf dein GO` at the top of `CLAUDE.md` at the end of each job. The owner reads that list in `CLAUDE.md`. If nothing needs a GO, write `Nichts offen.`
- Each item there is numbered, in German (de-CH, no Eszett), at most five lines, with `em` (the recommendation), `Option 2`, and the biggest difference in one sentence. The owner replies `N. em` or `N. 2`.
- Lead bots update `LEADERBOARD.md` at each checkpoint. One row each for Grok Bot, Kaan, Engineering Lead, Claude Opus, Gemini Carbon, Codex Sol, Cursor, and SuperGrok: time in Europe/Zurich, result, status `gruen`, `gelb`, or `rot`, and a link.
- No phase is done without a `WORKLOG.md` entry. The log is append-only. Each entry has the Europe/Zurich time, who, the job or phase, what changed, the check command and result, and what is open or at risk. At most about five lines.
- Before any job, every AI checks `LEADERBOARD.md`, `WORKLOG.md`, and open PRs, proposes one short numbered block for model, effort, context handling, and a collision (each with `em`, `Option 2`, and the biggest difference), and waits for Grok Bot (Atik). No work starts before that OK.

## Security and privacy

- Do **not** commit secrets, API keys, tokens, or credentials. Never print secrets.
- Do **not** commit personal data (PII), real names, e-mail addresses, or internal identifiers.
- Do **not** commit IP addresses, hostnames, or infrastructure-specific URLs in code or docs.
- Do **not** open network connections to production systems or user machines unless explicitly requested.
- Do **not** force-push or rewrite pushed history. The repository is and stays public.
- An explicit owner GO is required before any deploy, restart, migration, server install, or deletion.
  Preparing that change in the repository is allowed. Running it is not, until that GO.
  Test commands against a database whose name ends in `_test` stay covered by `CLAUDE.md`.

Agents read [COORDINATION.md](COORDINATION.md) before claiming work. That file records who
is working on what. It does not override this file. The checkpoint above still applies.

## Dependencies and scope

- Do **not** add new runtime or dev dependencies without a clear justification in the pull request description.
- Prefer minimal, focused changes that match the requested stage of the project.
- Keep code, comments, and commit messages in **English** (Conventional Commits).
- Keep `README.md` and everything under `docs/` in **German (de-CH, no Eszett)**.

## Roles

Entscheid E-056 (Auftrag von Kaan via Atik, 25.11.2026). Quelle: dieser Auftrag.
Methode: festgehaltener Entscheid. Status: proven als Ablauf in dieser Datei.
Er ersetzt die Rollen aus dem Nachtrag vom 2026-11-12.

- **Gemini Carbon** ist Denker/Planer und Entwickler.
- **Codex Sol** ist Entwickler. Das ist die Entwicklerrolle, keine neue Produkt-Lane.
- **Claude Opus** ist Assistenz-Entwickler und Reviewer.
- **Cursor** und **SuperGrok** machen kleine Fixes und Reviews.
- **Grok Bot** ist der Code-Pruefer. **Gemini Carbon** macht den Gegencheck.
- **Kaan** gibt das GO fuer jeden Pull Request (E-055). Nur **Atik** mergt, und nur
  nach diesem GO. Kein Auto-Merge. Kein Force-Push.
- Branch-Prefixe: `cursor/`, `codex/` und `gemini/<punkt>-<kurz>`.
- **Kaan** ist der Sammler. Er vertritt den Owner gegenueber allen AIs und wird von Grok Bot (Atik) gesteuert.
- **Engineering Lead** koordiniert den Pull-Request-Lauf (offene PRs, CI, Review-Nachzug). Engineering Lead schreibt keinen Code und mergt nicht.
- **Hermes** ist der lokale Agent auf dem Deck-Server. Er nutzt nur lokales Ollama und haelt keine Keys.

## Quality

- Run `make lint` and `make test` before marking work complete.
- Add or update tests when changing validation, schemas, or core behavior.
- Nur **Atik** mergt, und nur nachdem **Kaan** das GO fuer diesen Pull Request gegeben hat (E-055). Kein Auto-Merge. Kein Force-Push.

## Workflow

1. Der Owner gibt das Ziel in einem Satz.
2. Gemini Carbon plant und kann entwickeln. Der Auftrag nennt einen Pruefbefehl. Ein Gemini-Branch nutzt `gemini/<punkt>-<kurz>`.
3. Codex Sol entwickelt auf einem Branch mit Prefix `codex/`. Der Pruefbefehl muss bestehen.
4. Claude Opus assistiert beim Bau und reviewt.
5. Cursor und SuperGrok uebernehmen kleine Fixes und Reviews. Cursor nutzt einen Branch mit Prefix `cursor/`.
6. Grok Bot, der Code-Pruefer, prueft die Aenderung. Gemini Carbon macht den Gegencheck.
7. Kaan gibt das GO fuer diesen Pull Request (E-055). Nur Atik mergt. Kein Auto-Merge. Kein Force-Push.

## Reporting

Every end-of-job report states what was done, what was checked (the command plus the real output), what is still open, and the risks. Never claim done without verified check output. Never invent facts, paths, or test results.

Open items are numbered. Each item has `em` (the recommendation), `option 2`, and the biggest difference in one sentence. The owner replies `N. em` or `N. 2`.

Follow Architecture Decision Records in `docs/DECISIONS/` when making structural choices.
Entscheid E-056 (25.11.2026) ersetzt die Review-Rotation in ADR 0008 und die
Rollenteilung in den Nachtraegen vom 2026-11-11 und 2026-11-12. Der Merge folgt E-055.
