# API contracts

Status: design contract. The implemented subset is listed first; the generated OpenAPI document (`packages/contracts/openapi.json`, also served at `/v1/openapi.json`) is authoritative for implemented endpoints.

## Implemented in P00

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/health/live | none | `{status:"alive", service, version}`; no dependency detail |
| GET /v1/health/ready | credentials only (no DB lookup) | `ReadinessReport` with checks `database` (schema revision vs head), `workflow_service`, `workflow_worker` (fresh pollers), `artifact_store` (write/read/delete probe); each `ok\|failed\|unavailable` with `error_code`, `details`, `latency_ms`. 200 when all OK, otherwise 503 with the same body |
| GET /v1/capabilities | credentials | Capability registry (`available` / `planned` with phase and reason) that drives UI navigation |
| POST /v1/auth/session | allowed Origin + token in body | Sets HttpOnly SameSite=Strict session cookie; 401 `invalid_credentials`; 429 `too_many_attempts` after 10 failures/60 s |
| DELETE /v1/auth/session | none | Clears the cookie (204) |
| GET /v1/auth/me | principal | User, auth method, operator flag, workspace grants |
| POST /v1/projects | principal, role ≥ member | `{workspace_id, name, slug?, description?}` → 201; 404 `workspace_not_found` for non-members; 403 `insufficient_role`; 409 `project_slug_conflict` |
| GET /v1/projects | principal | Cursor pagination (`limit` 1–100, `cursor`), optional `workspace_id` (404 if not a member); 400 `invalid_cursor` |
| GET /v1/projects/{project_id} | principal | 404 `project_not_found` for other workspaces |
| POST /v1/diagnostics/workflow-runs | operator (admin/owner) | 202 `{workflow_id, status:"RUNNING"}`; 503 `workflow_unavailable` |
| GET /v1/diagnostics/workflow-runs/{workflow_id} | operator | Status and typed result; 404 for unknown or non-diagnostic IDs |

Cross-cutting: every response has `X-Request-ID`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Cookie-authenticated non-GET requests require an allowed `Origin` (403 `origin_rejected`). Non-loopback peers or `Host` headers get 403 (`non_loopback_client`, `host_not_allowed`). Validation errors are 400 `validation_failed` with locations/messages only (no submitted values). Data endpoints return 503 `database_unavailable` when PostgreSQL is down or unmigrated, and 403 `identity_not_provisioned` before `make migrate`.

## Implemented in P01

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/intake-policy | principal | Scope policy `scope-v1` (excluded dirs, secret/generated patterns) and quotas; the runner applies it locally |
| POST /v1/projects/{id}/intakes | member | `{mode: zip_upload\|local_runner, display_name}` → 201 intake (creates a source) |
| PUT /v1/intakes/{id}/content | member | Streamed `application/zip`; 413 `upload_too_large` on actual bytes, 415, 409 wrong state; temp removed on abort |
| PUT /v1/intakes/{id}/client-manifest | member | Runner-declared manifest (bounded); verified, never trusted |
| POST /v1/intakes/{id}/finalize | member | 202; idempotent; 409 `no_content` / `client_manifest_required`; 503 retryable |
| POST /v1/intakes/{id}/cancel, GET /v1/intakes/{id}, GET /v1/projects/{id}/intakes | member / viewer | Cancel deletes temporary bytes; status includes `error_code`, `error_message`, `error_details.violations` |
| GET /v1/projects/{id}/snapshots, GET /v1/snapshots/{id} | viewer | Identity (manifest SHA-256, policy, counts, optional Git metadata) and inventory |
| GET /v1/snapshots/{id}/files | viewer | Manifest entries; filters `disposition`, `language`, `q`; offset cursor; `total` |
| GET /v1/files/{id}/content | viewer | Bounded lines (≤2000) as JSON strings, likely secrets masked (`redactions`), 409 for unstored files |
| GET /v1/files/{id}/symbols | viewer | Syntax symbols (≤500) |
| POST /v1/projects/{id}/scans | member | `{snapshot_id}` + optional `Idempotency-Key` (same key → same scan; different snapshot → 409) → 202 |
| GET /v1/projects/{id}/scans, GET /v1/scans/{id} | viewer | State, engine runs (version, ruleset hash, eligible/attempted/succeeded/failed, findings, errors), summary with limitations |
| POST /v1/scans/{id}/cancel | member | Idempotent; completed engine results stay; scan ends CANCELED |
| GET /v1/scans/{id}/events | viewer | SSE `scan_started`, `engine_started`, `engine_finished`, `scan_finished`, `end`; `Last-Event-ID`/`after` resume |
| GET /v1/scans/{id}/coverage | viewer | Per-file engine outcomes ANALYZED/FAILED/NOT_ATTEMPTED with reasons |
| GET /v1/scans/{id}/findings | viewer | Filters severity[], category[], engine, rule_id, file_id, q; ordered by severity/path/line |
| GET /v1/findings/{id} | viewer | Finding + static rule guidance + source excerpt (±6 lines) + manifest SHA-256 + evidence note |
| GET /v1/projects/{id}/overview | viewer | Latest snapshot/scan and counts for dashboard cards |

