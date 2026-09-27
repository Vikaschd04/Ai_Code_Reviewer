# Phase P00 validation report — Runnable foundation

- Date: 26 September 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Worktree identity: the directory is **not a Git repository**, so there is no commit. Source digest `e054b3873ce60ff010ed09f56546c10a7cfeec8ac73d58e470da4bf278cb5417` = SHA-256 over sorted (path, file SHA-256) for `packages/core`, `services`, `tools`, `apps/web/src`, `apps/web/e2e`, `packages/contracts/{openapi.json,src}`, root manifests and lockfiles (102 files) at the final gate run.
- Environment: macOS 26.5.2 arm64 (8 CPU, 8 GB), Python 3.14.3, uv 0.12.19, Node 22.22.0, pnpm 11.20.0, PostgreSQL 18.6, Temporal CLI 1.9.1 (Server 1.32.0), Google Chrome for Playwright. Full versions: docs/TOOLCHAIN.md.
- Dataset: synthetic only (no customer source). Raw logs: `.local/validation-logs/` (ignored, local evidence).

## Implemented behavior

- Workspaces and packaging: ADR 0003. API `services/api/src/crp_api`, worker `services/worker/src/crp_worker`, core `packages/core/src/crp_core`, dev tools `tools/devtools/src/crp_devtools`, runner `tools/local-runner/src/crp_runner`, web `apps/web`, contract `packages/contracts`.
- Migration `0001`: workspace/user/membership/project/source/snapshot/scan boundary with composite scope FKs and CHECK constraints (docs/DATA_MODEL.md).
- Local auth: generated token outside source control, bearer + HttpOnly session cookie, Origin checks, throttle, loopback guard, IdP boundary (ADR 0004).
- Artifact store contract + filesystem backend; workflow gateway contract + Temporal implementation; `DiagnosticWorkflow` on a real worker.
- Health/readiness against real dependencies; UI showing real readiness; capability-driven navigation; projects.
- Developer commands and bounded context utilities (docs/INSTALLATION.md, docs/context/README.md).

## Mandatory checks

