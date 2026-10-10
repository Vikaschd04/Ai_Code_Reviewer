# Canonical data model

## Implemented in P00 (migration `0001`)

Tables: `workspaces`, `users`, `memberships`, `projects`, `sources`, `snapshots`, `scans` (`packages/core/src/crp_core/db/models.py`, migration `crp_core/db/migrations/versions/0001_initial_boundary.py`). Enforced in the database:

- Scope-compatible composite foreign keys: `sources(workspace_id, project_id) → projects`, `snapshots(workspace_id, project_id, source_id) → sources`, `scans(workspace_id, project_id, snapshot_id) → snapshots`. A scan cannot reference another project's snapshot (tested).
- Enum CHECK constraints generated from `crp_core.domain.states` (roles, project origin, source mode, capture status, scan state); slug format checks.
- Snapshot identity: `manifest_sha256` must be 64 lowercase hex; `FROZEN` requires manifest hash and `frozen_at`; optional `git_commit` must be a full 40/64-hex object ID (short or invented IDs are rejected).
- Idempotency: `UNIQUE(project_id, idempotency_key)` on scans; optimistic `version` columns on projects and scans.
- Scan state transitions and terminality are defined in code (`SCAN_TRANSITIONS`); the DB constrains the value set.

Only workspaces, users, memberships and projects are written by P00 code. Sources, snapshots and scans are schema only until P01 intake/scans exist. `alembic check` in the test suite guards model/migration drift.

Retention (P00): deleting a workspace cascades to memberships and projects; deleting a project cascades to sources → snapshots → scans. Users are retained (`disabled_at`) and referenced with `ON DELETE SET NULL`. Artifact-store objects are not yet linked to rows (only transient diagnostic probes, deleted immediately). Formal retention periods are defined when intake stores customer data (P01).

## Added in P01 (migration `0002`)

`intakes` (scoped to source/project/workspace; state CHECK; `ready_requires_snapshot`), `file_entries` (per snapshot; unique path; disposition ANALYZABLE/BINARY/OVERSIZED/EXCLUDED with reason; `analyzable_has_blob`; parse status), `code_symbols` (syntax symbols with spans; extractor), `engine_runs` (unique per scan+engine; coverage counts consistency CHECK; raw artifact SHA-256), `file_coverage` (unique per run+file; outcome CHECK), `findings` (unique per scan+fingerprint; severity/category/status CHECKs; span CHECK), `scan_events` (monotonic IDs for SSE). `snapshots` gained intake/manifest/policy/count/inventory columns; `scans` gained workflow/cancel/error/summary columns.

Identity: manifest digest = SHA-256 of canonical JSON `{version:1, files:[[path,size,sha256]…]}` over non-excluded entries (EXCLUDED entries are listed but carry no content identity). Blobs are stored content-addressed at `blobs/<aa>/<sha256>` in the artifact store; manifests at `snapshots/<id>/manifest.json`; raw engine reports at `scans/<id>/raw/<engine>.json`. Raw archives and client manifests are deleted after validation.

Retention (P01): deleting a project cascades to sources, intakes, snapshots, file entries, scans, engine runs, coverage, findings and events. Blob garbage collection for unreferenced content is not implemented yet (K-P01-03).

## Added in P02 (migration `0003`)

