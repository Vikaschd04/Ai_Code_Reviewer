# ADR 0014 — Validated fixes: deterministic recipes and a source-level validation ladder

Status: accepted. Date: 1 October 2026. Owner: repository owner (Phase 5 scope in
prompts/P05_VALIDATED_FIXES.md); implemented by the development agent.

Context and constraints:

- Reviewers want a concrete repair for a finding, not just a description. The repair must work
  without Git hosting (ZIP upload and local-folder capture) and must never modify the upload.
- Uploads are untrusted. Running a project's tests or build executes uploaded code (build
  scripts, test hooks, package lifecycle scripts). This deployment has no isolated runner, and
  SAP Commerce builds need a licensed distribution while Salesforce deployment and Apex tests need
  an authorized org. Neither is available.
- The AI provider for P03 is not configured yet (K-P03-01). Model agreement is not validation.
- The free hosting profile has 512 MB of memory and no extra runtimes.

Decision:

1. **Proposals are bound to exact content.** A `fix_proposals` row belongs to one finding and
   one upload (snapshot) and records the file's base SHA-256, the resulting file's SHA-256 and
   the patch's SHA-256. The patch scope (`allowed_paths`) is the finding's file. A proposal is a
   list of line edits, each carrying the exact original lines, so applying it to anything else
   is a conflict rather than a silent misapplication. Patches are Git-compatible unified diffs
   (including "\ No newline at end of file") that `git apply -p1` accepts on a copy of exactly
   that upload.
2. **Deterministic recipes first.** v1 has three approved transformations:
   - `eslint:<rule>` for `prefer-const`, `no-var` and `eqeqeq`, using ESLint 10.11.0's own
     safe fix (MIT). The runner captures the fix at scan time; the recipe checks that the
     original slice (UTF-16 offsets) still matches before using it.
   - `java:string-literal-equals` for PMD `UseEqualsToCompareStrings`, only when one side is a
     string literal (`"X".equals(y)` is null-safe). This is our own code.
   - `salesforce:api-version` for the retired-API finding, moving the metadata `apiVersion` to
     the project's `sourceApiVersion` only when that version is supported. This is our own code.

   Every recipe states what could behave differently ("What to watch"). Bounded AI patches
   (`kind = ai`) are part of the schema but not implemented; they need the P03 provider.
3. **A change policy guards every patch and every edit.** It refuses the following:
   - unsafe paths (absolute paths, traversal) and paths outside the scope;
   - configuration files;
   - added suppression markers (NOPMD, eslint-disable, inline `/* eslint … */` rule settings,
     @SuppressWarnings, nosem/nosemgrep, trivy:ignore, NOSONAR, @ts-ignore/@ts-nocheck/
     @ts-expect-error, codeanalyzer-disable, istanbul ignore, noqa);
   - weakened tests (fewer test annotations, test methods or assertions than before);
   - more than 60 changed lines.

   Reviewers may edit the replacement lines. An edit goes through the same policy and resets
   validation.
4. **The validation ladder runs on copies and never executes project code.**
   1. *Integrity:* the upload's file still has the base hash (otherwise "stale base"), the edits
      apply, and the result and patch hashes match. The policy is re-checked.
   2. *Syntax:* the changed file still parses. Java, JavaScript and TypeScript use Tree-sitter,
      metadata uses secure XML, and JSON is parsed directly. Apex and other files rely on the
      analyzers' own parsers.
   3. *Checks:* the trusted engines run on the original and on the changed copy of the single
      file. The original finding must be reproduced on the original copy and gone from the
      changed lines, and no rule may report more findings than before.
   4. *Tests* and 5. *Build or deployment:* always `not_run`, with a plain reason for the
      platform (isolated runner; SAP build profile; Salesforce org profile).

   Only the changed file is written into the work area, read-only; it is removed afterwards.
   Results are stored per validation with the patch and result hashes they apply to. A
   validation counts as "current" only while those hashes still match the proposal.
5. **Bounded, safe execution.**
   - Each proposal has a budget of 5 validations. A failure to start the workflow refunds its
     slot.
   - Only one validation per proposal runs at a time.
   - Validation runs as a Temporal workflow, or in-process on the lite profile. The activity is
     idempotent: it retries once, heartbeats, and has a 30-minute limit inside a 1-hour workflow.
   - Validation is cancellable. The request is checked before the checks start and every
     second while they run; running engines are stopped.
   - Exhausting the budget is reported, never turned into success.
6. **Moving a fix to another upload** relocates each edit only where its original lines appear
   exactly once and unchanged. Otherwise the result is a 409 `patch_conflict`. The moved fix is a
   new proposal that must be validated again; applicability to arbitrary later code is never
   claimed.
7. **Issue status is unchanged.** `FIX_PROPOSED` locks triage and is set only by the fix
   workflow (P02). A downloaded patch is not applied anywhere refactorX can observe. The status
   is therefore reserved for Phase 6, when a pull request carries the fix. Fix history is shown
   on the finding instead.

Alternatives considered:

- *Run the project's tests and build in a local container.* Rejected for now. There is no
  isolated runner with a network and credential boundary, and it does not fit the 512 MB
  profile. SAP and Salesforce also need licensed or authorized environments. These steps are
  recorded as "not run", never as passed.
- *Use each engine's auto-fix for every rule.* Rejected. Only fixes that are provably
  semantics-preserving or clearly intended are enabled. ESLint's `--fix` output for other rules
  is not reviewed.
- *Git-based worktrees.* Not required: identity is content-based and works without Git. Pull
  requests are Phase 6.
- *AI-generated patches now.* Deferred until the P03 provider is live. They would go through the
  same policy and ladder, labelled as AI.

Consequences and migration/reversal approach:

- Migration `0007` adds `fix_proposals` and `fix_validations` (both scoped to the snapshot, and
  deleted with the project or snapshot). Downgrade drops them.
- Scan results now store ESLint's safe fix (`details.autofix`) for the three rules. Earlier scans
  have none, so a new scan is needed before those fixes are offered.
- The `fix_workbench` capability is now `available`.

Evidence and source/version references:

- ESLint 10.11.0 rule fixers (`prefer-const`, `no-var`, `eqeqeq`); PMD 7.27.0
  `UseEqualsToCompareStrings`; Salesforce Help 000389618 (API 21.0–30.0 retirement); Git unified
  diff format (`git apply`). Report: docs/validation/P05_REPORT.md.

Affected contracts, phases and tests:

- API: `/v1/findings/{id}/fix-options`, `/v1/findings/{id}/fix-proposals`,
  `/v1/projects/{id}/fix-proposals`, `/v1/fix-proposals/{id}` (+ `/edits`, `/reject`,
  `/validations`, `/patch`, `/summary`, `/rebase`), `/v1/fix-validations/{id}/cancel`; export
  `crp-fix-export/v1`.
- Tests: `packages/analysis/tests/test_fixes.py`, `test_engines.py`,
  `services/api/tests/test_fixes_api.py`, `services/worker/tests/test_p05_fixes.py`,
  `apps/web/src/components/Fixes.test.tsx`, `apps/web/e2e/p05-fixes.spec.ts`.
