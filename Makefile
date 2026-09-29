# Developer command contract for refactorX (macOS/Linux, GNU Make >= 3.81).
# Every target delegates to pinned tools: uv (Python), pnpm (web) and `crp-dev` (tools/devtools).
# Paths are relative to the repository root, so a checkout path containing spaces works.

.DEFAULT_GOAL := help
.PHONY: help bootstrap engines dev infra-up infra-down infra-status doctor migrate seed-fixtures \
        contracts check test test-e2e benchmark package context-map context-pack token

SCOPE ?= all
PYTEST_REPORT := .local/test-reports/pytest-junit.xml

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' Makefile | sed -E 's/:.*## /\t/' | sort

bootstrap: ## Install pinned deps, generate local secrets, init the dev PostgreSQL cluster, run doctor
	uv sync --all-packages --locked
	pnpm install --frozen-lockfile
	uv run crp-dev engines
	uv run crp-dev bootstrap-local
	uv run crp-dev doctor

engines: ## Download/verify pinned PMD and check the ESLint runner (analysis engines)
	uv run crp-dev engines

dev: ## Run PostgreSQL, Temporal, API, worker and web UI in the foreground (Ctrl-C stops)
	uv run crp-dev dev

infra-up: ## Start only the dev PostgreSQL cluster and Temporal dev server (background)
	uv run crp-dev infra up

infra-down: ## Stop the dev PostgreSQL cluster and Temporal dev server
	uv run crp-dev infra down

infra-status: ## Show dev service state
	uv run crp-dev infra status

doctor: ## Check tool versions, secrets, DB/Temporal/worker/API state and analyzers
	uv run crp-dev doctor

migrate: infra-up ## Apply migrations to the dev database and provision the local identity
	uv run crp-dev migrate

seed-fixtures: migrate ## Load clearly labelled synthetic fixtures into the dev database
	uv run crp-dev seed-fixtures

contracts: ## Regenerate packages/contracts (OpenAPI JSON + TypeScript types) from the API models
	uv run crp-dev contracts
	pnpm contracts:generate

check: ## Formatting, lint, strict type checks and contract drift checks (Python + web)
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run crp-dev contracts --check
	pnpm --silent --filter @crp/contracts exec openapi-typescript openapi.json 2>/dev/null \
		| diff -q - packages/contracts/src/v1.d.ts
	pnpm typecheck
	pnpm lint
	pnpm format:check

test: ## Tests; SCOPE=all|python|unit|integration|web (integration starts real PG/Temporal)
	@mkdir -p .local/test-reports
ifeq ($(SCOPE),web)
	pnpm test
else ifeq ($(SCOPE),unit)
	uv run pytest -m "not integration"
else ifeq ($(SCOPE),integration)
	uv run pytest -m integration --junitxml=$(PYTEST_REPORT)
else ifeq ($(SCOPE),python)
	uv run pytest --junitxml=$(PYTEST_REPORT)
else
	uv run pytest --junitxml=$(PYTEST_REPORT)
	pnpm test
endif

test-e2e: ## Playwright browser tests against an isolated throwaway stack
	uv run crp-dev test-e2e

benchmark: ## Time/memory of real cold and warm scans on a generated medium fixture (isolated stack)
	uv run crp-dev benchmark

package: ## Build Python wheels/sdists and the web bundle into dist/ with a SHA-256 manifest
	uv run crp-dev package

context-map: ## Bounded development repository map (.local/context/)
	uv run crp-dev context-map

context-pack: ## Scoped context packet: make context-pack TASK=P01-01 PATHS="services/api docs/SOURCE_INTAKE.md"
	@test -n "$(TASK)" || (echo "TASK is required, e.g. TASK=P01-01" && exit 2)
	@test -n "$(PATHS)" || (echo "PATHS is required, e.g. PATHS=\"services/api\"" && exit 2)
	uv run crp-dev context-pack --task "$(TASK)" $(foreach p,$(PATHS),--path "$(p)")

token: ## Show where the local API token is stored (use `uv run crp-dev token --show` to print it)
	uv run crp-dev token
