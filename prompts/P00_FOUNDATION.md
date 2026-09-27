# Execute Phase 0 — Runnable foundation

Implement this phase now in the current project. Follow AGENTS.md and MASTER_DEVELOPMENT_PROMPT.md. Inspect existing files/worktree before edits; preserve user changes. Read ARCHITECTURE, DATA_MODEL, CODING_STANDARDS, SECURITY_MODEL, INSTALLATION and the compact memory as needed. Do not stop after producing a plan or more documentation.

## Deliver

1. Inspect runtimes, OS, network and package availability. Select compatible supported releases using official documentation; record exact versions and lockfiles in TOOLCHAIN.md.
2. Create a coherent React/TypeScript web app, FastAPI API, Python packages, worker and local runner package boundary. Shared contracts need one authoritative schema and a documented client generation strategy.
3. Add PostgreSQL migrations for the initial workspace/project/source/snapshot/scan boundary; apply migrations in an actual test/local database.
4. Implement loopback-only authenticated local development with a generated secret outside source control. Make nonlocal insecure startup fail. Define later identity-provider boundaries.
5. Implement artifact-store and workflow interfaces, a filesystem development backend and a working Temporal connection/worker. A real small diagnostic workflow can prove execution; do not return fabricated analysis results.
6. Create health/readiness endpoints that report actual DB/workflow dependencies, and a UI that shows real service readiness without placeholder findings.
7. Create and verify bootstrap/dev/doctor/migrate/check/test/test-e2e/package command equivalents. Implement bounded trusted-development context-map/context-pack utilities as specified in CONTEXT_AND_MEMORY.md.
8. Update README/INSTALLATION with exact successful commands, environment variables, service URLs, OS limitations and troubleshooting.

## Mandatory checks

Typed/lint checks; meaningful API health/auth tests; DB migration test; actual durable workflow/worker smoke check; artifact-store containment test; browser app loads and shows real readiness; context utility excludes secret/customer directories and discloses truncation. No provider account needed.

If a required service cannot run in this host, implement what can be implemented and record the affected gate BLOCKED with exact missing prerequisites. Do not replace PostgreSQL/Temporal integration tests with mocks and call the phase complete.

## Handoff

Write docs/validation/P00_REPORT.md, update phase/backlog/memory and report actual results. End with the next executable P01 task only after the mandatory gate passes. Do not start a deployment or configure an external paid service.

