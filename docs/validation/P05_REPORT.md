# Phase P05 validation report — validated fixes

- Date: 1 October 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Status: **IN_PROGRESS.** The deterministic-fix slice passed every mandatory test that it can
  exercise. Two deliverables remain:
  - The tests/compilation/platform rungs of the ladder are **not run**. There is no isolated
    runner (K-P05-01).
  - Bounded AI patches are **not implemented**. They need the live P03 provider (K-P05-02,
    K-P03-01).

  Nothing in this phase is a build, test or deployment claim.
- Design: [ADR 0014](../adr/0014_VALIDATED_FIXES.md).
- Environment:
  - macOS 26.5.2 arm64, Python 3.14.3, Node 22.22.0, Git 2.50.1, PostgreSQL 18.6, Temporal
    CLI 1.9.1.
  - Engines: PMD 7.27.0 (Java and Apex), ESLint 10.11.0 + typescript-eslint 8.70.1, Opengrep
    1.30.0, Trivy 0.69.3, in-process `frameworks`.
- Dataset: synthetic fixtures only (`seeded-mixed`, `salesforce-mixed`). The worker tests add
  hostile build and test files to the upload at test time.

## What was delivered

| Deliverable (P05 prompt) | Delivered | Where |
|---|---|---|
| 1. Proposal bound to snapshot/finding/rule with file, line, time, attempt and spend budgets | Bound to upload, finding, file base hash, result hash and patch hash. Budgets:<br>• file: the finding's file only<br>• line: at most 60 changed lines<br>• attempt: 5 validations, one at a time<br>• time: engine wall-time caps, 30-minute activity, 1-hour workflow<br>• spend: none, since recipes make no paid calls | `fix_proposals`, `policy.py`, `fix_validation.py` |
| 2. Deterministic recipes first; bounded AI patches for contextual cases; recipe rights | Three recipe families:<br>• ESLint 10.11.0's own safe fixes (MIT) for `prefer-const`, `no-var` and `eqeqeq`<br>• own code for Java string-literal `equals`<br>• own code for the Salesforce retired `apiVersion` rule<br>**AI patches: not implemented** (needs the P03 provider) | `crp_analysis/fixes/recipes.py`, `engines/eslint-runner/run.mjs` |
| 3. Separate copy for patch application; original immutable; no absolute or out-of-scope writes | Edits applied in memory. Checks run on a read-only copy of only the changed file (`O_EXCL`/`O_NOFOLLOW`), which is removed afterwards. Safe relative paths and scope are enforced | `workspace.written`, `patching.safe_path`, `policy.check` |
| 4. Validation ladder bound to patch/result hashes | Five steps:<br>• integrity (stale base, conflict, hashes, policy)<br>• syntax (Tree-sitter, secure XML, JSON; Apex via PMD)<br>• original detector and regression (all trusted engines on the original and changed copy)<br>• tests: **not run**<br>• build/platform: **not run**, with a platform-specific reason<br>Each validation stores the hashes it checked | `crp_analysis/fixes/validation.py` |
| 5. UI to compare, edit, reject, validate and download; explicit source-only labels | Fix card on findings, fix page (diff, what to watch, editor, checks, labels, patch and summary downloads, technical details) and a project Fixes tab | `apps/web/src/pages/FixPage.tsx`, `components/Fixes.tsx` |
| 6. Safe cancellation, retry and bounded attempts; honest exhaustion | Cancellable before and during checks. The idempotent activity retries once. Budget exhaustion returns 409 `validation_budget_exhausted`. A workflow outage fails the validation and refunds the slot | `routes/fixes.py`, `fix_validation.py`, `inline.py` |

## Mandatory tests (P05 prompt)

