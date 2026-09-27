# Phase P01 validation report — Source upload, folder capture and real baseline

- Date: 26 September 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Worktree identity: not a Git repository. Source digest `9859f3049821d2fb4dd8adb29547f2ee6081a600c90c1a5257d9df8e06c2d7ce` = SHA-256 over sorted (path, file SHA-256) of 171 files (`packages/core/src`, `packages/analysis/src`, `services`, `tools`, `apps/web/src|e2e`, `engines/eslint-runner` sources, `fixtures`, contracts, root manifests and lockfiles) at the final gate.
- Environment: macOS 26.5.2 arm64; Python 3.14.3; Node 22.22.0; PostgreSQL 18.6; Temporal CLI 1.9.1 (Server 1.32.0); Java 17.0.12 (for PMD); PMD 7.27.0; ESLint 10.11.0 + typescript-eslint 8.70.1; Tree-sitter 0.26.0; Google Chrome for Playwright. Versions/licenses: docs/TOOLCHAIN.md.
- Dataset: synthetic fixtures only (`fixtures/projects/seeded-mixed`, `clean-mixed`; see fixtures/README.md). Raw logs: `.local/validation-logs/p01-*` (ignored).

## Implemented behavior

- **ZIP intake** (`crp_api/routes/intakes.py`, `crp_analysis/zip_intake.py`, `crp_worker/intake.py`): create → streamed PUT (limit on actual bytes, 415/413, temp file removed on abort) → idempotent finalize (single Temporal workflow per intake) → validation before any storage (traversal, absolute/drive/backslash paths, control characters, symlink/special entries, encryption, unsupported compression, case/Unicode/file-vs-dir collisions, entry count, declared and actual expanded bytes, compression ratio, CRC) → content-addressed storage of accepted text → frozen snapshot with canonical manifest digest and persisted per-entry dispositions. Raw archive deleted after validation. States CREATED/UPLOADING/VALIDATING/READY/REJECTED/FAILED/CANCELED; expired intakes cancelled opportunistically.
- **Scope policy** `scope-v1` (`crp_analysis/policy.py`), published at `GET /v1/intake-policy`: VCS/vendor/build/IDE/tool directories, secret candidates and minified bundles are EXCLUDED (recorded, never stored or analyzed); binaries/nested archives recorded but not stored; oversized text recorded; agent instruction files categorized as untrusted data.
- **Local runner** `crp-runner capture` (`tools/local-runner`): fetches the server policy, skips excluded paths locally (secrets never leave the machine), records symlinks/special/unreadable entries, never writes to the folder, detects changes during capture (bounded retries), builds a deterministic ZIP outside the folder, discloses the upload and requires confirmation (`--dry-run`, `--yes`), uploads through the same intake API with a declared manifest that the server verifies (mismatch → REJECTED).
- **Inventory** (`crp_analysis/inventory.py`): languages/lines/categories, build and framework indicators with version confidence (Maven/Gradle Java version, Spring Boot, npm engines, TypeScript, React/Vue/Angular/Express/NestJS/LWC, tsconfig target, sfdx sourceApiVersion, SAP Commerce descriptors). XML with DOCTYPE/entities refused; nothing executed.
- **Structure** (`crp_analysis/structure.py`): Tree-sitter Java/JS/TS/TSX symbols (types, methods, functions, imports) with exact spans; parse status OK/PARTIAL/FAILED per file; symbol budget disclosed.
- **Engines** (ADR 0006): real PMD and ESLint adapters with trusted rulesets, in-source suppressions neutralised, bounded subprocesses, UNAVAILABLE/FAILED/PARTIAL/SUCCEEDED/NOT_APPLICABLE outcomes, raw reports stored with SHA-256.
- **Scans** (`crp_worker/scan.py`, `crp_api/routes/scans.py`): idempotent creation (`Idempotency-Key`), durable workflow with parallel engine activities, transactional publication of engine run + per-file coverage + findings, cancellation, SSE progress with monotonic IDs and resume, summaries with limitations. Findings: fingerprint, engine/rule versions, canonical severity/category, exact span, static guidance, evidence note; source excerpts with best-effort secret masking.
- **UI redesign + P01 screens** (`apps/web`): dark-first "deep space" design system with light theme toggle, sidebar/topbar shell, dashboard (stat tiles, project cards with severity stacked bars), projects, project workspace (ZIP drag-and-drop with progress, runner instructions, snapshots, scans), snapshot scope review, live scan pipeline, engine coverage meters, severity/category charts, limitations, findings with filters, finding detail with highlighted source and rule guidance, coverage table, operations. Charts follow the dataviz method (status colours with glyph + label, 2 px gaps, legends with counts, tooltips).

## Mandatory acceptance

