.PHONY: lint test validate

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app/

test:
	uv run pytest

validate:
	uv run python scripts/validate.py task tests/unit/examples/task_valid_01.json
	uv run python scripts/validate.py result tests/unit/examples/result_valid_01.json
