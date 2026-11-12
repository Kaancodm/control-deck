.PHONY: lint test test-server validate reference canary loadtest set-password unlock-login install start stop status update backup retention restore-probe

set-password:
	uv run python scripts/set_password.py

unlock-login:
	uv run python scripts/unlock_login.py --target configured --confirm-unlock

lint:
	uv run ruff check --no-cache .
	uv run ruff format --check .
	uv run mypy app/
	uv run python scripts/check_rules.py

test:
	uv run pytest

reference:
	uv run python scripts/run_reference.py

canary:
	uv run python scripts/live_canary.py $(ARGS)

loadtest:
	uv run python scripts/loadtest.py $(ARGS)

# Sequential local test-DB gate; no real provider or server acceptance run.
test-server:
	uv run python scripts/loadtest.py --test-server $(ARGS)

validate:
	uv run python scripts/validate.py task tests/unit/examples/task_valid_01.json
	uv run python scripts/validate.py result tests/unit/examples/result_valid_01.json

install start stop status update:
	uv run python -m app.operations.service $@ $(ARGS)

backup retention restore-probe:
	uv run python -m app.operations.main $@ $(ARGS)