- `findings`: `anchor_kind` (`source_span`/`file`/`dependency`) with a CHECK that a source span has lines and lineless rows are not source spans; `correlation_key` (backfilled from the fingerprint for existing rows; indexed per scan), `rule_family`, `details` (e.g. package, installed/fixed version, advisory, PURL), `guidance` (engine-supplied), `issue_id`; category adds `dependencies`. `findings.status` is the status at observation time; the durable status lives on the issue.
- `issues` (project-scoped by composite FK; unique `(project_id, fingerprint)`): engine/rule/path/title/severity/category, `status`, `recheck_state` + reason, owner, exception reason/expiry, first/last seen and last evaluated scan, last-seen engine version and rule-set hash, optimistic `version`. CHECKs: ACCEPTED_RISK and FALSE_POSITIVE need a non-blank reason; ACCEPTED_RISK needs an expiry.
- `issue_events`: append-only audit (`actor_kind` user/system, `kind`, JSON `changes`, reason, scan).
- `graph_builds` (per snapshot; state SUCCEEDED/PARTIAL/FAILED; one `is_current` per snapshot via partial unique index; extractor, counts, diagnostics), `graph_nodes` (kind module/file/package/type/function/external, stable `key` unique per build, label, module key, file anchor + span, attributes) and `graph_edges` (relation, classification declared/resolved/inferred/unresolved with `resolved ⇒ target` CHECK, `target_ref`, reason, evidence file/lines/text, extractor). Composite FKs force node/edge anchors into the build's snapshot. Superseded builds keep their row; their nodes/edges are deleted.
- `engine_cache` (project-scoped; unique `(project_id, cache_key)`; engine, version, rule-set hash, file hash, JSON payload of normalized findings or graph facts, hit count, last use). Deleted with the project; no eviction yet (K-P02-01).
- `engine_runs` add `enabled_rules` (null = open-ended), `config_fingerprint`, `cache_hits`, `cache_misses`; `file_coverage` adds `cached`; `scans` add `cache_mode` (`use`/`refresh`) and `lifecycle_applied`.

Retention (P02): project deletion cascades to issues, events, graph data and cache; snapshot deletion cascades to its graph builds; scan deletion nulls issue scan references.

## Added in P05 (migration `0007`, ADR 0014)

