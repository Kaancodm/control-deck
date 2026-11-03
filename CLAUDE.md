# CLAUDE.md

Project rules live in [AGENTS.md](AGENTS.md) and in `docs/DECISIONS/` (ADRs). This file adds
commands, conventions, and gotchas for Claude Code. If anything here conflicts with `AGENTS.md`,
`AGENTS.md` wins.

Work on **one task per session**; run `/clear` before starting the next one.

## Stack

- Python 3.12, FastAPI, uv (dependency and run manager), SQLAlchemy 2, Alembic.
- PostgreSQL. The production database `controldeck` runs on port 5433 and is managed
  outside this repository. Agents never create roles, touch servers, or connect to it.
- Tooling: pytest, ruff (lint and format), mypy in strict mode.

## Commands

```bash
uv sync --all-extras          # install runtime and dev dependencies
make lint                     # ruff check . && ruff format --check . && mypy app/
make test                     # uv run pytest
uv run ruff check .           # lint only
uv run ruff format .          # apply formatting (CI runs --check)
uv run pytest                 # unit tests; integration tests skip without a test DB URL
uv run alembic upgrade head   # apply migrations (see Database below for which URL is used)
make reference                # run the reference case suite (tests/reference/cases)
make validate                 # validate example task/result payloads
```

Run `make lint` and `make test` before declaring work complete. `make lint` includes mypy, so
type errors fail lint even when ruff is clean.

## Database and tests

- Integration tests use **only** `DECK_TEST_DATABASE_URL`. The database name **must end in
  `_test`**; `app/db/config.py:get_test_database_url` refuses anything else.
- Never point tests or `alembic downgrade` at `DECK_DATABASE_URL`.
- Without `DECK_TEST_DATABASE_URL` the integration tests are skipped and `make test` stays green.
- CI starts `postgres:17` on port 5432, creates `controldeck_test`, then runs
  `alembic upgrade head`, `alembic check`, ruff, mypy, and pytest.
- Alembic picks the URL in `alembic/env.py`: on `main` it prefers `DECK_TEST_DATABASE_URL` when
  set, otherwise `DECK_DATABASE_URL`.
- TODO: open PR #5 changes this. After it merges, pass `-x target=test` for test migrations
  (`uv run alembic -x target=test upgrade head`) and Alembic aborts when both URLs are set
  without a target.
- Migrations live in `alembic/versions/`. Downgrades are for dev/test databases only.

## Domain rules

### Task status model (Plan v1.1 plus `awaiting_approval`)

- Target states: `awaiting_approval -> queued -> blocked | leased -> running -> review -> done`,
  with branches `failed`, `cancelled`, `expired`.
- `awaiting_approval` is the **initial state before `queued`**. A new task waits there until the
  owner approves it (`approve -> queued`). `review` stays after `running` as in Plan v1.1.
- Every transition is an audit event (`task.<trigger>`); forbidden transitions must raise.
- Implementation status: the state machine in `app/db/state_machine.py` on `main` has
  `queued, leased, running, succeeded, failed, blocked, cancelled`. PR #5 adds
  `awaiting_approval` (migration `0002`, `requires_approval`, `approved_at`). `review`, `done`,
  and `expired` are not in the enum yet; the code uses `succeeded` where the plan says `done`.
  Check the current enum before relying on a state name.

### Data classes

- `data_class` values: `local_only` (default), `cloud_redacted`, `cloud_ok` (ADR 0005).
- `task.v1` requires the field explicitly; the database column defaults to `local_only`.
- `local_only` payloads must never reach a cloud lane. `cloud_redacted` is pseudonymised before
  leaving the host. Hits of the data gate set the task to `blocked`.

### Lanes and workers

- Worker enum: `ollama`, `claude`, `codex`. No other lanes without an ADR.
- Cloud workers are called through the **official CLIs only** (`claude -p`, `codex exec`),
  never through API keys (ADR 0006). No API keys in the repo, in `.env.example`, or in runner
  environments. Tests mock CLI calls or use local Ollama.

## Language and style

- Code, comments, docstrings, and commit messages: **English**.
- Commits follow **Conventional Commits** (`feat(db): ...`, `fix(api): ...`, `docs: ...`).
- `README.md` and everything under `docs/`: **German (de-CH), no Eszett** (write `ss`).
- Keep `schemas/*.json` and `app/schemas/*.json` identical; Pydantic models in `app/core/schemas.py`
  must mirror the JSON Schema (CI tests check parity).
- `task.v1` and `result.v1` are frozen. Extensions go into a `v2` next to them.

## Human gates (hard rules)

- **Merge only after an explicit GO from the repository owner.** Agents never merge.
- **No server changes**, no deployments, no database roles, no network connections to
  production or user machines.
- No new runtime or dev dependencies without a justification in the PR description.
- No secrets, tokens, personal data, real names, e-mail addresses, IPs, or hostnames in code
  or docs.
- Open pull requests as **draft** and fill in `.github/pull_request_template.md`
  (Etappe, Ziel, Abnahme-Checkliste, Tests, Reviewer-Anbieter, Bewusst offen).

## Gotchas

- `ruff format --check` runs in CI. Run `uv run ruff format .` before committing.
- mypy is `strict = true` for `app/`; tests may be untyped.
- Reference cases live in `tests/reference/cases/NNN-<slug>.yaml`; the case `id` must equal the
  file stem.
- `uv run` uses the project `.venv`; do not install packages globally.
- Branch naming for stage work: `stage/NN-<slug>`. Review provider rotates per stage (ADR 0008).

## References

- Plan v1.1 (architecture and 18 stages): Google Doc ID `1RLpgiZpNknISYLdiGTMEllgVo9OIXsZ73DANPQt2XCk`.
- ADRs: `docs/DECISIONS/0001` to `0008`.
- Database notes: `docs/DATENBANK.md`. Reference suite: `docs/REFERENCE_CASES.md`.
- Review criteria: `REVIEW.md`. Build tickets: `/plan-ticket` (template `tasks/TEMPLATE.md`); session handover: `/handoff`.
