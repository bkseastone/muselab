.PHONY: test test-fast test-serial lint fmt run

PYTEST_WORKERS ?= auto

test:
	uv run pytest -n $(PYTEST_WORKERS) --dist=worksteal -v

test-fast:
	uv run pytest -n $(PYTEST_WORKERS) --dist=worksteal -x --tb=short

test-serial:
	uv run pytest -v

lint:
	uv run ruff check backend tests
	bash scripts/lint.sh
	bash scripts/check-frontend.sh

fmt:
	@echo "no formatter configured yet; consider adding ruff later"

run:
	uv run uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload
