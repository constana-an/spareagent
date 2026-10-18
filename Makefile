COMPOSE ?= docker compose
# gid of the host Docker socket (Linux CI); macOS Docker Desktop works with gid 0.
export DOCKER_GID ?= $(shell stat -c %g /var/run/docker.sock 2>/dev/null || echo 0)
RUN_TEST = $(COMPOSE) run --rm -T test

.PHONY: help up down build migrate seed-dev test test-unit lint format validate-openapi smoke e2e-remote e2e-code sandbox-image logs clean

help:
	@echo "Targets: up down build migrate seed-dev test test-unit lint format validate-openapi smoke logs clean"

build:
	$(COMPOSE) build api

sandbox-image:
	$(COMPOSE) --profile build-only build sandbox-image

up:
	$(COMPOSE) up --build -d --wait postgres redis api api-tls
	$(COMPOSE) up -d worker scheduler

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) exec -T api python scripts/migrate.py

seed-dev:
	$(COMPOSE) exec -T api python scripts/seed_dev.py

# Full suite (unit + integration + e2e) against the compose PostgreSQL/Redis, isolated
# database `spareagent_test` and Redis DB 15. Coverage gate covers the core packages.
test: build sandbox-image
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

# Week 2: three remote workers over HTTPS, ten tasks, one worker killed mid-run.
# Writes reports/week2_e2e_report.json.
e2e-remote:
	python3 scripts/e2e_remote_demo.py

# Week 3: sandboxed deterministic code fix + independent verification.
# Writes reports/week3_e2e_report.json.
e2e-code: sandbox-image
	python3 scripts/e2e_code_demo.py

logs:
	$(COMPOSE) logs --tail=200 api api-tls scheduler worker

clean:
	$(COMPOSE) --profile remote --profile code down -v
