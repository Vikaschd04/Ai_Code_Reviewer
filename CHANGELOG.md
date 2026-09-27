# Changelog

## Unreleased — Complete application on Render (27 September 2026)

- The container serves the built web UI on the API's origin (`CRP_WEB_STATIC_DIR`, strict CSP, immutable hashed assets); hosted mode defaults the web origin to the service's own address, so the Render Blueprint needs no input.
- `render.yaml` now deploys everything (service `ai-code-reviewer` + PostgreSQL); `vercel.json` removed; Dockerfile without BuildKit heredocs (`deploy/fetch_temporal.py`).
- CI container smoke test also checks the served UI and its CSP.

## Unreleased — Git, CI and single-user hosted deployment (27 September 2026)

- Repository on GitHub (`Vikaschd04/Ai_Code_Reviewer`); GitHub Actions CI (lint, types, contracts, unit tests, web build, Linux image build + container smoke test), actions pinned to commit SHAs.
- Hosted tier `CRP_ENVIRONMENT=hosted`: Host allowlist, https-only origins (+ preview regex), Secure cookies, HSTS, token ≥ 32 characters; platform database URLs normalized.
- Direct archive uploads with 15-minute single-intake tickets (`POST /v1/intakes/{id}/upload-ticket`); the web UI always uploads via tickets.
- `Dockerfile` (digest-pinned bases, SHA-256/cosign-verified Linux engines), `crp-dev hosted` single-container entrypoint (Temporal on SQLite, migrations, daily offline Trivy DB refresh, supervised API/worker, unprivileged user), `render.yaml` Blueprint, `vercel.json` (proxy + CSP), docs/DEPLOYMENT.md, ADR 0009.
- ESLint runner: `typescript` is now a runtime dependency (required by typescript-eslint).

## Unreleased — Phase 2 persistent mappings and broader analysis (27 September 2026)

- Opengrep 1.30.0 with 10 platform-owned rules and Trivy 0.69.3 (offline vulnerabilities + secrets), SHA-256 and Sigstore verified; repository tool config inert; secret values never stored (ADR 0007).
- Migration `0003`: lineless dependency/file anchors, correlation keys, issues and audit events, graph builds/nodes/edges, per-file engine cache, cache mode and lifecycle flags.
- Cross-engine correlation ("also reported by") without collapsing distinct occurrences; catalog `crp-rules-v2`.
- Durable issues with strict recheck states, triage with optimistic versions, exceptions with expiry; scan comparison; JSON (`crp-scan-export/v1`) and SARIF 2.1.0 exports validated against their schemas (ADR 0008).
- Snapshot graph: Tree-sitter relation extraction, deterministic resolution with classifications and reasons, superseding builds (failed rebuilds hide older links), bounded summary/search/neighborhood/impact APIs.
- Per-file engine cache keyed by content + engine/rule/config/normalization scope; `cache_mode=refresh` full rescans.
- UI: Architecture, Issues and Compare views, triage panel, dependency details, cache and DB-age indicators, JSON/SARIF downloads.
- `make benchmark`; `make doctor` checks Opengrep/Trivy and DB freshness.

## Unreleased — Phase 1 source intake and baseline analysis (26 September 2026)

- ZIP intake: streamed bounded upload, validation before storage (traversal, symlinks, collisions, encryption, bombs, CRC), idempotent finalize, frozen content-addressed snapshots, manifest with per-entry dispositions; migration `0002`.
- `crp-runner capture`: local folder capture with server-published scope policy, local exclusion of secrets/VCS/vendor/build output, change detection, deterministic archive, disclosure + confirmation, server-verified manifest.
- Inventory with version confidence; Tree-sitter Java/JS/TS structure with parse status.
- Real PMD 7.27.0 and ESLint 10.11.0 adapters with trusted rulesets, neutralised in-source suppressions, bounded execution, cancellation; per-file coverage; fingerprinted findings with static guidance (ADR 0006).
- Scans with idempotency keys, SSE progress, cancellation, limitations; findings/coverage/finding-detail APIs with masked source excerpts.
- Redesigned web UI (dark-first design system with light theme, dashboard, projects, upload, scope review, live scan pipeline, findings, source evidence, coverage).
- `make engines`; doctor reports analyzer availability.

## Unreleased — Phase 0 foundation (26 September 2026)

- Added uv (Python 3.14) and pnpm workspaces: `crp_core`, `crp_api`, `crp_worker`, `crp_devtools`, `crp_runner`, `@crp/web`, `@crp/contracts` (ADR 0003).
- PostgreSQL 18 schema `0001` for workspaces, users, memberships, projects, sources, snapshots and scans with scope-compatible composite foreign keys.
- Loopback-only local-token authentication with HttpOnly browser sessions, Origin checks, throttling and an identity-provider boundary (ADR 0004).
- Real readiness checks (database/schema, Temporal, worker pollers, artifact store) and a diagnostic Temporal workflow.
- Filesystem artifact store with traversal/symlink containment, outside the repository.
- React UI: sign-in, readiness, workflow diagnostic, projects, capability-driven navigation.
- Generated OpenAPI + TypeScript contract with drift checks.
- `make bootstrap|dev|doctor|migrate|seed-fixtures|check|test|test-e2e|package|context-map|context-pack`; native PostgreSQL/Temporal and real ephemeral test infrastructure (ADR 0005).
- No source intake, analysis engines or findings yet (Phase 1).

## Specification kit

- Defined upload-first/local-folder intake and phased code intelligence platform.
- Added master/phase prompts, shared agent instructions and documentation seeds.
