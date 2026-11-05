# Agent rules (Control Deck)

These rules apply to all automated contributors (Cursor Cloud Agent, Claude Code, Codex, etc.).

## Security and privacy

- Do **not** commit secrets, API keys, tokens, or credentials.
- Do **not** commit personal data (PII), real names, e-mail addresses, or internal identifiers.
- Do **not** commit IP addresses, hostnames, or infrastructure-specific URLs in code or docs.
- Do **not** open network connections to production systems or user machines unless explicitly requested.

## Dependencies and scope

- Do **not** add new runtime or dev dependencies without a clear justification in the pull request description.
- Prefer minimal, focused changes that match the requested stage of the project.
- Keep code, comments, and commit messages in **English** (Conventional Commits).
- Keep `README.md` and everything under `docs/` in **German (de-CH, no Eszett)**.

## Quality

- Run `make lint` and `make test` before marking work complete.
- Add or update tests when changing validation, schemas, or core behavior.
- Agents must **not** merge pull requests on their own; a human owner gives final GO. After
  the owner's explicit GO for a specific PR, an agent may bring that PR to merge only as
  defined in ADR 0010 (auto-merge, or a plain merge when the required check is already green),
  never for the paths ADR 0010 excludes.

## Review workflow

- Implementation may use Cursor; review should rotate across providers (e.g. Codex, Claude) per project policy.
- Follow Architecture Decision Records in `docs/DECISIONS/` when making structural choices.
