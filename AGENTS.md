# Agent rules (Control Deck)

These rules apply to all automated contributors. If anything conflicts, AGENTS.md wins.
`CLAUDE.md` and `COORDINATION.md` follow this file and must not contradict it.

## Checkpoint

- Lead bots read `AGENTS.md`, `CLAUDE.md`, and `COORDINATION.md` before every run.
- Every agent and every lead bot updates `Wartet auf dein GO` at the top of `CLAUDE.md` at the end of each job. The owner reads that list in `CLAUDE.md`. If nothing needs a GO, write `Nichts offen.`
- Each item there is numbered, in German (de-CH, no Eszett), at most five lines, with `em` (the recommendation), `Option 2`, and the biggest difference in one sentence. The owner replies `N. em` or `N. 2`.

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

- **Claude plans.** The planning model is Fable via `/advisor`.
- **Cursor builds.**
- **Claude builds** only a complex job, and only when that job's brief explicitly says so.
  The build model is Opus 5.5 at medium effort.
- **Gemini Pro** is Lead Full-Stack Developer and pair-programming partner, steered by Grok Bot.
  Gemini Pro has no merge rights and is not security head.
- **Codex** is not used.
- **Grok Bot** coordinates. Grok Bot is the only merger.

## Quality

- Run `make lint` and `make test` before marking work complete.
- Add or update tests when changing validation, schemas, or core behavior.
- Only **Grok Bot** merges, and only after the owner says yes once. Claude, Fable, Gemini, and Cursor never merge.

## Workflow

1. The owner gives the goal in one sentence.
2. Claude plans with `/advisor` (Fable) and writes a short build ticket with a check command.
3. Cursor builds in a branch. The check command must pass.
4. Claude builds that ticket only when the brief explicitly says the job is complex, using Opus 5.5 at medium effort. Gemini Pro may pair-program on the build, steered by Grok Bot, without merge rights and without being security head.
5. The owner says yes once. Then Grok Bot merges.

## Reporting

Every end-of-job report states what was done, what was checked (the command plus the real output), what is still open, and the risks. Never claim done without verified check output. Never invent facts, paths, or test results.

Open items are numbered. Each item has `em` (the recommendation), `option 2`, and the biggest difference in one sentence. The owner replies `N. em` or `N. 2`.

Follow Architecture Decision Records in `docs/DECISIONS/` when making structural choices.
The review rotation in ADR 0008, and the role split in its amendment dated 2026-11-11, are superseded by the amendment dated 2026-11-12.
