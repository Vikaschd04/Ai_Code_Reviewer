# Phase P09 validation report — Compile and build in the portal (tiered)

Date: 9 October 2026. Environment: macOS arm64 (Darwin 25.5), Node 22, TypeScript 5.9.3 (pinned in
`engines/eslint-runner`), Python 3.14, PostgreSQL 18.6, Temporal CLI dev server. Decision record:
[ADR 0017](../adr/0017_TIERED_EXECUTION.md).

Status: **IN_PROGRESS.**
- **Tier 0 is delivered and verified:** a TypeScript type-check in fix workspace checks, with no
  code executed.
- **Tiers 1 and 2 are BLOCKED** on the owner's decision about paid isolated compute (K-P09-01).

Never reported as passed: Java compilation, builds and tests. They show as "not run" or "not
compiled".

## Deliverables (P09 prompt)

| Deliverable | Delivered | Where |
|---|---|---|
| 1. Tiered execution model, in plain words | Tier 0 delivered. The TypeScript type-check runs with the platform's compiler: it reads files only, is confined to the folder, uses no plugins or automatic types, and counts missing packages separately. The Java parse check already exists. Tiers 1–2 are specified (ADR 0017) and not built | `engines/eslint-runner/typecheck.mjs`, `crp_analysis/typecheck.py` |
| 2. Results mapped to file and line, bound to hashes; unavailable tiers say why | Type errors carry file, line, column, code and message. Results are cached by checker identity and every input's content hash. They are reported as new or fixed against the upload. The states unavailable, skipped, timed out and failed each come with a plain reason | `crp_worker/change_set.py` (`_types`), check `result.types` |
| 3. Integration: P05 ladder, P08 compile on demand, fix PR CI results | P08 workspace checks type-check on every check. The P05 ladder and CI results from pull requests are not yet built (P09-F1, P09-F2) | UI "Check my changes" card |
| 4. Operations: pinned images, proxy, quotas, cost ceilings | Tier 0 uses the platform's pinned, lockfile-installed compiler, bounded by time (300 s), output and heap. Images, proxy, quotas and costs belong to Tiers 1–2 (BLOCKED) | settings `CRP_TYPECHECK_*` |

## Mandatory tests (P09 prompt)

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Code that must not run (Tier 0) | The upload's `tsconfig.json` names a compiler plugin, extends a file outside the upload and sets an escaping `outDir`. A `.ts` file imports an `evil.js` that writes a mark if executed, and `node_modules/evil-plugin` holds the same code | PASS. No mark file and nothing written outside. The notes say the plugins were not loaded and only files inside the upload were read; the upload's own `strict` still applies | `test_typecheck.py::test_project_code_and_outside_files_are_never_used` |
| Code that must not run (Tier 1: annotation processor, npm postinstall, Maven extension, Gradle script) | — | **BLOCKED** (no Tier 1; K-P09-01) | — |
| Containment in Tier 2 | — | **BLOCKED** (no Tier 2; K-P09-01) | — |
| Correct results: missing dependencies honest, errors mapped | `strict` project with a wrong return type, a wrong assignment across files, an import of an uninstalled package and JSX without types | PASS. TS2322 at `price.ts:2` and `use.ts:3`. The missing package and the missing JSX types are counted (≥ 2), not reported as errors | `test_type_errors_are_mapped_and_missing_packages_counted` |
| Binding: results bound to hashes; stale content invalidates | A workspace check of the seeded fixture: the edit fixes an always-false NaN comparison and adds a wrong assignment. A second check of the same content follows | PASS. `new` holds TS2322 in `web/src/cart.ts` and `fixed` ≥ 1. The second check returns the identical result from the content-hash cache. New content gives a new key | `test_p08_workspace.py` (Temporal and lite), `test_inputs_and_cache_keys` |
| Comparison does not count moved lines | Same error on another line; new errors; checker failure on one side | PASS | `test_compare_counts_new_and_fixed_errors_but_not_moved_lines` |
| Customer CI | — | Not built (P09-F2) | — |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Types for Linux | `uv run mypy --platform linux` | no issues in 176 source files |
| Tests | `make test` | exit 0: 503 pytest (P09 added 5 type-check tests; the P08 workspace check tests assert the type result on Temporal and lite) + 29 vitest |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 19/19 in 4.1 minutes. The P08 journey asserts the type-check line ("1 type error fixed") |
| Screens | `p08-workspace.png` | "No new type errors · 1 type error fixed · TypeScript type-check" and "2 imports of packages that are not installed could not be checked" |

## Owner decision needed (Tiers 1–2)

- Managed microVM sandboxes, or a self-hosted node with Firecracker or gVisor.
- A package proxy with an integrity-checked cache.
- A monthly cost ceiling.

The market study (research/MARKET_ANALYSIS_2026.md §A) lists the options. Until then, Java
compilation, builds and tests stay "not run" everywhere.

## Limitations

- Packages are not installed in Tier 0, so code that uses them is type-checked less strictly
  (imports become `any`). The number of such imports is shown.
- Only the root `tsconfig.json` is used. Projects with several TypeScript projects (references,
  nested configs) are checked with the root options for all files.
- The P05 single-fix ladder does not run the type-check yet (P09-F1).
