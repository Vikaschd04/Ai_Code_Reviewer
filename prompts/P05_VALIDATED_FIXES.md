# Execute Phase 5 — Patch workbench and independent validation

Read AI_ORCHESTRATION, DATA_MODEL, API_CONTRACTS, TEST_STRATEGY and relevant framework validation contracts. Preserve ZIP/local-folder workflows: users must be able to obtain fixes without Git hosting.

## Deliver

1. A fix proposal bound to original snapshot/finding/rule context and an explicit file/line/time/attempt/spend budget.
2. Deterministic fixes/approved transformation recipes first; bounded AI patches for contextual cases. Verify exact recipe/module rights before use.
3. A separate copied snapshot/worktree for patch application. Original uploaded bytes and selected local directories stay immutable. No absolute-path/out-of-scope writes.
4. Validation ladder: patch integrity, parse/static rerun, original detector, relevant tests, compilation and platform checks where applicable. Bind results to exact patch and resulting content hashes.
5. UI to compare diff/options, edit/reject proposal, run approved validation and download a patch/change summary. Source-only/unverified labels are explicit when checks cannot run.
6. Safe cancellation/retry and bounded repair attempts. Exhaustion becomes an honest outcome, not a fabricated success.

## Mandatory tests

Correct small repair, invalid syntax, out-of-scope path, traversal, stale base, patch conflict, regression, missing build dependency/org, malicious test/build, canceled validation and attempts to disable tests/rules. Meaningful regression tests should fail on the original defect and pass after repair where possible. Run existing affected tests too.

Verify the original local directory is unchanged and the exported patch applies to its exact matching snapshot. Non-Git identity is content-based. A fresh capture with changed files requires revalidation; never claim applicability to an arbitrary later source tree.

## Completion

No auto-merge, direct production edits or permission/schema/data migration without review. Export patch plus validation evidence and known risks. Produce P05_REPORT.md, update user/install docs and state. PR creation remains Phase 6. Do not call a suggestion verified merely because the model or newly generated tests agree.