- `fix_proposals`: scoped to its upload by the composite FK `(workspace_id, project_id, snapshot_id) → snapshots` and to its finding (both `ON DELETE CASCADE`), plus `scan_id`. Columns: `kind` (`recipe`/`ai`; only `recipe` is produced), `recipe_id`, title, explanation, `behaviour_note`, `state` (PROPOSED/VALIDATING/VALIDATED/VALIDATION_FAILED/REJECTED), `path`, `target_line`, `rebased_from`, `allowed_paths` (JSON; v1 = the finding's file), `base_sha256`, `result_sha256`, `patch_sha256` (64-hex CHECKs), `patch` (unified diff), `edits` (JSON line edits with the exact original lines), `changed_lines`, `edited`, validation budget `validations_used` ≤ `max_validations` (default 5, CHECK), `rejected_reason`, `created_by` (`SET NULL`), optimistic `version`.
- `fix_validations`: per ladder run (`proposal_id` cascade), `state` (QUEUED/RUNNING/PASSED/FAILED/CANCELED), the `patch_sha256` and `result_sha256` it applies to, `steps` (JSON), `summary`, `workflow_id`, error code/message, `cancel_requested_at`, timestamps, `requested_by`.
- `findings.details.autofix` (ESLint safe fixes captured at scan time: UTF-16 `start`/`end`, replacement `text`, the `original` slice) — no schema change.

Issue status is not changed by P05; `FIX_PROPOSED` stays reserved for Phase 6 pull requests (ADR 0014). Retention: deleting a project, upload or finding deletes its proposals and validations; patches contain only lines of the stored upload.

## Added in P06 (migration `0008`, ADR 0015)

- `git_installations` (workspace FK cascade; unique `(provider, external_id)` so an installation belongs to one workspace): account, type, repository selection, permissions, `suspended_at`, `revoked_at`, `linked_by`, `synced_at`.
- `git_repositories` (composite FK to the installation, cascade; unique `(installation_id, external_id)`): full name, default branch, private, archived, `removed_at`.
- `git_connections` (one per project and one per repository; composite FKs to project, repository and the `github` source): `review_pushes`, `review_pull_requests`, `review_forks` (false), `publish_checks` (false), `publish_pull_requests` (false), `check_fail_threshold` (CHECK), `reconcile_days` (1–90), `last_full_review_at` (written without bumping the policy `version`), creator/editor, optimistic `version`. `git_connection_events`: append-only audit (connected, policy_changed, disconnected).
- `git_link_requests`: SHA-256 of the one-time linking state, user, workspace, expiry, `used_at`, result.
- `git_deliveries`: unique `(provider, delivery_id)`; event, action, installation/repository ids, outcome, detail, review ids — no payloads.
- `code_reviews` (project FK cascade; connection, snapshots and scans `SET NULL`; unique `(project_id, idempotency_key)`): kind (branch/pull_request), trigger (push/pull_request/manual/reconcile), state (CHECK), ref, head/base/merge-base commits (hex CHECKs), pull request number/title/author/url/fork, `full`, head/base snapshot and scan, `changes`, `result`, publish state/error, check run and comment ids, `superseded_by`, delivery id, cancel/timestamps.
- `fix_pull_requests` (proposal FK cascade; unique per proposal): repository, branch, base ref/commit, commit, number, url, creator.
- `snapshots` add `git_provider`, `git_repository`, `git_tree_sha` (CHECK), `git_capture` (verification report) and an index on `(project_id, git_commit)`; `sources.mode` / `intakes.mode` accept `github`.
- `scans.mode` is now `baseline`, `pull_request` or `reference`; only `baseline` scans update issues.

Retention: deleting a project removes its connection, reviews, deliveries' review links (ids only), fix pull request records and captured snapshots; unlinking an installation removes its repositories and connections but keeps reviews. No GitHub tokens are stored.

## Added in P08 (migration `0009`, ADR 0016)

- `change_sets` (composite FK `(workspace_id, project_id, base_snapshot_id) → snapshots`, cascade; `base_scan_id` `SET NULL`): title, `content_sha256` (64-hex CHECK; digest of the changed files), `archived_at`, `created_by`, `updated_at`, optimistic `version`.
- `change_set_files` (cascade; unique `(change_set_id, path)`): `action` (modify/add/delete, CHECK), `base_sha256` (none for added files), `sha256` (none for deleted files), size, lines, language, policy `flags` and provenance `sources` (JSON), `updated_by`, `updated_at`. Contents are content-addressed blobs in the artifact store.
- `change_set_events` (cascade): append-only history — `source` (manual/recipe/ai/revert/export, CHECK), path, action, `finding_ids`, `recipe_id`, summary, flags, revision hash, workspace digest after the event, actor.
- `change_set_checks` (cascade; snapshot and scans `SET NULL`): state (QUEUED/RUNNING/SUCCEEDED/PARTIAL/FAILED/CANCELED), the `content_sha256` and frozen `files` list it checks, derived snapshot, head and base scans, `result`, `workflow_id`, error, cancel and timestamps, requester.
- `snapshots` add `derived_from` and `change_set_id` (indexed) for copies made by checks; `scans.mode` accepts `change_set` (never changes issues).

- Migration `0010`: `ai_runs.kind` accepts `fix`; `ai_runs.change_set_id` (`SET NULL`, indexed, so AI usage accounting survives workspace deletion) and `target_sha256` (the exact file text the candidates were made for, 64-hex CHECK). Candidates, their edits, checks and `applied_at` are stored in `ai_runs.answer`.

- Migration `0011`: `change_set_pull_requests` (workspace cascade; unique `(change_set_id, content_sha256)`; repository, branch, base ref and commit, commit, number, url, creator).

- Migration `0012` (P10 slice 2, ADR 0019): `architecture_rule_versions` (composite FK `(workspace_id, project_id) → projects`, cascade; `created_by` `SET NULL`; unique `(project_id, version)`, `version >= 1`): canonical rules `document` (JSON), `sha256` (64-hex CHECK), `source` (`editor`/`yaml`, CHECK), `note`, `created_at`. Append-only; the newest version applies to the next review. Engine runs of `architecture` record the version id, number and per-rule hashes in `diagnostics`; their findings carry the rule's hash in `details.rule_sha256`, which the issue lifecycle stores as `issues.last_seen_ruleset_sha256`.

- Migration `0013` (P12 slice 1, ADR 0021): `nfr_profile_versions` (composite FK `(workspace_id, project_id) → projects`, cascade; `created_by` `SET NULL`; unique `(project_id, version)`, `version >= 1`): canonical profile `document` (targets, regulations, platforms, attested answers), `sha256` (64-hex CHECK), `note`, `created_at`. Append-only; the newest version applies. The readiness view is computed on request, not stored.

- P10 slice 3 (ADR 0020), no migration: findings of the `smells` engine carry `details.anchor_key` (the part); the issue lifecycle moves an issue to a new anchor file of the same part (event `moved`) instead of resolving it.

Retention: deleting a workspace deletes its files, events, checks and derived copies (with their scans and findings); deleting the project or the upload deletes the workspace. Revision blobs are shared content-addressed objects (no separate GC yet, K-P01-03).

## Target model

All domain records are workspace/project scoped. UUIDs are opaque identifiers, not authorization. Source identity works without Git.

| Entity | Required fields/relationships |
|---|---|
| Workspace/User/Membership | identity, role, revocation and audit timestamps |
| Project/ProjectGrant | owner, workspace, capability/policy references |
| Source | project, mode, display name, authorized registration metadata |
| Intake | source, state, quota policy, digest, errors and timestamps |
| Snapshot | source/project, immutable manifest hash/version, capture status, optional Git metadata |
| FileEntry | snapshot, safe relative path, blob hash, size/type, language, disposition/reason |
| Scan | snapshot, mode, policy version, idempotency key, state, budget and timestamps |
| EngineRun | scan, engine/image/rule versions, scoped inputs, state, counters, raw-artifact refs |
| FileCoverage | engine run/file, eligible/attempted/outcome, reason, parse/resolve/AI status |
| Symbol/Edge | snapshot, source span, stable local identity, relation, extractor/version, provenance/confidence |
| Finding/Occurrence | durable issue fingerprint; each scan observation, source/evidence, engine/rule, severity, confidence |
| Exception | finding/rule scope, reason, actor/approver, expiry and policy version |
| FixProposal | finding/snapshot, patch hash, allowed scope, status, validation profile |
| ValidationRun | proposal, copied-snapshot identity, toolchain, checks, exit results, artifacts, missing checks |
| KnowledgeSource/RulePack | official URL, applicability, content/version/license, review/expiry |
| UsageEvent/AuditEvent | operation identity, actor, units/cost where known, redacted event metadata |

## State conventions

Engine/scan outcomes include QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED, CANCELED, BLOCKED and BUDGET_EXHAUSTED where relevant. Define valid transitions and terminality in code. SUCCEEDED means the promised selected scope completed, not no bugs. Unavailable prerequisites use explicit reasons.

Finding lifecycle: OPEN, TRIAGED, ACCEPTED_RISK, FALSE_POSITIVE, FIX_PROPOSED, RESOLVED. Recheck state is separate: VERIFIED_PRESENT, VERIFIED_ABSENT, NOT_RECHECKED, UNKNOWN or RULE_OBSOLETE. Do not equate hidden/suppressed with resolved.

## Integrity

Foreign keys enforce scope-compatible relationships; unique keys make retries idempotent. Transaction boundaries cover result publication and terminal coverage. Use optimistic version checks for mutable state. Blob keys and signed downloads require authorization through the owning project.

Never use line numbers alone for fingerprints; include normalized rule family, symbol/context and evidence characteristics. Preserve original engine severity plus canonical mapping version. Keep graph/dataflow findings without false invented line ranges when evidence is an artifact or dependency.

Schema changes use migrations with upgrade tests. Define deletion/retention for every table and derived cache. Do not store provider secrets, full source or private reasoning in general-purpose event columns.

