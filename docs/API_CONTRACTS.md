# API contracts

Status: design contract. The implemented subset is listed first; the generated OpenAPI document (`packages/contracts/openapi.json`, also served at `/v1/openapi.json`) is authoritative for implemented endpoints.

## Implemented in P00

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/health/live | none | `{status:"alive", service, version}`; no dependency detail |
| GET /v1/health/ready | credentials only (no DB lookup) | `ReadinessReport` with checks `database` (schema revision vs head), `workflow_service`, `workflow_worker` (fresh pollers), `artifact_store` (write/read/delete probe); each `ok\|failed\|unavailable` with `error_code`, `details`, `latency_ms`. 200 when all OK, otherwise 503 with the same body |
| GET /v1/capabilities | credentials | Capability registry (`available` / `not_configured` / `planned` with phase and reason) that drives UI navigation; `ai_investigation` reflects the server's AI setup |
| POST /v1/auth/session | allowed Origin + token in body | Sets HttpOnly SameSite=Strict session cookie; 401 `invalid_credentials`; 429 `too_many_attempts` after 10 failures/60 s |
| DELETE /v1/auth/session | none | Clears the cookie (204) |
| GET /v1/auth/me | principal | User, auth method, operator flag, workspace grants |
| POST /v1/projects | principal, role ≥ member | `{workspace_id, name, slug?, description?}` → 201; 404 `workspace_not_found` for non-members; 403 `insufficient_role`; 409 `project_slug_conflict` |
| GET /v1/projects | principal | Cursor pagination (`limit` 1–100, `cursor`), optional `workspace_id` (404 if not a member); 400 `invalid_cursor` |
| GET /v1/projects/{project_id} | principal | 404 `project_not_found` for other workspaces |
| DELETE /v1/projects/{project_id} | creator (member) or admin/owner | 204; permanently deletes the project's rows (cascade) and artifacts, then sweeps content blobs no snapshot references (deferred while an intake is validating); 404 for other workspaces; 403 `insufficient_role`; 409 `project_busy` while a scan is queued/running or an intake is validating; cookie sessions need an allowed Origin |
| POST /v1/projects/sample | member | `{workspace_id}` → 202 `{project, intake}` for the built-in sample; demo quotas apply (429 `demo_limit_reached`) |
| POST /v1/diagnostics/workflow-runs | operator (admin/owner) | 202 `{workflow_id, status:"RUNNING"}`; 503 `workflow_unavailable` |
| GET /v1/diagnostics/workflow-runs/{workflow_id} | operator | Status and typed result; 404 for unknown or non-diagnostic IDs |

Cross-cutting: every response has `X-Request-ID`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Cookie-authenticated non-GET requests require an allowed `Origin` (403 `origin_rejected`). Non-loopback peers or `Host` headers get 403 (`non_loopback_client`, `host_not_allowed`). Validation errors are 400 `validation_failed` with locations/messages only (no submitted values). Data endpoints return 503 `database_unavailable` when PostgreSQL is down or unmigrated, and 403 `identity_not_provisioned` before `make migrate`.

## Implemented in P01

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/intake-policy | principal | Scope policy `scope-v1` (excluded dirs, secret/generated patterns) and quotas; the runner applies it locally |
| POST /v1/projects/{id}/intakes | member | `{mode: zip_upload\|local_runner, display_name}` → 201 intake (creates a source) |
| PUT /v1/intakes/{id}/content | member | Streamed `application/zip`; 413 `upload_too_large` on actual bytes, 415, 409 wrong state; temp removed on abort |
| POST /v1/intakes/{id}/upload-ticket | member | 15-minute HMAC ticket for this intake only → `{upload_url, expires_at, max_bytes}`; `upload_url` is relative locally and on the API's own https host in hosted mode (ADR 0009) |
| PUT /v1/intakes/{id}/content?ticket= | ticket | Same upload with the ticket instead of credentials (CORS: PUT from configured web origins, no credentials); 401 for missing/expired/tampered or other-intake tickets |
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

