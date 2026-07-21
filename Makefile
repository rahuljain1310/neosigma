.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose
API_PORT ?= 8000
OPTIMIZER_MODE ?= heuristic

.PHONY: help install env up down logs restart dev test format openapi client clean

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies (uv sync)
	$(UV) sync

env: ## Copy .env.example to .env if missing
	@test -f .env || cp .env.example .env
	@echo ".env ready"

up: env ## Start all services (db + api) in Docker
	$(COMPOSE) up -d --build

down: ## Stop and remove containers
	$(COMPOSE) down

logs: ## Follow service logs
	$(COMPOSE) logs -f

restart: down up ## Restart all services

dev: install env ## Run API locally with hot reload (Postgres must be running)
	OPTIMIZER_MODE=$(OPTIMIZER_MODE) $(UV) run uvicorn app.main:app --reload --port $(API_PORT)

test: install ## Run tests
	OPTIMIZER_MODE=heuristic $(UV) run pytest -q

format: install ## Format Python with ruff
	$(UV) run ruff format .

openapi: install ## Export OpenAPI spec to openapi.json
	$(UV) run python scripts/export_openapi.py

client: ## Run test_client.py against Harbor (real Terminal-Bench runs)
	$(UV) run python test_client.py --executor harbor --max-iterations 15 --patience 5

client-debug: ## Run test_client with optimizer trace/diff debug output (Harbor)
	$(UV) run python test_client.py --executor harbor --max-iterations 15 --patience 5 --debug

debug-job: ## Dump debug artifacts for JOB_ID=... (e.g. make debug-job JOB_ID=abc)
	@test -n "$(JOB_ID)" || (echo "Usage: make debug-job JOB_ID=<job-id>" && exit 1)
	$(UV) run python scripts/debug_job.py $(JOB_ID)

clean: down ## Stop containers and remove Python caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
