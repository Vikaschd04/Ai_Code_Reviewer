# Phase P02 validation report — Persistent mappings and broader analysis

- Date: 27 September 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Worktree identity: not a Git repository. Source digest `df19e18c2f287045e1676ae99a4fcfadc9c8e79807f536cea9e4c4085f26b7d6` = SHA-256 over sorted (path, file SHA-256) of 240 files (`packages/{core,analysis}/{src,tests}`, `services`, `tools`, `apps/web/src|e2e`, `engines/eslint-runner` sources, `fixtures`, contracts, root manifests and lockfiles) at the final gate.
- Environment: macOS 26.5.2 arm64 (Apple M2, 8 cores, 8 GB); Python 3.14.3; Node 22.22.0; PostgreSQL 18.6; Temporal CLI 1.9.1 (Server 1.32.0); Java 17.0.12; PMD 7.27.0; ESLint 10.11.0 + typescript-eslint 8.70.1; **Opengrep 1.30.0; Trivy 0.69.3 with offline DB `UpdatedAt` 2026-09-26T01:14Z**; Tree-sitter 0.26.0; jsonschema 4.26.0 (tests); cosign v3.1.3 (signature verification); Google Chrome for Playwright. Versions/licences: docs/TOOLCHAIN.md, docs/ENGINE_ADOPTION.md.
- Dataset: synthetic fixtures only — `fixtures/projects/{seeded-mixed,clean-mixed,security-mixed,graph-mixed}` (fake credentials generated at test time, never committed) and the generated benchmark project `synthetic-medium-v1`. Raw logs: `.local/validation-logs/p02-*`, benchmark JSON: `.local/benchmarks/p02-20260927T064312Z.json` (both ignored).

## Implemented behavior

- **Opengrep and Trivy** (ADR 0007): pinned, SHA-256- and Sigstore-verified binaries installed by `make engines` (`crp-dev engines --verify-signatures`). Opengrep runs only the 10 platform-owned rules with `--disable-nosem`; Trivy runs offline (`vuln,secret`), with trusted secret config and an empty ignore file so repository config is inert; secret match text is stripped before storage. Missing binary/DB → UNAVAILABLE with a reason; DB staleness in diagnostics, UI and `make doctor`.
- **Normalization and correlation** (`crp_analysis/normalize.py`): nullable lines with `anchor_kind` (`source_span`/`file`/`dependency`), engine identity text (e.g. `package@version`) for dependency fingerprints, per-engine guidance and details preserved. `correlation_key` groups the same rule family at the same path/line/occurrence slot across engines (catalog `crp-rules-v2`, 74 rules with families); distinct occurrences never collapse; every observation is kept and exposed as "also reported by".
- **Schema** (migration `0003`): findings (nullable span with CHECKs that refuse a source span without lines, anchor kind, correlation key, family, details, guidance, issue link), `issues` + `issue_events`, `graph_builds`/`graph_nodes`/`graph_edges` with composite FKs to the snapshot's file entries and one current build per snapshot, `engine_cache`, engine-run enabled rules/config fingerprint/cache counters, scan cache mode and lifecycle flag. Upgrade test from 0002 with data, downgrade test, `alembic check`.
- **Issue lifecycle** (`crp_worker/lifecycle.py`, `crp_analysis/lifecycle.py`, ADR 0008): durable issues per fingerprint; strict recheck (VERIFIED_PRESENT / VERIFIED_ABSENT / NOT_RECHECKED / UNKNOWN / RULE_OBSOLETE); RESOLVED only after a verified absence; reopening; exceptions with reason and expiry (≤ 1 year) and automatic lapse; audit events; newest-snapshot-wins application; triage API with optimistic versions.
- **Comparison and exports** (`crp_api/routes/reports.py`, `crp_analysis/reports.py`): scan-to-scan comparison with the same classifier and per-engine compatibility notes; JSON export (`crp-scan-export/v1`, own JSON Schema) and SARIF 2.1.0 derived from it, including engines that did not run.
- **Graph** (`crp_analysis/graph/`, `crp_worker/graph_job.py`, `crp_api/routes/graph.py`): Tree-sitter relation extraction (Java package/imports/static/wildcard/extends/implements; TS/JS imports, re-exports, require, dynamic import, heritage and import bindings), defensive manifest parsing (pom.xml without DTDs, package.json, JSONC tsconfig), deterministic resolution with classification and reasons, superseding builds, bounded summary/search/neighborhood/impact APIs.
- **Caching** (`crp_worker/engine_cache.py`): per-file results for PMD, ESLint, Opengrep and graph facts keyed by content plus engine/rule/config/normalization scope; `refresh` full rescans; Trivy explicitly not cacheable.
- **UI** (`apps/web`): pipeline with six analysis stages, engine cards with rule counts, cache reuse and Trivy DB age; findings with dependency locations, "also reported by", issue status and filters; finding page with dependency panel, correlated findings and triage panel with history; Compare tab (group tiles + tables + engine compatibility); JSON/SARIF download; Re-run vs Full rescan; project Issues tab (status tiles, recheck filters); Architecture tab (build tiles, classification legend with counts, module map SVG + table, node search, radial neighborhood SVG + relation table with evidence, impact list with caveats). Reviewed in light and dark themes from E2E screenshots; layout issues found in review were fixed (table overflow, wrapped pipeline, unreadable graph labels, overlapping module boxes, stale header badge after triage).
- **Tooling**: `crp-dev benchmark` / `make benchmark` (isolated stack, generated medium fixture, cold and warm scans, worker-tree RSS sampling, API latencies); `make doctor` checks Opengrep/Trivy and DB freshness; E2E harness ships security and graph fixture archives.

