# ADR 0003 — Workspace layout, packaging and contract generation

Status: accepted and implemented, 26 September 2026 (P00). Owner: development agent for the repository maintainer.

Context and constraints: ARCHITECTURE.md proposed `apps/web`, `services/api`, `services/worker`, `packages/*`, `tools/local-runner`. P00 needs one authoritative API schema, a documented client-generation strategy, and no placeholder modules for future analysis code.

Decision:

- Python is one **uv workspace** (`pyproject.toml` root is virtual; `uv.lock` pins everything). Members:
  - `packages/core` → `crp_core`: typed settings, logging/redaction, domain states, SQLAlchemy models, Alembic migrations (packaged inside the module), artifact-store contract + filesystem backend, workflow contracts/gateway + Temporal implementation.
  - `services/api` → `crp_api` (FastAPI control plane, `crp-api` entry point).
  - `services/worker` → `crp_worker` (Temporal worker, `crp-worker`).
  - `tools/devtools` → `crp_devtools` (`crp-dev`: local services, doctor, migrate, fixtures, context utilities, E2E stack, packaging, pytest infrastructure plugin).
  - `tools/local-runner` → `crp_runner` (`crp-runner`; depends only on httpx so users can install it without server code).
- Web is one **pnpm workspace**: `apps/web` (`@crp/web`) and `packages/contracts` (`@crp/contracts`).
- **Contracts:** Pydantic models in `crp_api.schemas`/`crp_api.errors` are authoritative. `crp-dev contracts` exports deterministic `packages/contracts/openapi.json`; `openapi-typescript` generates `packages/contracts/src/v1.d.ts`; the web client uses it through `openapi-fetch`. `make check` and `services/api/tests/test_contract.py` fail on drift. Workflow payloads are Pydantic models in `crp_core.workflows.contracts`, serialized by Temporal's Pydantic data converter.
- `packages/analysis` and `packages/adapters` are **not created** until P01 needs them (no dead scaffolding).

Alternatives considered: separate virtualenvs per service (duplicated pins, harder cross-package typing); hand-written TypeScript types (drift risk); JSON Schema as the primary source (more tooling, no benefit while FastAPI is the only producer); Poetry/PDM (uv already available, fast, lockfile-first).

Consequences and reversal: one lock covers all Python services, so a dependency upgrade is repo-wide. Adding a service = adding a workspace member. Generated files are committed and must be regenerated with `make contracts` after schema changes. Moving to another package manager requires re-locking only.

Evidence: `make check` (contract drift checks), `services/api/tests/test_contract.py`, `make package` builds wheels for all five members; see docs/validation/P00_REPORT.md.

Affected contracts/phases/tests: all API endpoints; P01 adds `packages/analysis`/adapters as new workspace members.
