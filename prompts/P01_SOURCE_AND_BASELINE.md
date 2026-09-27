# Execute Phase 1 — Source upload, folder capture and real baseline

Implement the first usable product, not an upload-screen prototype. Read shared instructions, P00 evidence, SOURCE_INTAKE, DATA_MODEL, API_CONTRACTS, ANALYSIS_PIPELINE, UI_SPEC and relevant security/tests. Repair mandatory P00 prerequisites before depending on them.

## Deliver in vertical slices

1. Authenticated project/source creation and ZIP intake with bounded streaming, idempotent finalization, safe extraction, checksums, frozen manifests, explicit rejection/cancellation and temp cleanup.
2. User-invoked local-folder CLI: dry-run inventory, explicit root, scoped project authentication, stable capture, unchanged original tree, canonical manifest and upload through the same API. Explain that this mode transmits source. Do not add a background daemon or arbitrary server path reader.
3. Browser folder selection is optional after the mandatory modes work. Registered server mounts are optional and admin-controlled through opaque IDs. Both must pass the same safety rules.
4. Inventory languages/build/framework indicators and version confidence. Account for accepted, excluded, unreadable and unsupported entries. Add basic Java and JS/TS structural extraction with parser errors; do not claim full semantic resolution.
5. Build real pinned PMD and ESLint adapters using trusted configuration, bounded execution and normalized evidence. Include seeded and clean fixtures; choose defects that the actual configured rules detect. Capture coverage and raw reports, not fake JSON.
6. Persist jobs/engine outcomes and stream progress. Add cancel/retry handling and distinguish failed/partial from clean.
7. Build source upload/scope/progress, repository overview, coverage, issue list and exact-source detail screens using real API results. Static rule explanations/recommendations are available without AI.

## Mandatory acceptance

Both ZIP and local-folder intake of equivalent content yield equivalent logical manifests/findings. Real scanner results survive service restart. The original directory's contents remain unchanged. Users can navigate from a finding to the exact source span. A failed engine leaves completed findings visible and reports incomplete coverage. No AI key or GitHub account is required.

Test traversal, symlinks, normalized path collisions, oversized/aborted input, resource quotas, duplicate finalize, unauthorized IDs, local file mutation, exclusions, empty/clean source, unsupported syntax, scanner crash/timeout/cancel, source escaping and user-visible errors. Uploaded instruction files must not change developer/product policy.

## Completion

Update installed versions, working runner commands, limits, capability matrix and docs. Produce P01_REPORT.md with real E2E/engine evidence. Optional intake modes may remain clearly unimplemented; mandatory modes/engines may not. Do not label the product as understanding every business rule or detecting all possible defects.

