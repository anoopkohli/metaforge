PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install test lint fmt seed evals up down clean

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -e ".[dev]"

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

fmt:
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

# Dev-only secret, set explicitly so nothing runs with a baked-in default.
seed:
	METAFORGE_AUTH_SECRET=dev-only-secret $(BIN)/python -m core.cli seed

evals:
	$(BIN)/python -m evals.run

up:
	docker compose up -d

down:
	docker compose down

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache *.egg-info metaforge.db
