# Code Review Platform

An upload-first code intelligence, review and validated-remediation application for Java, JavaScript/TypeScript, SAP Commerce and Salesforce.

**Current state: Phases 0–2 are implemented and their mandatory gates passed on macOS — see the [P00](docs/validation/P00_REPORT.md), [P01](docs/validation/P01_REPORT.md) and [P02](docs/validation/P02_REPORT.md) reports.** You can upload a ZIP or capture a local folder, review the frozen scope, run real PMD, ESLint, Opengrep and Trivy scans, triage durable issues, compare scans, export JSON/SARIF, and explore an evidence-backed architecture graph. Analysis is source-only (no build/runtime verification) and no AI is involved yet.

## What works today

- **Source intake:** ZIP upload (streamed, bounded, attack-resistant) and `crp-runner capture` for a local folder (secrets and dependency/build output skipped on your machine); both produce the same content-addressed snapshot.
- **Scope review:** every entry accounted for (analyzable, excluded with reason, binary, oversized), languages, build/framework indicators with version confidence.
- **Baseline analysis:** Tree-sitter structure, PMD 7.27.0 and ESLint 10.11.0 with platform-owned rules that uploaded code cannot disable; per-file coverage; failures and partial results shown honestly; live progress and cancellation.
- **Findings:** severity/category, exact span (or dependency anchor without an invented line), fingerprint, engine/rule versions, static or advisory guidance, masked source excerpt, cross-engine "also reported by" correlation.
- **Security engines:** Opengrep 1.30.0 with platform-owned rules and Trivy 0.69.3 (known-vulnerable dependencies and exposed secrets), both pinned and signature-verified; Trivy runs fully offline and secret values are never stored.
- **Issues:** durable issues across scans with triage (owner, accepted risk with expiry, false positive) and strict recheck states — an issue is resolved only when a compatible scan verifies its absence.
- **Comparison and exports:** new / still present / verified absent / not rechecked / unknown / rule obsolete between any two scans; schema-validated JSON and SARIF 2.1.0 downloads.
- **Architecture:** snapshot graph of modules, files, types and relations with source evidence and resolved/declared/inferred/unresolved classification; bounded neighborhood and impact views with table equivalents.
- **Caching:** per-file results reused only for identical content, engine, rules and configuration; full rescans on demand.
- **Modern UI:** dark-first "deep space" design with a light theme, dashboard, upload with progress, live scan pipeline, charts and source viewer.
- Loopback-only, token-authenticated local deployment: FastAPI API, Temporal worker, React UI, PostgreSQL 18, Temporal dev server.
- Readiness that checks the real dependencies (database + schema revision, Temporal namespace, worker pollers, artifact-store write/read probe), shown in the UI and by `make doctor`.
- A diagnostic Temporal workflow that proves API → Temporal → worker → artifact store/DB execution.
- Workspace-scoped projects (create/list/get) with database-enforced scope-compatible foreign keys for sources, snapshots and scans.
- Filesystem artifact store with symlink/traversal containment; artifacts live outside the repository.
- Generated API contract (`packages/contracts`) consumed by a typed web client; drift is checked.
- Developer commands (`make bootstrap|dev|doctor|migrate|seed-fixtures|check|test|test-e2e|benchmark|package|context-map|context-pack`).
- Planned features appear in the UI navigation as disabled items with the phase that delivers them.

## Deployment

Repository: <https://github.com/Vikaschd04/Ai_Code_Reviewer>. Free: open the repository in **GitHub Codespaces** — the complete application (web UI, API, worker, analyzers, PostgreSQL) starts automatically and prints its URL. Paid, always on: the Render Blueprint `render.yaml`. Both are a single-user mode signed in with one access token; every push to `main` runs CI (which also starts the app exactly as Codespaces does). Setup steps and the security posture: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Quickstart (macOS, verified)

```sh
brew install uv pnpm postgresql@18 temporal   # plus Python 3.14, Node 22 and a Java 17+ runtime (for PMD)
make bootstrap        # also downloads and verifies PMD, Opengrep, Trivy + its offline DB (~1.3 GB)
make dev
# other terminal: uv run crp-dev token --show, then open http://127.0.0.1:5173
# create a project, then drop a ZIP on "Add source" (or use crp-runner capture)
```

Full instructions, configuration and troubleshooting: [INSTALLATION.md](docs/INSTALLATION.md). Versions and licenses: [TOOLCHAIN.md](docs/TOOLCHAIN.md).

## Repository layout

| Path | Contents |
|---|---|
| `apps/web` | React + TypeScript UI (Vite), unit tests and Playwright E2E |
| `services/api` | FastAPI control plane (`crp-api`) |
| `services/worker` | Temporal worker (`crp-worker`) |
| `packages/core` | Settings, models, Alembic migrations, artifact store, workflow contracts |
| `packages/analysis` | Scope policy, ZIP validation, manifests, inventory, Tree-sitter structure, PMD/ESLint adapters, rule catalog |
| `engines/eslint-runner` | Isolated ESLint analyzer with the platform's trusted configuration |
| `fixtures` | Synthetic test projects (seeded and clean) |
| `packages/contracts` | Generated OpenAPI JSON and TypeScript types |
| `tools/devtools` | `crp-dev` developer CLI and real-infrastructure pytest fixtures |
| `tools/local-runner` | `crp-runner ping` and `crp-runner capture` (local folder snapshot + upload) |
| `docs/` | Specification, ADRs, status, memory and validation reports |

## Planned user journey

Create project → upload ZIP or capture a selected folder → inspect scope → start scan → see real progress/coverage → inspect code mappings and issues → investigate with AI when configured → generate/validate/export a patch → optionally connect Git later. See [roadmap](docs/ROADMAP.md), [features](docs/FEATURE_MATRIX.md) and [source intake](docs/SOURCE_INTAKE.md).

## Tests

`make check` (lint, format, strict types, contract drift), `make test` (unit + integration against real ephemeral PostgreSQL/Temporal), `make test-e2e` (browser tests against an isolated stack).

## Limitations

Local development plus a single-user hosted mode (GitHub Codespaces for free, or Render); not a multi-tenant or SSO deployment. Engines run without a per-scan OS sandbox. The Linux container image is built and smoke-tested in CI; Windows is untested. Temporal runs as the single-node dev server. Graph relations are syntax-level (no classpath or type checker). Next: Phase 3 (bounded AI review; requires an approved provider and data-egress policy). See [known issues](docs/memory/KNOWN_ISSUES.md).

## Project quality

See [architecture](docs/ARCHITECTURE.md), [coding standards](docs/CODING_STANDARDS.md), [test strategy](docs/TEST_STRATEGY.md), [security](SECURITY.md), [completion criteria](docs/DEFINITION_OF_DONE.md) and [current phase](docs/PHASE_STATUS.md).