## Implemented in P03 (AI review, ADR 0012)

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/ai/status | credentials | `available`, provider, model, plain `reason`, `admin_hint` (operators only), limits, prices configured, month usage (calls, tokens, token limit, cost or null when prices are unknown, cost limit) |
| GET /v1/projects/{id}/ai-policy | viewer | `enabled` (default false), `max_excerpt_lines`, `version`, `can_edit`, last change |
| PUT /v1/projects/{id}/ai-policy | admin | `{enabled, max_excerpt_lines, version}`; 409 `version_conflict`; audited in `ai_policy_events` |
| POST /v1/projects/{id}/ai-runs | member | `{kind: question\|file_review\|finding_review, question?, paths? (1–5 reviewable files), finding_id?, snapshot_id?}` → 202 QUEUED run. 409 `ai_policy_disabled` / `ai_unavailable` / `no_snapshot` / `snapshot_not_ready`; 429 `ai_monthly_limit`; 422 `question_required` / `paths_required` / `finding_required` / `unknown_paths`; 404 foreign project/finding/snapshot; 503 `workflow_unavailable` (the run is stored FAILED) |
| GET /v1/projects/{id}/ai-runs | viewer | Newest first, `limit` ≤100; `finding_id` narrows to second opinions on one finding |
| GET /v1/ai-runs/{id}/export?format=json\|sarif | viewer | Finished runs only (409 `ai_run_not_finished`); JSON `crp-ai-run-export/v1` (schema `crp_analysis/schemas/crp-ai-run-export-v1.schema.json`) keeps rejected suggestions marked; SARIF 2.1.0 tool `refactorX AI review` excludes them and flags every result `aiGenerated`. Separate from scan exports on purpose |
| GET /v1/ai-runs/{id} | viewer | Run with state, usage (tokens in/out, cache, `usage_reported`, cost or null, excerpts read), answer (`answer` with citations and `evidence_class`, or `review` with reviewed paths and optional finding `assessment`), steps, limitations and AI findings (severity, confidence and `evidence_class` separate; anchors with verification `status` and file `sha256`) |
| POST /v1/ai-runs/{id}/cancel | member | Idempotent; records the request and cancels the workflow; no model call starts afterwards |
| GET /v1/findings/{id} | viewer | now also returns `project_id` |

Run states: QUEUED, RUNNING, SUCCEEDED, PARTIAL, BUDGET_EXHAUSTED, FAILED (`error_code` e.g. `interrupted`, `provider_rate_limited`, `no_result`), CANCELED. Runs are never retried automatically.

## Implemented in P04 (framework packs, ADR 0013)

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/snapshots/{id}/graph | viewer | adds `frameworks`: one pack report per detected platform (`id`, `name`, `adapter`, `status`, `version`, `version_status` supported/unsupported_version/unknown_version, `version_evidence` path:line, `supported_versions`, `capabilities` [id, label, state available/partial/unavailable, detail], `relations`, `components`, `rules`, `notes`); `module_dependencies[].relation` may be `config` (configuration links such as bean injection across modules) |
| GET /v1/snapshots/{id}/graph/nodes?kind=component | viewer | framework components; `attributes.framework` (`sap`/`sf`) and `attributes.component_type` (spring_bean, spring_alias, itemtype, enumtype, sobject, field, apex_class, apex_trigger, lwc, flow, permission_set, custom_metadata_record) |
| GET /v1/scans/{id} | viewer | engine runs may include `pmd-apex` and `frameworks` (NOT_APPLICABLE when the upload has no matching files) |
| GET /v1/scans/{id}/compare | viewer | engines not applicable on both sides say so in `note` |
| GET /v1/capabilities | credentials | adds `sap_commerce_pack` and `salesforce_pack` (available, experimental) |