## Mandatory checks (P02 prompt)

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Graph anchors exist in the relevant snapshot | Composite FKs (node/edge file anchors → file entries of the build's snapshot; nodes → build of the same snapshot); `test_graph_anchors_must_belong_to_the_build_snapshot`, `test_every_anchor_is_a_snapshot_file` | **PASS**: cross-snapshot anchors rejected by PostgreSQL | p02-final-test.log |
| Failed reparsing cannot present stale links as current facts | `test_graph_build_apis_and_failed_rebuild_hides_stale_links`: build, then a refresh scan with the extractor crashing (labelled monkeypatch), then recovery | **PASS**: failed build becomes current, summary `failed` with no modules, node APIs 409 `graph_build_failed`; recovery serves a new current build | p02-final-test.log |
| Authorized users see only their project nodes/source/exports | Foreign-workspace rows probed: export, compare (foreign base), graph summary/nodes, project issues, issue GET/PATCH; node of snapshot A requested through snapshot B | **PASS**: all 404 (existence not revealed); cross-snapshot node 404 | test_p02_analysis.py |
| Partial analyzer coverage | Snapshot B with an unparseable `cart.ts`; classifier matrix | **PASS**: its ESLint issues NOT_RECHECKED (not resolved); comparison lists them as not rechecked | test_p02_analysis.py, test_lifecycle_reports.py |
| Duplicate families | ESLint `no-eval` + Opengrep eval on the same call; PMD `HardCodedCryptoKey` + Opengrep hard-coded key | **PASS**: one correlation key per construct, both observations kept, "also reported by" in API/UI | test_security_engines.py, E2E |
| Different flows / distinct occurrences | Two `eval()` calls on one line plus one elsewhere | **PASS**: three distinct correlation keys per engine; cross-engine keys match pairwise | test_security_engines.py |
| Configuration-only change | Same snapshot, Opengrep `max_target_bytes` changed | **PASS**: Opengrep 0 cache hits, other engines full hits; cache-key unit tests per input | test_p02_analysis.py, test_engine_cache_keys.py |
| Rule-version change | ESLint runner copy with `no-eval` removed (real rule-set change) | **PASS**: ESLint 0 hits, new rule-set hash, `no-eval` issues RULE_OBSOLETE and still OPEN | test_p02_analysis.py |
| Deletions/renames | Snapshot B deletes `CryptoUtil.java` (a rename is a delete + add: path is part of identity) | **PASS**: its issues UNKNOWN, status OPEN (never "fixed"); comparison lists them as unknown; they return to VERIFIED_PRESENT in snapshot C | test_p02_analysis.py |
| Verified absence | Snapshot B removes `debugger;` | **PASS**: VERIFIED_ABSENT → RESOLVED with events; reopens in snapshot C; older-snapshot rescan does not move state | test_p02_analysis.py |
| Bounded graph queries | Neighborhood `depth=3` rejected; `limit=5` truncates; impact depth ≤5 / limit ≤500; edge cap per level | **PASS** | test_p02_analysis.py |
| Exports validated against actual schemas | Official OASIS SARIF 2.1.0 schema (draft-04) and the platform JSON Schema (2020-12) on synthetic and real scan exports | **PASS**: 0 validation errors; invalid variants rejected | test_lifecycle_reports.py, test_p02_analysis.py, E2E download |
| Real additional engines on positive/negative examples | Opengrep: all 10 owned rules fire on positives, none on negatives, `nosem` ineffective; Trivy: CVE-2021-44228 (log4j-core 2.14.1), CVE-2020-8203 (lodash 4.17.15), GitHub token and private key secrets; clean fixture 0 findings; missing DB/binary UNAVAILABLE | **PASS** | test_security_engines.py |
| Benchmark on a documented medium fixture | `make benchmark` (below) | **PASS** (recorded; no scale claim) | .local/benchmarks/ |
| Responsiveness via background jobs and pagination | Scans run in Temporal workers; findings/issues/nodes/coverage paginated; single-request latencies below | **PASS** | benchmark JSON |
| No AI inference promoted to a graph fact | No model is called anywhere; heuristic matches are labelled `inferred` | **PASS** | code review, ADR 0008 |
| Phase 1 workflows still work without model credentials or Git | P01 pipeline tests and E2E; migrated P01 dev data scanned again on the live stack | **PASS** | p02-final-test.log, p02-final-e2e.log |

## Benchmark (`make benchmark`, Apple M2, 8 GB, isolated stack)

Fixture `synthetic-medium-v1`: 1,010 files (600 Java in 4 Maven modules, 400 TypeScript in 2 npm workspace packages, manifests), 59,019 lines, 1.25 MB. Intake (upload → frozen snapshot): 1.07 s.

| Scan | Wall time | Worker tree peak RSS | Findings | Per engine (ms / cache) |
|---|---|---|---|---|
| Cold (`use`, empty cache) | 8.71 s | 650.7 MB | 652 | PMD 2,704 · ESLint 2,180 · Opengrep 3,662 · Trivy 1,054 (engines run in parallel); graph 1,612 nodes / 1,826 edges |
| Warm (same snapshot) | 4.08 s | 277.6 MB | 652 (same fingerprints) | PMD/ESLint/Opengrep/graph 100 % cache hits; Trivy 828 (not cacheable); structure re-parsed |

Single-request API latency after the scans: findings page 13.8 ms, issues page 7.9 ms, graph summary 8.3 ms, node search 6.7 ms, neighborhood depth 2 12.0 ms, impact depth 5 9.8 ms (427 ms with the first recursive-CTE implementation; replaced by bounded BFS), compare 31.1 ms, JSON export 31.4 ms, SARIF export 36.6 ms. One machine, one fixture, sequential requests: not an enterprise-scale or SLO claim. RSS is sampled every 0.25 s and can miss short spikes.

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint/format/types/contracts | `make check` | exit 0 — ruff (147 files), mypy strict (108 files), OpenAPI + TS drift clean, tsc, ESLint strict, Prettier |
| Tests | `make test` | exit 0 — 265 pytest (core 73, analysis 117, api 29, worker 16, devtools 17, runner 13; 0 skipped) + 14 vitest |
| Browser E2E | `make test-e2e` | exit 0 — 10/10 (foundation 6, P01 2, P02 2) on an isolated stack |
| Live stack | `make dev` restart (dev DB migrated 0002 → 0003 with P01 data), `make doctor` | doctor 0 failing: PMD, ESLint, Opengrep 1.30.0, Trivy 0.69.3 available; 1 warning (offline Trivy DB past NextUpdate); a new scan of the migrated P01 project: PARTIAL, 26 findings, 26 issues, graph current; pre-0003 scan still readable and exportable |
| Engine supply chain | `crp-dev engines --verify-signatures` and a clean reinstall into a scratch directory | cosign verified Opengrep (signature + certificate) and Trivy (Sigstore bundle); SHA-256 pins matched |

## Semantic-resolution gaps (explicit)

- Relations are syntax-level. No Java classpath, compiled classes, Maven/Gradle resolution, installed `node_modules` or TypeScript type checker is used, so: method calls and field/type usages are not edges; types provided by dependencies stay `declared`/`inferred`/`unresolved`; overloads, generics, DI, reflection, dynamic dispatch and runtime configuration are invisible.
- Java: package-to-artifact mapping for dependencies is a groupId-prefix heuristic (`inferred`); when groupId ≠ package (e.g. Guava) the import stays `unresolved` rather than guessed. Parent-POM inheritance, properties, profiles and `dependencyManagement` are not evaluated; Gradle builds are reported, not parsed.
- TypeScript/JavaScript: `tsconfig` `extends` chains, project references, `package.json` `exports`/`main` fields, npm workspace globs and CommonJS `module.exports` shapes are not followed; default imports resolve to the target file (`inferred`), not to a declaration; non-literal `require`/`import()` are `unresolved (dynamic)`.
- Impact is computed at file/module level; unresolved edges can hide dependents (the API states their count).
- Dataflow/taint "flows" are not analyzed in P02 (Opengrep rules are intra-file patterns); correlation distinguishes locations and occurrences, not flows.

## Security and data integrity

Workspace authorization on every new endpoint (404 for non-members, MEMBER role for triage). Engine binaries pinned and signature-verified; Trivy offline, telemetry disabled; repository tool configuration inert; secret values never stored and redacted in excerpts; untrusted XML manifests with DTDs/entities refused; JSON/JSONC parsed without execution. Graph anchors and issues are project/snapshot-scoped by foreign keys. Cache is project-scoped and keyed by content, so no result crosses a project boundary.

## Remaining gaps (not blocking the P02 mandatory gate)

- `engine_cache` has no eviction beyond project deletion (P02-F1).
- Findings from scans made before migration 0003 are not linked to issues (only scans finalized after 0003 link findings).
- The Trivy DB refresh is manual (`make engines`); a DB update changes Trivy's rule-set hash, so absent vulnerabilities become UNKNOWN until re-observed or triaged.
- Engine binaries are pinned for macOS arm64 only; Linux/Windows untested.
- No OS-level sandbox for engines (carried from P01, K-P01-01).
- Exception expiry is applied at the next lifecycle-applying scan (read-time `exception_expired` flag in between).
- Structure extraction is not cached (cheap, re-parsed each scan). Module map draws up to 24 modules (table beyond).
- Not a Git repository; `/security-review` and `/code-review` not run.

## Phase decision

All mandatory P02 checks passed on macOS arm64. **P02 COMPLETE.** Next runnable task: **P03-01 — provider-independent model adapter with a no-credentials "unavailable" state and an explicit project source-egress policy** (`prompts/P03_AGENTIC_ANALYSIS.md`). Live provider validation needs an approved account/policy from the user; until then that integration gate is BLOCKED while offline components proceed.