| Test | Procedure | Outcome | Evidence |
|---|---|---|---|
| Correct small repair | PMD `UseEqualsToCompareStrings` on `InvoiceService.java`; real PMD and Opengrep on both copies | PASS: integrity, syntax and checks passed; tests and build `not_run` | `test_fixes.py::test_ladder_passes_a_correct_small_repair`; Temporal and lite: `test_p05_fixes.py` |
| Invalid syntax | Replacement drops a parenthesis | PASS: syntax step `failed` ("syntax errors") | `test_ladder_rejects_broken_ineffective_and_regressing_fixes[syntax]` |
| Out-of-scope path | Edit to a file outside `allowed_paths` | PASS: `out_of_scope` refused | `test_policy_refuses_changes_that_hide_problems` |
| Traversal / absolute path | `../etc/passwd`, `/abs/A.java`; workspace writes outside root | PASS: `unsafe_path`; `WorkspaceError` | same; `workspace.written` |
| Stale base | Base hash differs from the upload's file | PASS: integrity `failed` ("stale base"); later steps not run | `test_ladder_detects_stale_base_and_tampered_patches` |
| Tampered patch | Patch hash mismatch | PASS: integrity `failed` | same |
| Patch conflict | Original lines changed or duplicated; rebase to an upload where the lines differ | PASS: `PatchError` conflict / ambiguous; API 409 `patch_conflict` | `test_edits_apply_exactly_or_conflict`, `test_relocate_follows_moved_lines_only_when_unambiguous`, `test_moving_a_fix_to_another_upload` |
| Ineffective fix | An edit that keeps the problem | PASS: checks `failed` ("still reported"); proposal VALIDATION_FAILED | `test_ladder_rejects…[still reported]`; Temporal flow after a reviewer edit |
| Regression | The fix adds `System.out.println` (new PMD finding) | PASS: checks `failed` ("new findings") | `test_ladder_rejects…[new findings]` |
| Missing build dependency / org | Java fix; Salesforce `apiVersion` fix on `salesforce-mixed` | PASS (honest outcome): build `not_run` with "build toolchain and dependencies" (Java) or "authorized Salesforce org" (Salesforce); tests `not_run` | `test_fix_is_validated_by_the_temporal_worker`, `test_salesforce_fix_leaves_org_checks_not_run` |
| Malicious test / build | The upload carries:<br>• `package.json` lifecycle and test scripts<br>• a project `eslint.config.mjs`<br>• Makefile, gradlew, build.gradle<br>• JS and Java tests<br>Each creates a mark file if run; they are confirmed to be in the snapshot | PASS: no mark after scanning and fix validation (Temporal and lite); the ladder's work area holds only the changed file | `test_p05_fixes.py`, `test_ladder_copies_only_the_changed_file` |
| Canceled validation | Ladder with a cancelled token; API cancel endpoint; on the Temporal worker a validation is held inside the PMD check, stopped twice (idempotent), then run again | PASS: result `canceled`, never passed; validation CANCELED, proposal back to PROPOSED with the slot used; the retry passes (VALIDATED, 2 of 5 used) | `test_ladder_cancellation`, `test_validation_requests_budget_and_service_outage`, `test_p05_fixes.py::test_running_validation_stops_and_can_run_again` |
| Attempts to disable tests or rules | The edit or recipe adds any of:<br>• NOPMD, `eslint-disable`, inline `/* eslint … */`<br>• `@SuppressWarnings`, `nosemgrep`, `trivy:ignore`<br>• fewer asserts or `@isTest`<br>• a `package.json` change | PASS: `suppression_added`, `test_weakened`, `config_change` (API 422 `fix_not_allowed`; UI shows the reason) | `test_policy_refuses_changes_that_hide_problems`, `test_prepare_edit_reject_and_export`, `p05-fixes.spec.ts` |
| Original directory unchanged | Digest of the captured folder before and after scan and validation | PASS | `test_p05_fixes.py::_fix_and_validate` |
| Patch applies to its exact snapshot only | `git apply --check -p1` on a copy of the upload, then on a changed copy; result file hash compared | PASS: applies and matches `result_sha256`; refused on the changed copy | `test_prepare_edit_reject_and_export`, `test_p05_fixes.py::_check_patch` |
| Fresh capture requires revalidation | Rebase creates a new PROPOSED proposal (`rebased_from`) with no validation | PASS | `test_moving_a_fix_to_another_upload` |
| Existing affected tests | Engine, API, worker, capability and UI suites | PASS | `make test` below |

Also covered:
- Recipe positives and negatives: Java single literal only; ESLint UTF-16 offsets verified against the original slice; the Salesforce recipe needs a supported `sourceApiVersion`.
- Idempotent proposal creation.
- Edits reset validation, and an earlier validation is flagged as not current.
- A workflow outage fails the validation and refunds the budget slot.
- Other workspaces get 404.
- The diff view renders code as inert text.

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint/format/types/contracts | `make check` | exit 0 |
| Tests | `make test` | exit 0: 420 pytest (P05: 43 — fixes 33, API 5, worker 4, ESLint autofix capture 1) and 20 vitest (diff view 2, router 1 added) |
| Types for Linux | `uv run mypy --platform linux` | no issues in 154 source files |
| Hosted entrypoints | `uv run pytest tools/devtools/tests/test_hosted_smoke.py` (runs `deploy/smoke_test.py`, which now prepares, checks and downloads a fix) | 5 passed (standard and lite profiles on macOS); CI runs the same script against the Linux image, including the lite profile at 512 MB / 0.1 CPU |
| Browser E2E | `make test-e2e` | exit 0: 17/17 (foundation 7, P01 2, P02 2, P03 1, P04 2, P05 1, UI tour 2) |
| Screens | `p05-fix-passed.png`, `p05-fix-dark.png`, `p05-fix-mobile.png` (390 px, no horizontal scroll), `p05-fix-card.png` | Reviewed: file name first, plain step labels, "Not run" reasons, collapsed technical details |

Issue found during this checkpoint:
- **Problem:** the suppression policy matched `nosem` but not `nosemgrep` (Opengrep's usual
  marker), inline ESLint rule settings or `trivy:ignore`.
- **Fix:** the policy now matches all three, and test cases were added.

## Limitations

- **Source-level only (K-P05-01).** A passing fix may still fail to compile against the rest of
  the project, and the regression check covers only the changed file (K-P05-05). Reviewers build
  and test the patch in their own environment; the page and the patch header say so.
- **Few recipes (K-P05-03).** There are three recipe families. ESLint fixes appear only for scans
  made after this release. Each fix covers one file.
- **Issue status unchanged (K-P05-04).** Fixes do not change issue status; `FIX_PROPOSED` is
  reserved for P06 pull requests.
- **No AI patches (K-P05-02).**

## Next tasks

1. Once the owner configures the P03 provider (K-P03-01): run the live AI evaluation, then add
   bounded AI patches that go through the same policy and ladder, labelled as AI.
2. Build an isolated runner for the tests and build rungs: container or VM, no network or
   secrets, CPU/memory/time caps, and a per-language allowlist of test commands. Then define the
   SAP build and Salesforce check-only profiles in a customer-authorized environment.
3. Add recipes rule by rule, each with positive and negative fixtures.
