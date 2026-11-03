---
description: Write a build ticket (Bauauftrag) for a stage into tasks/ using tasks/TEMPLATE.md
argument-hint: <stage-number-or-slug> [short goal]
---

Write a build ticket for: $ARGUMENTS

Steps:

1. Read `tasks/TEMPLATE.md`. If it does not exist, create it first with exactly the sections
   listed below, then continue.
2. Read `CLAUDE.md`, `AGENTS.md`, and the relevant ADRs in `docs/DECISIONS/`. Look up the stage
   in Plan v1.1 (Google Doc ID in `CLAUDE.md`) if the caller did not paste the stage text.
3. Explore the code paths the stage touches (Glob, Grep, Read) so every file you name exists or
   is explicitly marked `(new)`.
4. Fill the template and save it as `tasks/NN-<slug>.md` (two-digit stage number, kebab-case slug).
   Do not modify any other file.

Rules for the ticket:

- **Atomic steps**: one change per step, each independently committable, numbered.
- **Explicit files**: every step names the file paths it creates or edits. No "and related files".
- **Acceptance as an executable command**: each acceptance criterion is a shell command
  (`uv run pytest tests/...`, `make lint`, `uv run alembic -x target=test upgrade head`, a
  `grep` that must return nothing, ...) plus the expected result. No prose-only criteria.
- Respect the hard rules: no merge, no server changes, no new dependencies without justification,
  mandatory explicit `data_class` on every `task.v1` (ADR 0005; DB default `local_only` is legacy
  only), tests only against the `_test` database, English code and commits, de-CH docs without Eszett.
- Keep the ticket under 120 lines. Mark unknowns as `TODO` instead of guessing.

Finish by printing the path of the ticket and the list of files it touches.