## Implemented in P02

| Method/path | Auth | Behavior |
|---|---|---|
| POST /v1/projects/{id}/scans | member | adds `cache_mode: use\|refresh` (refresh = full rescan, overwrites cached per-file results) |
| GET /v1/scans/{id} | viewer | engine runs add `enabled_rule_count` (null = open-ended, e.g. vuln DB), `cache_hits`, `cache_misses`, diagnostics (`cache`, Trivy `db_updated_at`/`db_stale`); scan adds `cache_mode`, `lifecycle_applied`, summary `lifecycle` and `cache` |
| GET /v1/scans/{id}/findings | viewer | findings add `anchor_kind` (`source_span`/`file`/`dependency`; lines null for lineless anchors), `correlation_key`, `rule_family`, `details`, `issue {id,status,recheck_state,version}`, `also_reported_by[]`; filter `issue_status[]` |
| GET /v1/findings/{id} | viewer | engine-supplied guidance (e.g. CVE data) preferred over catalog; `related[]` correlated findings; no source excerpt for lineless anchors |
| GET /v1/scans/{id}/coverage | viewer | rows add `cached` |
| GET /v1/projects/{id}/issues | viewer | filters `status[]`, `recheck_state[]`, `severity[]`, `engine`, `q`; offset cursor; `total`, `by_status`, `by_recheck` |
| GET /v1/issues/{id} | viewer | issue + up to 100 audit events + latest finding id; `exception_expired` computed at read time |
| PATCH /v1/issues/{id} | member | `{version, status?, owner?, reason?, expires_at?}`; statuses OPEN/TRIAGED/ACCEPTED_RISK/FALSE_POSITIVE; 409 `version_conflict`, `issue_resolved`, `fix_in_progress`; 422 `reason_required`, `expiry_required`, `expiry_in_past`, `expiry_too_far` (> 365 days) |
| GET /v1/scans/{id}/compare?base={scan} | viewer | groups `new`, `unchanged`, `verified_absent`, `not_rechecked`, `unknown`, `rule_obsolete` (count + ≤200 items + truncated), per-engine compatibility, notes; 422 `different_projects`; 409 `scan_not_finished` |
| GET /v1/scans/{id}/export?format=json\|sarif | viewer | terminal scans only (409); `Content-Disposition: attachment`; JSON = `crp-scan-export/v1` (schema `crp_analysis/schemas/crp-scan-export-v1.schema.json`), SARIF 2.1.0 (`application/sarif+json`) |
| GET /v1/snapshots/{id}/graph | viewer | current build (or `status: none\|failed` with message), counts by kind/classification/relation, modules with file/type counts, module dependency aggregation (≤500), unresolved reasons |
| GET /v1/snapshots/{id}/graph/nodes | viewer | search `q` (label/key), `kind`; `limit` ≤100; offset cursor; 409 `graph_build_failed`, 404 `graph_not_built` |
| GET /v1/snapshots/{id}/graph/nodes/{node}/neighborhood | viewer | `depth` 1–2, `limit` 5–300 nodes, edge cap 600/level, `truncated`; edges carry classification, reason, evidence path/lines/text, extractor |
| GET /v1/snapshots/{id}/graph/nodes/{node}/impact | viewer | file/module-level dependents, `depth` 1–5, `limit` ≤500, `truncated`, unresolved-edge and parse-problem counts, caveats |

