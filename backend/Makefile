# Convenience targets. On Windows, run the underlying commands directly or use
# Git Bash / WSL.
.PHONY: help install dev seed seed-core reset reembed validate-data test test-unit test-cov lint format typecheck verify run worker beat docker-up docker-down clean

VENV := .venv
PY   := $(VENV)/bin/python
ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
endif

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install runtime + dev dependencies
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

dev: install  ## Install optional AI provider SDKs as well
	$(PY) -m pip install -r requirements-ai.txt

seed:  ## Seed users, the core places and the 2,000-place world corpus
	$(PY) -m scripts.seed_db

seed-core:  ## Seed without the world corpus (Paris, Rome and Tokyo only)
	$(PY) -m scripts.seed_db --no-world

reset:  ## Drop the database and reseed everything
	$(PY) -m scripts.seed_db --reset

reembed:  ## Re-embed places into the configured embedding space (idempotent)
	$(PY) -m scripts.reembed

validate-data:  ## Check the world data for structural and geographic errors
	$(PY) -m scripts.validate_world_data

run:  ## Start the API with auto-reload
	$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

worker:  ## Start a Celery worker
	$(PY) -m celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=solo

beat:  ## Start the Celery scheduler
	$(PY) -m celery -A app.workers.celery_app.celery_app beat --loglevel=info

test:  ## Run the whole test suite
	$(PY) -m pytest

test-unit:  ## Run only the tests that need no database
	$(PY) -m pytest tests/test_units.py tests/test_optimization.py

test-cov:  ## Run tests with a coverage report
	$(PY) -m pytest --cov=app --cov-report=term-missing --cov-report=html

verify:  ## Check every endpoint against a running server
	$(PY) -m scripts.verify_endpoints

lint:  ## Lint
	$(PY) -m ruff check app tests scripts

format:  ## Auto-format and fix what can be fixed
	$(PY) -m ruff format app tests scripts
	$(PY) -m ruff check --fix app tests scripts

typecheck:  ## Type-check
	$(PY) -m mypy app

docker-up:  ## Start the full stack
	docker compose up --build -d

docker-down:  ## Stop the stack and remove volumes
	docker compose down -v

clean:  ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -exec rm -rf {} + 2>/dev/null || true
