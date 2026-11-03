# Review criteria

Checklist for `/code-review` and for the `reviewer` agent (`.claude/agents/reviewer.md`).
Correctness and safety only; style is covered by `ruff` and `mypy`. A finding names the file
and line, the violated rule, and the smallest fix. Merge needs an explicit GO from the owner.

## 1. Security

- No server, deployment, or network changes in this repository. No commands against
  production hosts, no database roles, no `tailscale`, `systemd`, or `docker` changes in code.
- Input from tasks (instructions, inputs, file paths) is untrusted: path traversal, shell
  injection, and oversized payloads must be rejected by validation, not by convention.
- Cloud workers are called only through the official CLIs (`claude -p`, `codex exec`).
  Reject any API-key based client, any OAuth token reuse, and any new network endpoint.

## 2. Secrets and privacy

- No keys, tokens, passwords, or private keys in code, tests, fixtures, docs, or examples.
  Reject `.env` content, `*.pem`, `*.key`, and anything under `secrets/`.
- No personal data: no real names, e-mail addresses, phone numbers, IBANs, IPs, or hostnames.
  Example data uses obvious placeholders.
- `.env.example` lists variable names only, never values.

## 3. Test-database rule

- Integration tests and destructive Alembic commands use `DECK_TEST_DATABASE_URL` only.
  The database name must end in `_test`; `app/db/config.py:get_test_database_url` enforces it.
- Reject tests, fixtures, or CI steps that read `DECK_DATABASE_URL`, bypass the `_test` check,
  or run `alembic downgrade` without the test URL.
- Without `DECK_TEST_DATABASE_URL` the integration tests must skip, not fail.

## 4. Data classes

- ADR 0005: `data_class` is **mandatory** on `task.v1` (no schema default). Values:
  `local_only`, `cloud_redacted`, `cloud_ok`. Reject payloads or APIs that infer or omit it.
- Legacy only: the DB column may still default to `local_only` for existing rows; do not treat that
  as permission to skip the field in new `task.v1` JSON.
- `local_only` content must never be included in a payload for a cloud lane (`claude`, `codex`),
  including knowledge-base context and attachments. `cloud_redacted` is pseudonymised first.
- A data-gate hit sets the task to `blocked` and records the hits; it never silently downgrades.

## 5. Migrations

- Every Alembic revision has a working `downgrade()` that restores the previous schema.
  `upgrade` then `downgrade` then `upgrade` must succeed on the test database.
- Schema and ORM models agree (`alembic check` is clean). Constraints added in SQL are mirrored
  in `app/db/models.py`.
- No data-destroying migration without an explicit note in the PR under "Bewusst offen".

## 6. Status model

- `app/db/state_machine.py` is the single source of truth. Target:
  `awaiting_approval -> queued -> blocked | leased -> running -> review -> done`, branches
  `failed`, `cancelled`, `expired`. New transitions go into the table, emit an audit event, and
  come with a unit test; forbidden transitions must keep raising.