Nodes are only reachable through their own snapshot's current build (404 otherwise). Not yet implemented from the table below: `POST /sources/{id}/captures` (registered mounts), Q&A, fixes, PRs. Triage is on issues (`PATCH /v1/issues/{id}`) rather than on per-scan findings.

## Target contract (later phases)
 Prefix /v1. Resolve workspace/project authorization at each boundary. Use structured errors {code, message, request_id, details}; details must not expose absolute paths or secrets.

| Method/path | Contract |
|---|---|
| POST /projects | Create project in authorized workspace |
| POST /projects/{project_id}/intakes | Declare ZIP/files/runner intake and applicable quota policy |
| PUT /intakes/{intake_id}/content | Bounded stream for initial ZIP mode; enforce ownership and media type |
| POST /intakes/{intake_id}/finalize | Idempotently verify/freeze; return async validation status |
| GET /intakes/{intake_id} | Progress, errors, resulting snapshot |
| GET /projects/{project_id}/sources | Authorized sources and registered source IDs |
| POST /sources/{source_id}/captures | Capture only pre-registered root; no arbitrary path payload |
| POST /projects/{project_id}/scans | snapshot_id, scan_mode, policy_id; idempotency key |
| GET /scans/{scan_id} | State, scope, engines, coverage, budget, limitations |
| GET /scans/{scan_id}/events | Authorized SSE stream with monotonic event IDs/resume |
| POST /scans/{scan_id}/cancel | Idempotent cancellation |
| GET /snapshots/{snapshot_id}/files | Cursor-paginated inventory; no absolute paths |
| GET /files/{file_id}/content | Bounded authorized source ranges; safe text encoding |
| GET /scans/{scan_id}/findings | Filtered cursor pagination, severity/evidence/status |
| GET /findings/{finding_id} | Detail, occurrences, evidence and recommendations |
| PATCH /findings/{finding_id} | Allowed triage changes with version check |
| GET /snapshots/{snapshot_id}/graph | Bounded node/edge neighborhood with provenance |
| POST /scans/{scan_id}/questions | Phase 3 evidence-backed Q&A; bounded retrieval |
| POST /findings/{finding_id}/fix-proposals | Phase 5 constrained proposal |
| POST /fix-proposals/{proposal_id}/validate | Approved profile, budget, copied source |
| GET /fix-proposals/{proposal_id}/patch | Authorized patch download |
| POST /fix-proposals/{proposal_id}/pull-requests | Phase 6 freshness/auth checks |

The local CLI uses the same authenticated intake protocol and validates the canonical manifest; it does not require a browser-to-localhost server bridge. Design multipart/many-file transfer before adding folder uploads; do not pretend one ZIP endpoint natively supports every mode.

Long operations return 202 + resource ID and persist state. Define 400 validation, 401 auth, 403/404 authorization policy, 409 state/version conflict, 413 quota exceeded, 422 supported-but-invalid input, 429 throttling and 503 dependencies unavailable. No raw traceback in user responses.

Tests: cross-project object IDs, upload limit enforcement, retry identity, malformed schema, pagination consistency, SSE reconnect/cancellation and exported artifact authorization. Every endpoint shown in UI must have implemented capability/error handling rather than silently returning placeholder success.