## Implemented in P05 (validated fixes, ADR 0014)

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/findings/{id}/fix-options | viewer | Approved recipes for the finding's rule: `recipe_id`, `title`, `available`, plain `reason` when this occurrence cannot be fixed automatically (e.g. no captured ESLint fix, code no longer matches) |
| POST /v1/findings/{id}/fix-proposals | member | `{recipe_id}` → 201 proposal bound to the finding's upload, file base hash, result hash and patch hash; idempotent per identical patch (returns the existing open proposal). 422 `unknown_recipe`; 409 `no_fix` / `content_not_stored`; 404 `file_not_found` |
| GET /v1/projects/{id}/fix-proposals | viewer | Newest first; `finding_id` narrows to one finding |
| GET /v1/fix-proposals/{id} | viewer | Proposal: `kind` (`recipe`), `recipe_id`, title, explanation, `behaviour_note`, `state`, path, `edits` (start/end line, original and replacement lines), `patch` (unified diff), hashes, `changed_lines`, `edited`, budget (`validations_used`/`max_validations`), `labels` (plain applicability/validation statements), `finding` summary, `latest_validation` (state, steps, summary, `current` = bound to the present patch) |
| PUT /v1/fix-proposals/{id}/edits | member | `{version, edits: [{start_line, replacement[]}]}`; 409 `version_conflict` / `fix_rejected` / `patch_conflict`; 422 `fix_not_allowed` (`details.violations`: `unsafe_path`, `out_of_scope`, `config_change`, `suppression_added`, `test_weakened`, `too_large`), `unknown_edit`, `no_change`. Success resets the proposal to PROPOSED and marks it `edited` |
| POST /v1/fix-proposals/{id}/reject | member | `{reason}` (≤500); terminal |
| POST /v1/fix-proposals/{id}/validations | member | 202 QUEUED validation of the current patch (copies only; no project code executed). 409 `validation_running` / `validation_budget_exhausted` (5 per proposal) / `fix_rejected`; 503 `workflow_unavailable` (validation stored FAILED, budget slot refunded) |
| POST /v1/fix-validations/{id}/cancel | member | Idempotent; records the request and cancels the workflow |
| GET /v1/fix-proposals/{id}/patch | viewer | `text/x-diff` attachment: `#` header (fix, finding, upload and manifest hash, file base/result hashes, patch hash, validation state, "not compiled, built or tested") followed by a Git-compatible diff that `git apply -p1` accepts on a copy of exactly that upload |
| GET /v1/fix-proposals/{id}/summary | viewer | JSON `crp-fix-export/v1` (schema `crp_analysis/schemas/crp-fix-export-v1.schema.json`): applicability, hashes, edits, validation steps including what was not run, known risks |
| POST /v1/fix-proposals/{id}/rebase | member | `{snapshot_id}` of another ready upload in the project → 201 new proposal (`rebased_from`) when every edit's original lines are found exactly once; 409 `patch_conflict` otherwise; 422 `same_snapshot`; 409 `snapshot_not_ready` |
| GET /v1/capabilities | credentials | `fix_workbench` is `available` |

Proposal states: PROPOSED, VALIDATING, VALIDATED (every step that could run passed for the current patch), VALIDATION_FAILED, REJECTED. Validation states: QUEUED, RUNNING, PASSED, FAILED, CANCELED; steps `integrity`, `syntax`, `checks`, `tests`, `build` with `passed` / `failed` / `not_run` and a plain `detail`.

