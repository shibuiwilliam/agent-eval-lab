.PHONY: check test lint fmt sync

sync:
	uv sync --all-extras

lint:
	uv run ruff check . && uv run isort --check-only . && uv run ruff format --check . && uv run mypy src

test:
	AGENTEVAL_LLM_MODE=replay uv run pytest -q

check: lint test

fmt:
	uv run ruff check --fix . && uv run isort . && uv run ruff format .
