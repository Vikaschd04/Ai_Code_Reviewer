# Architecture

Status: target architecture, with the P00 implementation described in "Implemented foundation (P00)" below. Sections without an implementation note are still targets.

## Implemented foundation (P00, 26 September 2026)

```
browser ──(same origin, HttpOnly cookie)──► Vite dev/preview server ──/v1 proxy──► crp-api (FastAPI, 127.0.0.1:8710)
                                                                                 │  ├─ PostgreSQL 18 (async SQLAlchemy + psycopg)
crp-runner / curl ──(Bearer token)───────────────────────────────────────────────┘  ├─ Temporal client ──► Temporal dev server (127.0.0.1:7233)
                                                                                    └─ ArtifactStore (filesystem, outside repo)
crp-worker ◄── polls task queue `crp-main` ── Temporal ; worker uses the same ArtifactStore + PostgreSQL
```

| Component | Actual module | Implemented behavior |
|---|---|---|
| apps/web | `@crp/web` | Sign-in, capability-driven navigation, readiness table, workflow diagnostic, projects list/create |
| services/api | `crp_api` | Health/readiness, capabilities, local session auth, projects, diagnostics; structured errors; loopback guard |
| services/worker | `crp_worker` | `DiagnosticWorkflow` + activities (artifact round trip, DB schema probe, worker identity) |
| packages/core | `crp_core` | Settings, logging/redaction, domain states, models + Alembic, artifact store, workflow gateway |
| packages/contracts | `@crp/contracts` | Generated OpenAPI JSON + TypeScript types (ADR 0003) |
| tools/devtools | `crp_devtools` | `crp-dev` commands, local infra management, context utilities, real-infra pytest fixtures |
| tools/local-runner | `crp_runner` | `ping` connectivity/auth check only |
| packages/analysis, packages/adapters, infra, top-level tests | — | **Not created yet** (P01+); tests live beside each package |

P01 additions: `packages/analysis` (`crp_analysis`: policy, ZIP validation, manifests, inventory, Tree-sitter structure, engine adapters, catalog, normalization), `engines/eslint-runner` (isolated ESLint), `IntakeWorkflow` and `ScanWorkflow` in the worker (engine activities run in parallel over read-only copies under `CRP_WORK_ROOT`), and the `crp-runner capture` client. Data flow: upload/capture → artifact store (transient archive) → worker validation → content-addressed blobs + manifest + `file_entries` → scan workflow → per-engine coverage/findings (+ raw reports) → API/SSE → UI.

P02 additions (ADR 0007, 0008): Opengrep and Trivy adapters; a `graph` pseudo-engine in `ScanWorkflow` (`crp_worker.graph_job`: per-file Tree-sitter facts from the syntax cache → snapshot-wide resolution in `crp_analysis.graph.resolve` → superseding `graph_builds` publication); a project-scoped per-file engine cache (`crp_worker.engine_cache`) consulted before engines run; issue lifecycle at finalize (`crp_worker.lifecycle` using the pure classifier `crp_analysis.lifecycle`); API routes `issues`, `reports` (compare, JSON/SARIF export via `crp_analysis.reports`) and `graph` (bounded queries in PostgreSQL). Scan data flow now: snapshot → parallel engine activities (cache hits reused, misses executed) + graph build → findings with correlation keys → finalize (scan state, graph failure handling, issue lifecycle) → API/UI/exports.

Seams: the API depends on `WorkflowGateway` (Temporal implementation in `crp_core.workflows.temporal`) and `ArtifactStore` (filesystem implementation) protocols, and on `IdentityProvider` for authentication (ADR 0004). Workflow payloads contain IDs, hashes and small probe results only. Configuration is typed and validated in `crp_core.config.Settings`. Topology decisions: ADR 0003–0005.

## Boundaries

The trusted development repository contains application instructions and source. Uploaded projects live outside it as untrusted artifacts. The control plane authorizes work; the data plane processes snapshots under restricted permissions. Original source directories are never writable by analyzers or patch agents.

## Components

| Component | Responsibility |
|---|---|
| apps/web | React/TypeScript screens, authenticated API client, source/diff/graph UI |
| services/api | FastAPI endpoints, auth, validation, projects/sources/scan policies |
| services/worker | Temporal activities and execution-adapter orchestration |
| packages/contracts | Canonical API/result schemas and generated client boundary |
| packages/analysis | Inventory, mappings, normalization and deterministic policies |
| packages/adapters | Source, parser, framework, engine, model and validation adapters |
| tools/local-runner | User-invoked folder capture and explicit snapshot upload |
| infra | Local services, pinned worker images, deployment definitions |
| tests | Unit, contracts, integration, malicious intake, E2E and benchmarks |

These are proposed implementation directories, not pre-existing modules. Choose a coherent Python packaging/workspace structure in Phase 0 and record it in an ADR.

## Data flow

Source capture → immutable manifest/snapshot → inventory → parser/engine tasks → coverage and raw reports → normalized findings/graph → optional AI review → independently checked evidence → dashboard. Repair reads a snapshot and writes a separate patch artifact. Git publication is a later integration.

The API returns job IDs promptly, with cursor-based result APIs and resumable progress events. Workflows carry references/hashes, never source text. Activities are idempotent, cancellable and bounded. Duplicate delivery cannot duplicate scans or findings.

PostgreSQL is authoritative for lifecycle and permissions. Object/artifact storage holds snapshots, raw output and patches. Start graph edges relationally; add a specialized graph backend only after profiling. Embeddings are optional derived artifacts with the same deletion and authorization scope as source.

## Execution topology

Phase 0/1 uses a loopback-only single-user development deployment with authenticated local access and isolated approved analyzer processes/containers. A local launcher can use the developer's existing container engine, but untrusted containers/API containers never receive its socket or privileged access. Treat this as a development boundary, not hostile multi-tenant certification.

Hosted multi-tenant release requires qualified stronger isolation or dedicated customer runners, network/credential segregation, tenant fairness and tested cleanup. Analyzer installation/build dependencies run only in intended worker contexts. Builds/tests are untrusted execution too.

## State correctness

Identity is workspace + project + snapshot + policy/tool version. Graph references never silently cross snapshots. A failed reparse invalidates or marks stale the affected graph; a failed scanner does not resolve old issues. Query authorization applies before retrieval and aggregation.

## Configuration

Use typed server configuration with documented defaults, validation and environment overrides. Store provider endpoints, policies, budgets and source registration separately from uploaded files. Runtime feature flags must describe real supported capabilities and expose unavailable reasons.