## Implemented in P06 (GitHub, ADR 0015)

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/github/status | credentials | `available` (app can authenticate), `linking_available`, `webhooks_available`, plain `reason`, `admin_hint` (workspace admins only, never the demo account), `install_url` |
| POST /v1/workspaces/{id}/github/link | admin (not demo) | `{authorize_url}`; stores a one-time state (SHA-256 only, 10 min, bound to user and workspace). 503 `github_not_configured` / `linking_not_configured` |
| GET /v1/github/callback | none (browser redirect) | 303 to `#/github?code&state` (or `?error`); touches no data (the session cookie is SameSite=Strict) |
| GET /v1/github/setup | none (browser redirect) | 303 to `#/github?installed=1`; the forged-able `installation_id` is ignored |
| POST /v1/workspaces/{id}/github/link/complete | admin (not demo) | `{code, state}` → `{linked: [installation], skipped: [{account, reason}]}`; only installations the GitHub user can access (`/user/installations`), one workspace per installation; repositories synced. 403 `link_state_invalid` (another user/workspace), 409 `link_state_used` / `link_state_expired`, 403 `github_authorization_failed` |
| GET /v1/workspaces/{id}/github/installations | member | installations (account, suspended/revoked, synced) with repositories (default branch, private, removed, connected project) |
| POST /v1/workspaces/{id}/github/installations/{installation}/sync | admin | refresh the repository list from GitHub (missing ones marked removed) |
| DELETE /v1/workspaces/{id}/github/installations/{installation} | admin | unlink: connections removed, active reviews canceled; reviews and snapshots stay |
| POST /v1/github/webhook | `X-Hub-Signature-256` | 401 `invalid_signature`; 400 `invalid_webhook` (headers/JSON); 413 above `CRP_GITHUB_WEBHOOK_MAX_BYTES`; 503 `webhooks_not_configured`. Recorded by `X-GitHub-Delivery` (`duplicate` on redelivery). `ping`; `installation` (deleted/suspend/unsuspend/new permissions); `installation_repositories`; `repository` renamed; `push` to the default branch → branch review; `pull_request` opened/reopened/synchronize/ready_for_review/base edited → pull request review (forks only when allowed), closed → its reviews canceled. 202 `accepted` with review ids, otherwise 200 `ignored`/`ok` with a plain `detail` |
| GET /v1/projects/{id}/git-connection | viewer | `{connected, can_edit, status (active/access_removed/installation_revoked/installation_suspended), status_reason, repository, installation_account, review_pushes, review_pull_requests, review_forks (false), publish_checks (false), publish_pull_requests (false), check_fail_threshold (never/critical/high/medium), reconcile_days, last_full_review_at, version}` |
| PUT /v1/projects/{id}/git-connection | admin (not demo) | `{repository_id}` → 201; creates the `github` source and starts a full review of the default branch. 409 `already_connected` / `repository_connected` / unavailable status; 404 `repository_not_found` (other workspace) |
| PATCH /v1/projects/{id}/git-connection | admin (not demo) | policy fields + `version`; 409 `version_conflict`; audited in `git_connection_events` |
| DELETE /v1/projects/{id}/git-connection | admin (not demo) | disconnect; active reviews canceled |
| POST /v1/projects/{id}/code-reviews | member | `{kind: branch\|pull_request, pull_request?, full?}` → 202 review (manual reviews also of forks). 409 `not_connected` / unavailable status; 422 `pull_request_required`; 503 `workflow_unavailable` (stored FAILED) |
| GET /v1/projects/{id}/code-reviews | viewer | newest first; `kind`, `pull_request`, `limit` ≤ 100 |
| GET /v1/code-reviews/{id} | viewer | review: kind, trigger, state, ref, head/base/merge-base commits, pull request title/author/url/fork, snapshots and scans, `changes` (added/modified/removed/renamed/configuration/impacted), `result` (new/unchanged/fixed/not_rechecked counts, by severity, top new and fixed items, incomplete checks, cache reuse), publish state/error, superseded_by, error |
| POST /v1/code-reviews/{id}/cancel | member | idempotent |
| POST /v1/fix-proposals/{id}/pull-request | member | 201 `{number, url, repository, branch, base_ref, base_sha, commit_sha}`; idempotent per fix. 409 `pull_request_unavailable` (plain reason: not allowed, fork, not validated, not from GitHub), 409 `stale_patch` `{reviewed, current}`, 409 `github_rejected`, 409 access codes, 503 `github_unavailable` / `github_not_configured` |
| GET /v1/fix-proposals/{id} | viewer | adds `pull_request`, `pull_request_available`, `pull_request_reason` |
| GET /v1/snapshots/{id} | viewer | adds `git_ref`, `git_provider`, `git_repository`, `git_tree_sha`, `git_capture` (how the capture was checked against the commit) |
| GET /v1/capabilities | credentials | `git_integration` is `available` or `not_configured` |

Review states: QUEUED, CAPTURING, SCANNING, PUBLISHING, SUCCEEDED, PARTIAL, FAILED (`github_unavailable`, `installation_not_found`, `installation_suspended`, `capture_rejected`, `scan_failed`, `interrupted`, …), SUPERSEDED, SKIPPED (`already_reviewed`, `pull_request_closed`, `fork_not_reviewed`, `branch_not_found`), CANCELED. Scan `mode`: `baseline`, `pull_request`, `reference`.

## Implemented in P08 (fix workspaces, ADR 0016)

