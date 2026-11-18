# Agent rules (Control Deck)

These rules apply to all automated contributors. If anything conflicts, AGENTS.md wins.
`CLAUDE.md` and `COORDINATION.md` follow this file and must not contradict it.

Who does what, who may decide what, and how work flows: see [TEAM.md](TEAM.md). This file
keeps the checkpoint, security, scope, quality, and reporting rules and repeats nothing from
that page.

## Checkpoint

- Lead bots read `TEAM.md`, `AGENTS.md`, `CLAUDE.md`, and `COORDINATION.md` before every run.
- Every agent and every lead bot updates `Wartet auf dein GO` at the top of `CLAUDE.md` at the end of each job. The owner reads that list in `CLAUDE.md`. If nothing needs a GO, write `Nichts offen.`
- Each item there is numbered, in German (de-CH, no Eszett), at most five lines, with `em` (the recommendation), `Option 2`, and the biggest difference in one sentence. The owner replies `N. em` or `N. 2`.
- Lead bots update `LEADERBOARD.md` at each checkpoint. One row each for Grok Bot, Kaan, Engineering Lead, Claude/Fable, Gemini, and Cursor: time in Europe/Zurich, result, status `gruen`, `gelb`, or `rot`, and a link.
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

## Quality

- Run `make lint` and `make test` before marking work complete.
- Add or update tests when changing validation, schemas, or core behavior.

## Reporting

Every end-of-job report states what was done, what was checked (the command plus the real output), what is still open, and the risks. Never claim done without verified check output. Never invent facts, paths, or test results.

Open items are numbered. Each item has `em` (the recommendation), `option 2`, and the biggest difference in one sentence. The owner replies `N. em` or `N. 2`.

Follow Architecture Decision Records in `docs/DECISIONS/` when making structural choices.
The review rotation in ADR 0008, and the role split in its amendment dated 2026-11-11, are superseded by the amendment dated 2026-11-12.
