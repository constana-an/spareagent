COMPOSE ?= docker compose
RUN_TEST = $(COMPOSE) run --rm -T test

.PHONY: help up down build migrate seed-dev test test-unit lint format validate-openapi smoke logs clean

help:
	@echo "Targets: up down build migrate seed-dev test test-unit lint format validate-openapi smoke logs clean"

build:
	$(COMPOSE) build api

up:
	$(COMPOSE) up --build -d --wait postgres redis api
	$(COMPOSE) up -d worker

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) exec -T api python scripts/migrate.py

seed-dev:
	$(COMPOSE) exec -T api python scripts/seed_dev.py

# Full suite (unit + integration + e2e) against the compose PostgreSQL/Redis, isolated
# database `spareagent_test` and Redis DB 15. Coverage gate covers the core packages.
test: build
	$(RUN_TEST) pytest --cov --cov-report=term --cov-fail-under=80

test-unit: build
	$(RUN_TEST) pytest tests/unit

lint: build
	$(RUN_TEST) ruff check .
	$(RUN_TEST) ruff format --check .
	$(RUN_TEST) python scripts/validate_openapi.py

format: build
	$(RUN_TEST) ruff format .

validate-openapi: build
	$(RUN_TEST) python scripts/validate_openapi.py

smoke:
	bash scripts/smoke.sh

logs:
	$(COMPOSE) logs --tail=200 api worker

clean:
	$(COMPOSE) down -v