| Method/path | Auth | Behavior |
|---|---|---|
| POST /v1/projects/{id}/change-sets | member | `{snapshot_id?, title?}` → 201 workspace on that upload (default: the latest ready upload). 404 `snapshot_not_found` (other project or a derived copy); 409 `no_snapshot` / `snapshot_not_ready` |
| GET /v1/projects/{id}/change-sets | viewer | Open workspaces, newest change first: title, upload name, files changed, last check state |
| GET /v1/change-sets/{id} | viewer | `state` (draft/checking/ready/exported), upload and review it is based on, `content_sha256`, `version`, `can_edit`, changed `files` (action, language, size, lines, policy `flags`, provenance `sources`), `latest_check` (with `current`), the 40 newest `events` |
| DELETE /v1/change-sets/{id} | member | 204; removes its checks and the copies they scanned (the upload stays). 409 `check_running` |
| GET /v1/change-sets/{id}/file?path= | viewer | Upload and current text (`base_content`, `content`), hashes, `action`, `editable` + plain `reason` (binary, too large, not stored, not UTF-8), `line_ending`. 404 `file_not_found`; 422 `invalid_path` |
| PUT /v1/change-sets/{id}/file | member | `{version, path, content, finding_ids?}` → `{change_set, flags}`; adds or changes a file (CRLF kept; equal to the upload = change removed). Flags are information for manual edits (`config_change`, `suppression_added`, `test_weakened`). 409 `version_conflict` / `not_editable` / `workspace_full` / `workspace_archived`; 413 `file_too_large`; 422 `invalid_path` |
| POST /v1/change-sets/{id}/file/delete | member | `{version, path}`; deleting an added file drops it. 404 `file_not_found`; 409 `not_editable` |
| POST /v1/change-sets/{id}/file/revert | member | `{version, path}`; back to the uploaded content. 404 `file_not_changed` |
| POST /v1/change-sets/{id}/fixes | member | `{version, finding_ids[]}` or `{version, engine, rule_id}` (every occurrence of a rule in the upload's review) → `{applied[{finding_id, path, recipe_id, title}], skipped[{finding_id, path, reason}], change_set}`. Recipes are computed on the upload and applied at the same lines or where those lines now are; otherwise skipped ("changed in this workspace"). Strict policy: suppressions, weakened tests and oversized changes are skipped. 409 `no_review`; 422 `nothing_selected` |
| GET /v1/change-sets/{id}/issues | viewer | The upload's findings as a queue: severity, file and line, `recipe_available`, `changed`, `outcome` of the latest check (fixed / still_present / suppressed / not_rechecked). Filters `outcome` (incl. `unchecked`), `severity`, `q`, `fixable`; `limit` ≤ 500, cursor |
| POST /v1/change-sets/{id}/checks | member | 202 QUEUED check of exactly the current files (digest stored). 422 `nothing_to_check`; 409 `check_running`; 503 `workflow_unavailable` (stored FAILED) |
| GET /v1/change-set-checks/{id} | viewer | state, `content_sha256`, `current`, derived snapshot, scans, `result` (`counts` incl. `new`, per-finding `outcomes`, up to 50 `new_items`, incomplete checks, cache reuse, `compiled: false`, `not_verified`, `types` = Tier 0 TypeScript type-check `{state (checked/not_applicable/skipped/unavailable/timeout/failed), tool, before, after, new_count, new[≤50 {path, line, column, code, message}], fixed, unresolved_imports, notes, reason}`), error |
| POST /v1/change-set-checks/{id}/cancel | member | idempotent |
| GET /v1/change-sets/{id}/export?format= | viewer | `patch` (`text/x-diff`, `git apply -p1`), `mbox` (one commit for `git am`), `changed` (ZIP: changed files + `refactorx-changes.json` + `.diff`), `full` (ZIP: stored files byte for byte with the edits + `refactorx-not-included.txt`), `summary` (JSON `crp-change-set-export/v1`, schema `crp_analysis/schemas/crp-change-set-export-v1.schema.json`), `summary-md`. Every download names the upload and hashes and is recorded. 422 `nothing_to_export` |
| GET /v1/snapshots/{id}/compare?base= | viewer | Two uploads of one project: `counts` and `changes` (added/modified/removed/renamed, stored text files; ≤ 2,000, `truncated`). 422 `different_projects` |
| GET /v1/snapshots/{id}/compare/file?base=&path=&previous_path= | viewer | `before`/`after` text, hashes and a plain `note` (new, removed, not stored) |

| POST /v1/change-sets/{id}/ai-fixes | member | `{finding_id}` → 202 AI run of kind `fix` for the file as it is in the workspace (target hash stored). 409 `ai_policy_disabled` / `ai_unavailable` / `not_editable` (incl. not UTF-8) / `file_deleted` / `ai_fix_running`; 422 `no_location`; 404 `finding_not_found` (not in the upload's review); 429 `ai_monthly_limit`; 503 `workflow_unavailable` |
| GET /v1/change-sets/{id}/ai-fixes?path=&finding_id= | viewer | Newest first (≤ 20): AI runs with `fix` = `{text, abstained, uncertainty, path, base_sha256, candidates[{index, title, explanation, behaviour_note, confidence, label, patch, changed_lines, problems, steps (P05 checks), passed, summary, applicable, reason, applied_at}]}` |
| POST /v1/change-sets/{id}/ai-fixes/{run}/apply | member | `{version, candidate}` → `{change_set, flags}`; recorded as an `ai` change with the finding. 409 `ai_fix_not_ready` / `candidate_not_applicable` (reason) / `candidate_applied` / `ai_fix_stale` / `file_deleted` / `version_conflict`; 422 `fix_not_allowed`; 404 `ai_fix_not_found` / `candidate_not_found` |
| GET /v1/change-sets/{id} | viewer | adds `ai` `{available, reason}` (project switch and server setup) and `pull_request` `{applies (GitHub upload), available, reason, opened[{number, url, repository, branch, base_ref, base_sha, commit_sha, content_sha256, current, created_at}]}` |
| POST /v1/change-sets/{id}/pull-request | member | 201 one pull request with every change of the checked workspace on the reviewed branch (one commit; deletions included; executable bits kept); idempotent per content. 409 `pull_request_unavailable` (plain reason: not from GitHub, not allowed, fork, not checked), `stale_patch` `{reviewed, current}`, `github_rejected`, access codes; 422 `nothing_to_export`; 503 `github_not_configured` / `github_unavailable` |
| GET /v1/ai-runs/{id} | viewer | kind `fix` adds `change_set_id` and `fix` (as above; edits and hashes stay internal); `answer` is null for fix runs. AI exports carry `fix` |

Derived copies scanned by checks are not listed as uploads, not counted in project overviews and not offered to AI by default; their `change_set` scans never change issues and are not listed under Reviews.

## Implemented in P10 (architecture metrics, ADR 0018)

| Method/path | Auth | Behavior |
|---|---|---|
| GET /v1/snapshots/{id}/architecture | viewer | From the current graph build: `summary` (components, dependencies, component edges, cycles, components in cycles, zone of pain, zone of uselessness, test files left out, edges not counted, average distance), `components` (key, kind package/folder, files, lines, types, abstract types, Ca, Ce, fan-in, fan-out, instability, abstractness, distance — null when not measurable —, zone, in cycle; sorted by distance), up to 400 `edges` (`edges_truncated`), `cycles` (members, heaviest edges and lightest cut items up to 50 each, full counts, `cut_weight`, `exact`), `notes`, `extractor`, `algorithm`. 404 `graph_not_built`; 409 `graph_build_failed` |

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
| POST /scans/{scan_id}/questions | Phase 3 evidence-backed Q&A; implemented as `POST /v1/projects/{id}/ai-runs` (above) |
| POST /findings/{finding_id}/fix-proposals | Phase 5 constrained proposal; implemented (above) |
| POST /fix-proposals/{proposal_id}/validate | Approved profile, budget, copied source; implemented as `POST /v1/fix-proposals/{id}/validations` (source-level; tests/build not run) |
| GET /fix-proposals/{proposal_id}/patch | Authorized patch download; implemented (above) |
| POST /fix-proposals/{proposal_id}/pull-requests | Phase 6 freshness/auth checks; implemented as `POST /v1/fix-proposals/{id}/pull-request` (above) |

The local CLI uses the same authenticated intake protocol and validates the canonical manifest; it does not require a browser-to-localhost server bridge. Design multipart/many-file transfer before adding folder uploads; do not pretend one ZIP endpoint natively supports every mode.

Long operations return 202 + resource ID and persist state. Define 400 validation, 401 auth, 403/404 authorization policy, 409 state/version conflict, 413 quota exceeded, 422 supported-but-invalid input, 429 throttling and 503 dependencies unavailable. No raw traceback in user responses.

Tests: cross-project object IDs, upload limit enforcement, retry identity, malformed schema, pagination consistency, SSE reconnect/cancellation and exported artifact authorization. Every endpoint shown in UI must have implemented capability/error handling rather than silently returning placeholder success.

