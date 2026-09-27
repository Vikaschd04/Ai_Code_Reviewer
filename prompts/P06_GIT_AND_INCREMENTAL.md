# Execute Phase 6 — GitHub, incremental scans and PR publication

Read current data/source/workflow and security contracts. Git is a new adapter; existing ZIP/folder sources and patch export must remain fully usable.

## Deliver

1. Least-privilege GitHub integration with installation/repository authorization, scoped short-lived credentials and revocation handling. Read access and publication capability are separate.
2. Signed webhooks, replay/idempotency handling and immutable checkout snapshots. Do not execute repository hooks/config plugins while cloning or inspecting.
3. Baseline and PR scans using the correct merge base and head commit. Handle shallow/missing history explicitly and process additions, deletions, renames and submodule/LFS policy.
4. Dependency/configuration-aware scope expansion and cache invalidation; retain scheduled/full reconciliation. A changed lockfile, permissions file or Spring config can affect unchanged source.
5. Checks/comments and optional human-authorized PR publication through a dedicated credential boundary, with duplicate-update behavior and exact-head freshness checks.
6. Show Git metadata alongside universal snapshot identity and maintain finding lineage without resolving not-rechecked issues.

## Mandatory checks

Invalid webhook signature, duplicate/out-of-order events, revoked access, forked PR trust boundary, concurrent pushes, stale patch, changed merge base, deleted/renamed files, config-only change, failed partial scan and unavailable provider. Rebase/regenerate and revalidate when the patch base changes. Do not leak source from unauthorized repositories through graph/context caches.

Use a controlled authorized test repository for live integration. If unavailable, contract tests can progress but live connector verification remains blocked. Do not create issues/comments/PRs in arbitrary user repositories merely to test the product.

## Completion

Produce P06_REPORT.md, actual installation/permission/webhook docs and revocation/runbook guidance. Verify non-Git regression flows. Publication is optional per project and never implies auto-merge or deployment authorization.

