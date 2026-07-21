.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose
API_PORT ?= 8000
OPTIMIZER_MODE ?= heuristic

.PHONY: help install env up down logs restart dev test openapi client clean

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

openapi: install ## Export OpenAPI spec to openapi.json
	$(UV) run python scripts/export_openapi.py

client: ## Run test_client.py (requires AOS_API_KEY and running API)
	$(UV) run python test_client.py --executor simulated --max-iterations 3 --patience 2

clean: down ## Stop containers and remove Python caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