| Gate | Procedure | Outcome | Evidence |
|---|---|---|---|
| ZIP and local-folder intake of equivalent content yield equivalent manifests/findings | `test_zip_and_folder_intake_are_equivalent_and_findings_persist` (live uvicorn API, real worker/Temporal/PG/PMD/ESLint); runner uses `capture_and_upload` over real HTTP | **PASS**: identical manifest SHA-256; identical finding fingerprint sets; both scans PARTIAL | p01-final-test.log |
| Real scanner results survive service restart | Same test re-opens a new API instance over the DB; plus the live dev stack was restarted twice via `make dev` and the UI-walkthrough scan was re-read | **PASS**: identical finding count; dev project "Billing platform 2728" → PARTIAL, 24 findings after restart | test log; curl output in session |
| Original directory unchanged | `test_original_folder_is_unchanged` (content hash, mtime, mode of every entry before/after) | **PASS** | test log |
| Finding → exact source span | E2E `uploads a ZIP, reviews scope, scans and navigates to exact source` | **PASS**: row → `InvoiceService.java:10`, highlighted line 10 contains `status == "PAID"` | p01-final-e2e.log |
| Failed engine keeps completed findings and reports incomplete coverage | `test_failed_engine_keeps_completed_results` (labelled crashing PMD test double + real ESLint) | **PASS**: scan PARTIAL, PMD FAILED `engine_crashed`, ESLint findings present, PMD coverage NOT_ATTEMPTED, limitation listed | test log |
| No AI key or GitHub account | whole suite | **PASS**: none configured or used | — |
| Real PMD/ESLint on seeded and clean fixtures | `test_engines.py` | **PASS**: expected rule IDs (10 PMD, 10 ESLint) found; clean fixture 0 findings SUCCEEDED | test log |

## Required tests (P01 prompt / SOURCE_INTAKE.md)

| Case | Test(s) | Result |
|---|---|---|
| Traversal, absolute, drive, backslash, control chars, depth/length | `test_malicious_paths_are_rejected` (10 cases); E2E traversal rejection | PASS |
| Symlinks | ZIP symlink entry rejected; runner records symlinked file/dir without following | PASS |
| Normalized path collisions | case, NFC/NFD, file-vs-directory, duplicate | PASS |
| Oversized / aborted input | upload > limit (declared and streamed) → 413 and temp removed; client disconnect mid-body → intake stays CREATED, nothing stored; archive > limit rejected | PASS |
| Resource quotas | entry count, expanded bytes (actual), zip-bomb ratio, per-file text limit (OVERSIZED) | PASS |
| Encrypted / non-ZIP / truncated / CRC mismatch | explicit codes | PASS |
| Duplicate finalize | finalize twice → same intake, single workflow | PASS |
| Unauthorized IDs | random IDs → 404 for intakes/scans/findings/snapshots/files; another workspace's intake/project → 404 | PASS |
| Local file mutation | change during capture retried; persistent change fails | PASS |
| Unreadable entry | runner discloses `unreadable` | PASS |
| Exclusions | `.env`, keys, `.git`, `node_modules`, minified, binaries — recorded, not stored | PASS |
| Empty / clean source | README-only snapshot → engines NOT_APPLICABLE, SUCCEEDED; clean fixture → 0 findings | PASS |
| Unsupported syntax | `Broken.java`/`broken.ts` → FAILED coverage (PMD/ESLint), PARTIAL parse (structure) | PASS |
| Scanner crash / timeout / cancel / malformed output / unavailable | engine tests + pipeline cancel (scan CANCELED within seconds) | PASS |
| Source escaping | `CodeView.test.tsx` (HTML/script rendered as text); names escaped via `safe_display` | PASS |
| User-visible errors | E2E rejection message with code and offending entry | PASS |
| Uploaded instruction files do not change policy | `AGENTS.md` fixture: ruleset SHA-256 unchanged, findings still reported, UI warns it is untrusted | PASS |
| Deterministic hash | order-independent digest; deterministic runner ZIP | PASS |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint/format/types/contracts | `make check` | exit 0 — ruff (121 files), mypy strict (91 files) clean, OpenAPI + TS drift clean, tsc, ESLint strict, Prettier |
| Tests | `make test` | exit 0 — 203 pytest (core 70, analysis 65, api 29, worker 9, devtools 17, runner 13; 0 skipped) + 12 vitest |
| Browser E2E | `make test-e2e` | exit 0 — 8/8 (foundation 6, P01 2) on isolated stack |
| Live stack | `make dev`, `make doctor` | doctor 0 failing incl. `analyzer pmd 7.27.0 available`, `analyzer eslint 10.11.0 available`; schema 0002 |
| UI inspection | Headless Chrome tour: login, dashboard (dark + light), add source, snapshot, scan running/done, finding, coverage | Inspected; fixed code-panel overflow and PMD ruleset attribution found during review |

## Security and data integrity

Workspace authorization on every new endpoint (`get_scoped`, 404 for non-members). Archive validated before storage; excluded secrets never stored; source excerpts masked for likely secrets (best-effort); engines receive only copies, scrubbed env, no platform credentials; repository config/inline directives/instruction files cannot change rules. Workflows carry IDs only. Raw engine reports have work-dir paths redacted.

## Remaining gaps (not blocking the P01 mandatory gate)

- Optional intake modes not implemented: browser folder selection, registered server mounts.
- No OS-level CPU/memory/network sandbox for engines on macOS (wall time, output and heap caps only) — required before multi-tenant hosting (P07).
- User scope overrides, finding triage (`PATCH /findings`), scan comparison/lifecycle across scans, JSON/SARIF export: P02.
- Content-addressed blob garbage collection and a scheduled intake-expiry job are not implemented (expiry runs opportunistically on intake creation).
- Secret masking is heuristic; dedicated secret scanning arrives with later engines.
- Linux/Windows untested; not a Git repository.

## Phase decision

All mandatory P01 gates passed on macOS arm64. **P01 COMPLETE.** Next runnable task: **P02-01 — persistent snapshot graph and bounded queries** (`prompts/P02_GRAPH_AND_ANALYZERS.md`).
