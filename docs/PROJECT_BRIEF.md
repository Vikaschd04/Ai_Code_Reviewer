# Product brief

Status: specification, not implemented. Working project name: code-review-platform; branding and commercial clearance are outside this kit.

## Users and outcomes

Developers investigate and repair code; architects inspect dependencies and impact; engineering/security leads track actionable risk, coverage and progress. Initial customers have existing Java/JS applications and enterprise SAP Commerce/Salesforce customizations.

A successful user can supply source without Git integration, see its inventory and mappings, understand evidence-backed issues, obtain suitable solutions and validate a proposed change. Whole-repository accounting is mandatory; universal defect detection is not a promise.

## Modes

- Static baseline: deterministic analysis, no model account required.
- Structural mapping: syntax and available semantic/configuration evidence.
- AI investigation: explicitly configured provider and bounded source disclosure.
- Build/runtime verification: optional authorized environment and correct dependencies.
- Remediation: copied snapshot/worktree, patch and verification evidence.

Phase 1 delivers ZIP and local-folder capture, real baseline analysis and a usable dashboard. Later phases deepen graph semantics, AI, frameworks and repairs. A non-Git snapshot can pass through the full pipeline.

## Non-functional requirements

Source confidentiality; workspace/project authorization; resumable bounded execution; accessible UI; reproducible snapshot/rule identity; explicit incomplete states; cost visibility; exact-version tooling; compatible migration path; auditable fixes and deletions.

## Acceptance narrative

A user uploads a synthetic Java/JS application. The application records every file, identifies technologies, detects seeded defects using real engines, displays original source locations and explains limitations. The local-folder runner produces equivalent logical results from the same content. Later phases add dependency traversal, contextual review and verified patch download without requiring GitHub.

## Scope boundaries

No production database connection, arbitrary URL fetch, unrestricted source execution, automatic permission rewrites, automatic merging or compliance certification. Large-codebase support is qualified through measured tiers; do not label a small-fixture demo production-ready.

