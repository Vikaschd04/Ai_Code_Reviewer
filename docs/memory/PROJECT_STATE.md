# Project state

Updated: 30 September 2026.

Product: **refactorX** (working name Code Review Platform; internal `crp` identifiers kept, ADR 0011) — upload-first code review/intelligence web application with later agentic investigation and verified fixes. Inputs: ZIP and explicit local-folder snapshot (implemented); Git later. Universal identity: immutable snapshot/content hashes, optional Git metadata.

Stack (implemented): Python 3.14 uv workspace — `crp_core` (settings, models + Alembic 0001–0004, filesystem/PostgreSQL artifact stores, workflow gateway), `crp_analysis` (scope policy, ZIP validation, manifests, inventory, Tree-sitter structure, `graph/` extraction + resolution, PMD/ESLint/Opengrep/Trivy adapters, catalog `crp-rules-v2`, normalization + correlation, lifecycle classifier, JSON/SARIF reports), `crp_api` (FastAPI incl. issues/reports/graph routes), `crp_worker` (Temporal: diagnostic, intake, scan with graph job, engine cache, issue lifecycle; `inline.py` runs the same activities without Temporal for the lite profile), `crp_devtools` (`crp-dev`, incl. `benchmark`), `crp_runner`; pnpm — `@crp/web` (dark-first UI), `@crp/contracts`, `@crp/eslint-runner`. Engines in `.local/engines` via `make engines`: PMD 7.27.0, Opengrep 1.30.0, Trivy 0.69.3 + offline DB (~1.3 GB). PostgreSQL 18.6 + Temporal CLI dev server native. Decisions: ADR 0001–0008.

Implemented and verified: P00, P01, P02 COMPLETE (macOS arm64). P02 adds: owned Opengrep rules, offline Trivy vulns/secrets, cross-engine correlation, durable issues with strict recheck states and triage/exceptions, scan comparison, JSON/SARIF exports (schema-validated), snapshot graph with classified evidence-backed edges and bounded APIs, per-file engine cache, Architecture/Issues/Compare UI. Evidence: docs/validation/P0{0,1,2}_REPORT.md.

Not implemented: AI (P03), SAP/Salesforce packs, fixes, Git, optional intake modes, engine OS sandbox, cache eviction, semantic (classpath/type-checker) resolution.

Deployment: free public trial on Render free (`render.yaml`, lite profile: one process, no Temporal, artifacts in PostgreSQL, Trivy DB baked in; ADR 0010), free alternative GitHub Codespaces, paid `deploy/render-standard.yaml`. Single-user hosted mode; the container serves UI + API + analyzers. CI builds the Linux image and smoke-tests the standard profile and the lite profile under 512 MB / 0.1 CPU. The live Render service awaits the owner (docs/DEPLOYMENT.md). Repo: github.com/Vikaschd04/Ai_Code_Reviewer.

UX (standing owner rule, 30 September 2026): reviewer-first UI — plain language, technical details collapsed, admin/dev pages hidden from reviewers, light/dark/mobile screenshot review (docs/UI_SPEC.md). Demo account (shared `demo` workspace, quotas) and one-click sample project ("Sample: Online store") exist for trials; disable the demo before real customer code (ADR 0011).

Active phase: P03 NOT_STARTED. Next task: P03-01 provider-independent model adapter, no-credentials state and explicit source-egress policy (prompts/P03_AGENTIC_ANALYSIS.md).

Blockers: live AI provider validation needs a user-approved provider account and data-egress policy (not available); offline P03 components can proceed. Disk is tight (~6 GB free).

Always verify current files/tests before trusting this memory. Keep history in validation reports.
