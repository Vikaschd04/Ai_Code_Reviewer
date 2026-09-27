# Execute Phase 7 — Qualified production release

Read all launch-scope phase reports, SECURITY_MODEL, OPERATIONS, TEST_STRATEGY and DEFINITION_OF_DONE. Treat existing completion claims as assertions to verify against evidence.

## Deliver

1. Production identity integration, workspace/project roles, server-side authorization, audit events and revocation across jobs/caches/artifacts.
2. Qualified hostile execution isolation or dedicated customer-runner boundary, restricted egress/credentials, image/rule provenance and tested cleanup. A Docker container alone is not proof of tenant isolation.
3. Private artifact storage, explicit source/model retention and deletion, encryption configuration, backup/restore and incident procedures with named operators.
4. Fair scheduling, quotas, retry/backpressure, cancellation/checkpoint recovery and cost ceilings under realistic concurrent load.
5. Scale qualification on documented datasets/hardware/tool versions; include medium and large repositories with failures, not just ideal single-file scans. Publish measured service tiers and limitations rather than inherited aspirational SLOs.
6. Supported-version matrix, upgrade/rollback/migration tests, installation/deployment guides, user/admin docs, operational dashboards and support/security reporting process.
7. Final UI/accessibility/performance checks and held-out finding/fix evaluation for the promised launch domains.

## Mandatory release review

Verify isolation/access control, malicious intake/config, prompt injection, secret handling, stale graph/cache/fix state, engine outages, provider outages, deletion/revocation and disaster recovery. Review component/rule licensing and unresolved security issues for the exact shipped versions.

Set measurable acceptance criteria with owners based on pilot evidence. Do not assume the original planning duration or LOC goals establish readiness. Unavailable infrastructure/credentials/assessments remain explicit blockers to the affected release claim; never relabel them passed.

## Completion

Write P07_REPORT.md and a RELEASE_READINESS.md listing launch scope, evidence, measured limits, residual risks and blockers. Update runbooks and user documentation to the actual deployment. Preparing the release does not authorize public deployment, paid resource creation or production data changes. Report a concrete release candidate ready for review, or precise blockers.

