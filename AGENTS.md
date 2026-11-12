# Agent rules (Control Deck)

These rules apply to all automated contributors (Claude for planning, Cursor for builds,
SuperGrok for code review, Grok Bot for merges).

## Security and privacy

- Do **not** commit secrets, API keys, tokens, or credentials.
- Do **not** commit personal data (PII), real names, e-mail addresses, or internal identifiers.
- Do **not** commit IP addresses, hostnames, or infrastructure-specific URLs in code or docs.
- Do **not** open network connections to production systems or user machines unless explicitly requested.

Agents must read [COORDINATION.md](COORDINATION.md) first.

## Dependencies and scope

- Do **not** add new runtime or dev dependencies without a clear justification in the pull request description.
- Prefer minimal, focused changes that match the requested stage of the project.
- Keep code, comments, and commit messages in **English** (Conventional Commits).
- Keep `README.md` and everything under `docs/` in **German (de-CH, no Eszett)**.

## Quality

- Run `make lint` and `make test` before marking work complete.
- Add or update tests when changing validation, schemas, or core behavior.
- Only **Grok Bot** merges, and only after the owner says yes once. Other agents and bots, including Fable, do not merge.

## Workflow

Codex and Gemini Pro are not used.

1. The owner gives the goal in one sentence.
2. Claude plans and writes a short build ticket with a check command.
3. Cursor builds in a branch. The check command must pass.
4. SuperGrok reviews the code only.
5. The owner says yes once. Then Grok Bot merges.

Exception: Claude (including Fable) builds only complex tasks, and only when the job brief explicitly says so.

Follow Architecture Decision Records in `docs/DECISIONS/` when making structural choices.
The review rotation in ADR 0008 is superseded by the amendment dated 2026-11-11.