| Gate/check | Exact command or procedure | Actual outcome | Evidence | Limitation |
|---|---|---|---|---|
| Typed/lint checks | `make check` | **PASS**, exit 0: ruff format (84 files) + lint clean, mypy strict 62 files no issues, OpenAPI drift check OK, TS contract drift OK, tsc (web + contracts), ESLint strictTypeChecked 0 warnings, Prettier clean | final-check.log | — |
| API health/auth tests | `make test` → `services/api/tests` | **PASS** 29/29 (live/ready, 401/403/429, cookie flags, Origin/CSRF, tampered/expired session, non-loopback peer + Host, unmigrated/unreachable DB, validation redaction, project scoping, pagination, contract drift) | pytest-junit.xml | Temporal outage simulated by a labelled test double in these tests; real Temporal covered below |
| DB migration test | `packages/core/tests/test_migrations.py` (in `make test`) | **PASS**: upgrade empty → `0001`, `alembic check` no drift, downgrade to base, re-upgrade; FK/CHECK/unique enforcement; idempotent identity provisioning — on a real ephemeral PostgreSQL 18.6 cluster | pytest-junit.xml | — |
| Migration on local DB | `make migrate` (twice), `make seed-fixtures` (twice) | **PASS**: schema `0001`; fixture "created" then "already present" | migrate-seed.log | — |
| Durable workflow/worker smoke | `services/worker/tests/test_workflow_smoke.py` | **PASS** 3/3: real Temporal dev server + worker + PostgreSQL + artifact store through the real API; readiness reports missing worker honestly, then COMPLETED diagnostic with verified sha256 round trip and cleanup | pytest-junit.xml | Worker runs in-process in this test; the process entry point is covered by `make dev` and E2E |
| Live stack | `make dev`; `make doctor`; `uv run crp-runner ping --token-file …`; curl readiness via Vite proxy; POST/GET diagnostic | **PASS**: doctor "0 failing, 0 warnings"; runner "platform ready"; all four checks `ok`; diagnostic `COMPLETED` on `crp-worker@<host>:<pid>`, artifact 4096 bytes verified and deleted, schema 0001; unauthenticated readiness 401; `Host: attacker.example` 403 | live-stack.log | — |
| Artifact-store containment | `packages/core/tests/test_artifact_store.py` | **PASS** 26 tests: 15 unsafe keys rejected; symlinked dir cannot redirect writes/reads; symlinked file not followed or deleted; symlinked root refused; bounded reads/writes without partial files; owner-only files; untrusted marker; probe leaves no residue; root inside repo refused (test_config) | pytest-junit.xml | Filesystem backend only; S3-compatible backend not built |
| Browser app loads and shows real readiness | `make test-e2e` | **PASS** 5/5 against isolated PostgreSQL/Temporal/API/worker + production build: wrong token rejected; all four readiness checks OK incl. schema 0001; planned features disabled with reasons; diagnostic COMPLETED; project persisted across reload; sign-out | final-e2e.log | Chrome only; other browsers untested |
| UI inspection | Headless Chrome screenshots of sign-in, overview (after diagnostic) and projects on the dev stack | Inspected: statuses use icon + text, no placeholder findings, fixture project labelled "Synthetic fixture", disabled future navigation with phase reasons. Fixed nav/heading label mismatch afterwards | screenshots in session scratchpad (not committed) | Visual check at 1280×900 light theme only |
| Context utility exclusions/truncation | `tools/devtools/tests/test_context.py`; `make context-map`; `make context-pack TASK=P01-01 PATHS=…`; approved `.local/secrets` and `.sf` | **PASS** 17 tests; real map: 177 files, excluded `{generated: 3, vendor_cache_or_local_state: 34}`, truncated false; pack generated then cache hit; `.local/secrets` and `.sf` rejected (exit 2); token string absent from all context output | context-and-refusal.log | Symbols: Python via `ast`, TS/JS via export patterns, Markdown headings |
| Nonlocal insecure startup fails | `CRP_API_HOST=0.0.0.0 uv run crp-api`; `CRP_API_HOST=192.168.1.20 uv run crp-worker` | **PASS**: both exit 2 "Refusing to start: local-token authentication may only bind to a loopback address"; message no longer echoes input values | context-and-refusal.log + rerun | — |
| No provider account needed | All of the above | **PASS**: no model/provider/cloud credentials used or configured | — | — |
| Other suites | `make test` | core 70, devtools 17, runner 5, web vitest 5 — all pass; total pytest 124 passed, 0 failed/errors/skipped | final-test.log | Runner tests use an HTTP test double (unit scope) |
| Packaging | `make package`; install runner wheel into a clean venv | **PASS**: 5 wheels + 5 sdists + web bundle, 15-entry SHA-256 manifest; core wheel contains migrations; standalone `crp-runner --version` and unreachable ping exit 3 | package.log | Not signed; no container images |

## Security and data integrity

Authorization: workspace grants applied before queries; non-member IDs return 404; viewer cannot create (403). Input handling: Pydantic validation with value-free error output. Source preservation: no customer source handled in P00; artifact root forced outside the repo. Isolation: loopback-only local boundary; no analyzer execution exists yet. Secrets: owner-only files, redacted logs/errors, token never placed in browser storage or context packets.

## Environment interventions (recorded for reproducibility)

- Installed with Homebrew: `postgresql@18` 18.6, `temporal` 1.9.1, `uv` 0.12.19.
- `brew postinstall postgresql@18` initially failed (formula DSL newer than Homebrew 6.0.13, since auto-update had been disabled for the install). Fixed by `brew update` (→ Homebrew 7.0.6) and re-running the post-install; it also created Homebrew's own default cluster in `/opt/homebrew/var/postgresql@18`, which this project neither uses nor starts.
- Pinned vitest 5.0.1 instead of 5.0.2 because pnpm 11's minimum-release-age guard flagged 5.0.2 (published the previous day).

## Remaining work and phase decision

Mandatory gates: **all passed**. Phase decision: **P00 COMPLETE** (macOS arm64 only).

Known gaps that do not block P00 (see docs/memory/KNOWN_ISSUES.md): Linux/Windows untested; no container images/Compose; Temporal is the CLI dev server; psycopg LGPL-3.0 needs license review before binary distribution; not a Git repository, so `/security-review` and `/code-review` were not run and commits cannot be referenced; session cookies cannot be individually revoked; worker readiness can lag up to 90 s after a worker stops; source intake, scans, findings, engines and AI are not implemented (P01+).

Next runnable task: **P01-01 — streamed ZIP intake and freeze/manifest** via `prompts/P01_SOURCE_AND_BASELINE.md`.
