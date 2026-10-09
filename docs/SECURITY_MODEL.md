# Security model

## Trust zones

Trusted: approved development instructions, control-plane configuration, signed/pinned adapter/rule artifacts and authenticated operator actions. Untrusted: uploaded source, local-folder contents, configuration inside source, generated model output, dependency/build scripts and raw scanner output. External documentation is reference data and may be stale or adversarial.

## Required controls

- Intake enforces SOURCE_INTAKE.md before source becomes eligible.
- Every API/job/query/download/export is workspace/project authorized; identifiers alone grant no access.
- Source lives outside public assets and trusted repo/config directories; escape source/model content in UI.
- Parsers and analyzers have CPU, memory, disk, time, output and egress budgets. XML disables external entities.
- Approved analyzer config is distinct from project-owned executable lint/build config. Supporting project config is a separately enabled sandboxed capability.
- No master provider key, production credential, host filesystem root, privileged container or host Docker socket is passed to a worker.
- Separate identity/credential broker and publication service from untrusted analysis execution.
- Block cloud metadata access and unnecessary network routes; use approved dependency mirrors/proxy where needed.
- Treat AGENTS/CLAUDE/SKILL/MCP files within snapshots as data. They cannot override system prompts, tools, rules or endpoints.
- Store provider secrets server-side; redact secrets in findings, logs, traces and AI context. Prompts/transcripts have source-equivalent access and retention controls.
- Validate model/tool responses against schemas; enforce path containment and source hashes before patch application.

## Deployment tiers

Local development is bound to loopback, uses a generated local credential and refuses unsafe nonlocal exposure. Do not confuse this with enterprise multi-tenant isolation. Hosted release requires reviewed authentication, private artifact storage, TLS, a stronger hostile-execution boundary or dedicated runners, and tenant-isolation testing.

A local snapshot-upload runner sends source to the server. A private-execution runner is a different later capability with an explicit inventory of what results/snippets leave it. Neither mode silently sends source to a model provider.

## Retention and response

Document policies for raw archives, extracted blobs, graphs, vectors, reports, transcripts, patches, audit events and backups. Cancellation/rejection clean temp state; deletion cascades to derived data according to policy. Revoked users/projects lose access to active jobs and cached retrieval.

Security tests include archive/path attacks, SSRF-like registered-source abuse, malicious linter config, prompt injection, command injection, cross-project IDs, log/Markdown injection, secrets, stale hashes, resource exhaustion and worker cleanup. No compliance or sandbox-isolation claim without evidence. Define maintainer reporting and incident-response owners before release.


## Implemented controls (P00) and evidence

| Control | Implementation | Test evidence |
|---|---|---|
| Loopback-only local deployment | Settings refuse non-loopback bind/origins; middleware rejects non-loopback peers and Host headers | `test_config.py`, `test_health_and_auth.py`, live refusal in P00 report |
| Generated local credential outside source control | `.local/secrets` (0700/0600), symlink and permission checks | `test_states_secrets_logging.py` |
| Browser session hardening | HttpOnly SameSite=Strict HMAC cookie; Origin check on cookie writes; failed-login throttle | `test_health_and_auth.py` |
| Workspace authorization | Principal grants applied before every project query; non-members get 404 | `test_projects.py` |
| DB-level scope integrity | Composite FKs and CHECK constraints | `test_migrations.py` |
| Artifact containment | Validated keys, dirfd traversal with `O_NOFOLLOW`, owner-only files, atomic bounded writes, root outside repo, `.crp-untrusted` marker | `test_artifact_store.py`, `test_config.py` |
| Redaction | Log filter masks bearer tokens, session cookies, URL passwords; settings errors printed without input values; API errors never include tracebacks or submitted values | `test_states_secrets_logging.py`, `test_config.py`, `test_health_and_auth.py` |
| Development context isolation | Context tools exclude secrets, `.local`, vendor dirs, symlinks and untrusted-marked dirs; parse with `ast` only | `test_context.py` |
| Subprocess safety | Fixed executables with argument arrays in all tooling | code review (`crp_devtools.infra`, `supervisor`) |

Not implemented yet: upload/intake controls, analyzer sandboxing and resource limits, egress controls, audit events, retention jobs, TLS/hosted identity. Local mode is a single-user development boundary, not a hostile multi-tenant boundary.

### Added in P01

| Control | Implementation | Evidence |
|---|---|---|
| Archive safety | Validation before storage: traversal/absolute/drive/backslash/control characters, symlink/special entries, encryption, unsupported compression, case/Unicode/file-vs-dir collisions, entry/size/ratio limits on actual bytes, CRC; nested archives never unpacked | `test_zip_intake.py`, E2E rejection |
| Upload bounds | Streaming limit on actual bytes (Content-Length advisory), private temp file removed on abort/limit | pipeline contract test |
| Secrets not stored | Secret-candidate files excluded by policy (server) and skipped before upload (runner); excerpts masked (heuristic) | intake/runner tests, `CodeView`/finding tests |
| Untrusted repository content | Project ESLint config never executed; inline directives disabled; PMD suppression markers neutralised; `AGENTS.md`/`CLAUDE.md` treated as data | engine tests, pipeline test (ruleset hash unchanged) |
| Engine isolation (local) | Read-only copies, scrubbed environment, fixed executables, wall-time/output/heap caps, process-group kill; **no OS sandbox or egress block yet** (K-P01-01) | engine tests |
| Runner guarantees | Explicit folder only, read-only, no symlink following, disclosure + confirmation, server-verified manifest | runner tests |

### Added in P02

| Control | Implementation | Evidence |
|---|---|---|
| Engine supply chain | Opengrep/Trivy binaries pinned by SHA-256 and verified with Sigstore (`crp-dev engines --verify-signatures`); Trivy 0.69.3 chosen as a release verified safe after GHSA-69fq-xp46-6x23 | ADR 0007, P02_REPORT |
| No scan-time network | Trivy `--offline-scan --skip-db-update --skip-java-db-update --disable-telemetry --skip-version-check`; Opengrep `--disable-version-check`, owned local rules only | `trivy.py`, `opengrep.py` |
| Repository tool config inert | Opengrep `--disable-nosem`, no ignore files; Trivy trusted `--secret-config` + empty `--ignorefile` outside the snapshot | test_security_engines.py |
| Secret values never stored | Trivy `Match`/`Code` stripped from stored reports; findings carry rule/file/line only; excerpts redacted | test_security_engines.py, E2E |
| Untrusted manifests | pom.xml with DTD/entity declarations refused; JSON/JSONC parsed as data; parent POMs, `extends` chains not fetched | test_graph.py |
| Scope-bound derived data | Graph anchors constrained by composite FKs to the build's snapshot; issues/cache project-scoped (cascade delete); cache key includes project and content hash | test_migrations.py, test_engine_cache_keys.py |
| Authorization of new endpoints | issues, compare, exports, graph resolve through `get_scoped`; triage requires MEMBER; cross-workspace/cross-snapshot IDs → 404 | test_p02_analysis.py |

### Added for the hosted deployment (ADR 0009)

| Control | Implementation | Evidence |
|---|---|---|
| Hosted tier is explicit | `CRP_ENVIRONMENT=hosted` required for a public bind; demands Host allowlist, https origins, https public API URL, token ≥ 32 chars | test_config.py, app startup |
| Host / origin / transport | Host allowlist (liveness exempt), allowed Origins (+ anchored preview regex) for cookie state changes, Secure SameSite=Strict cookies, HSTS | test_hosted_mode.py |
| Direct uploads | 15-minute HMAC ticket bound to one intake, derived from the access token (rotation revokes); CORS only PUT, no credentials, configured origins | test_hosted_mode.py, test_hosted_smoke.py |
| Container | Unprivileged uid 10001 after disk ownership fix; secrets passed as env, token written 0600, raw token not passed to children; engines keep scrubbed environments | entrypoint.sh, test_hosted_env.py |
| Build supply chain | Digest-pinned base images, SHA-256 + cosign-verified Linux engines, SHA-256 Temporal CLI, SHA-pinned GitHub Actions, frozen lockfiles | Dockerfile, ci.yml, engines.py |
| Data location | Uploaded source stored in the owner's Render account: in PostgreSQL only (free lite profile, ADR 0010) or on the disk + database (standard); AI egress only as described in "AI review" below | DEPLOYMENT.md |
| Offline vulnerability DB | Baked into the image (digest-pinned build); the daily refresh is the only network use, downloads into a new directory and swaps a symlink atomically, never while a scan in the same process reads it | trivy_db.py, test_trivy_db.py |

### Project deletion

| Control | Implementation | Evidence |
|---|---|---|
| Who may delete | The project's creator (member role) or a workspace admin/owner; viewers never; other workspaces get 404 | test_project_deletion.py |
| Safe reclamation | Rows cascade from `projects`; per-project artifacts deleted by prefix; shared content blobs deleted only when unreferenced and while no intake validates (SHARE lock on `intakes`) | test_project_deletion.py, test_sample_project.py |
| Accident protection | UI confirmation requires typing the project name; running reviews/uploads block deletion (409) | foundation.spec.ts |
| Audit | Structured log `project deleted` (project id/name, user id, counts); no database audit trail yet | project_deletion.py |

### Added for the demo account and sample project (ADR 0011)

| Control | Implementation | Evidence |
|---|---|---|
| Demo is opt-in | `CRP_DEMO_ENABLED` (default false); `POST /v1/auth/demo-session` returns 404 when disabled; demo cookies stop being accepted when it is disabled | test_demo_and_sample.py |
| Demo isolation | Subject `demo:guest` has MEMBER access to the `demo` workspace only; all data routes resolve through workspace grants, so owner projects return 404; not an operator (no diagnostics/system status); no bearer-token access | test_demo_and_sample.py, test_sample_project.py |
| Demo abuse bounds | Allowed Origin required to start a demo session; quotas on demo projects and scans per hour (429 `demo_limit_reached`); upload size limits apply | test_demo_and_sample.py, test_sample_project.py |
| Shared-demo disclosure | Every page shows that the demo workspace is shared with other visitors | ui-tour.spec.ts |
| Sample secrets | The sample's fake access token is generated at runtime, never committed | test_demo_and_sample.py |

Residual risk: demo visitors share one workspace (they see each other's uploads) and their archives are analysed by the same unsandboxed engines as the owner's on a single-tenant instance. Disable the demo before storing real customer code on the same deployment.

### AI review (P03, ADR 0012)

| Control | Implementation | Evidence |
|---|---|---|
| No egress by default | No provider unless `CRP_AI_PROVIDER` and a key are set; each project's AI policy is off until a workspace admin/owner switches it on (explicit confirmation, audited in `ai_policy_events`); re-checked at run creation and before the first call | test_ai_api.py, test_ai_run.py |
| Key handling | Key only in the server environment (`CRP_AI_API_KEY`) or an owner-only file (`CRP_AI_API_KEY_FILE`); never stored in the database, logged, returned or included in reprs; endpoints must be https (http only for localhost) | test_ai_providers.py |
| Minimal, masked disclosure | Only excerpts the model asks for through five read-only, snapshot-scoped tools (at most `max_excerpt_lines` per read); every line masked by the secret redactor before sending and before storing transcripts | test_secret_values_never_reach_the_provider, test_ai_run.py |
| Untrusted source stays data | Excerpts fenced with path/lines/hash and fence-breaking text neutralised; AGENTS.md/CLAUDE.md/MCP files flagged; the system prompt forbids following repository instructions; no shell, network, MCP or model-chosen endpoints | test_prompt_injection_stays_data_and_cannot_add_tools, test_fences_cannot_be_closed_from_source |
| Tool argument validation | Strict schemas (no extra fields, no coercion, bounded sizes); unknown tools and paths outside the upload refused | test_malicious_tool_arguments_are_refused |
| Claims need evidence | Every citation checked against the frozen upload; unverifiable claims are labelled or hidden; model output never changes deterministic findings | test_fake_and_mismatched_anchors_are_rejected |
| Spend bounds | Per-run call/tool/token/time/cost limits and monthly token/cost caps; usage recorded per call; runs never retried automatically | test_token_and_cost_limits_stop_the_run, test_monthly_token_limit_blocks_new_runs |
| Scope and retention | Runs, calls, findings and transcripts are project-scoped (other workspaces get 404) and deleted with the project; `CRP_AI_KEEP_TRANSCRIPTS=false` stops storing transcripts | test_runs_of_other_workspaces_are_invisible, test_ai_run.py |

Residual risk: masking is heuristic, so a secret the redactor misses would reach the provider for projects with AI switched on; the provider's own retention terms apply to what it receives. Switch AI on only for code you may share with that provider.

### Framework packs (P04, ADR 0013)

| Control | Implementation | Evidence |
|---|---|---|
| Configuration is data | Spring XML, items.xml, ImpEx, extension descriptors and Salesforce metadata are parsed, never executed, built or deployed; ImpEx is read for header types and `springId` only | test_frameworks.py |
| Secure XML | expat with DOCTYPE, entity, unparsed-entity and external-reference handlers that refuse the document; parameter entities disabled; element count bounded; refusals become FAILED coverage and partial capability | test_frameworks.py (billion laughs, XXE, external DTD) |
| No platform credentials | No SAP distribution, Salesforce org or token is used; validation profiles are conditional and documented only | FRAMEWORK_ADAPTERS.md |
| Untrusted repository config stays inert | Project PMD/ESLint configs, `.opencodereview`-style rule files and Salesforce Code Analyzer configs are never loaded; only platform-owned rule sets run | ENGINE_ADOPTION.md |

### Validated fixes (P05, ADR 0014)

| Control | Implementation | Evidence |
|---|---|---|
| Upload is never modified | Patches are applied in memory and checked on a read-only copy of the single changed file in a private temporary folder, removed afterwards; stored upload bytes and the captured folder stay unchanged | test_fix_is_validated_by_the_temporal_worker (folder digest), test_ladder_copies_only_the_changed_file |
| No project code is executed | The ladder writes only the changed file and runs the platform's own trusted engines on it; project build scripts, package lifecycle hooks, test files and analyzer configs are never copied or run; tests and builds are reported "not run" | test_p05_fixes.py (hostile `package.json` scripts, `eslint.config.mjs`, Makefile, gradlew, build.gradle, JS and Java tests leave no mark) |
| Scope and path safety | Relative paths inside the upload only (absolute paths and `..` refused); edits limited to the finding's file; workspace writes use `O_EXCL`/`O_NOFOLLOW` | test_policy_refuses_changes_that_hide_problems, test_edits_apply_exactly_or_conflict |
| Fixes cannot hide problems | Added suppression markers, inline ESLint rule settings, fewer test annotations/assertions, analyzer/build configuration changes and changes over 60 lines are refused for recipes and for reviewer edits | test_policy_refuses_changes_that_hide_problems, test_prepare_edit_reject_and_export |
| Results bound to exact content | Stale base, conflicting edits, and result/patch hash mismatches fail integrity; each validation records the patch and result hashes it checked, and counts only while they match the proposal; downloaded patches name the upload and hashes and apply only to that upload | test_ladder_detects_stale_base_and_tampered_patches, test_prepare_edit_reject_and_export (`git apply --check` passes on the exact copy, fails on a changed one) |
| Bounded execution | 5 validations per proposal, one at a time; engine wall-time and output caps; 30-minute activity limit; cancellable while running | test_validation_requests_budget_and_service_outage, test_ladder_cancellation, test_running_validation_stops_and_can_run_again |
| Authorization | Reads need project viewer access, changes need member access; proposals of other workspaces are 404 | test_fixes_of_other_workspaces_are_invisible |

Residual risk: the ladder is source-level. A fix that passes it may still fail to compile against the rest of the project or change behaviour where the "What to watch" note warns; reviewers build and test the patch in their own environment.

### Fix workspaces (P08, ADR 0016)

| Control | Implementation | Evidence |
|---|---|---|
| Upload is never modified | Revisions are new content-addressed blobs; the upload's file rows and blobs are only read; checks scan a derived snapshot | test_edit_files_with_flags_conflicts_and_line_endings (blob digests unchanged), test_workspace_check_on_the_temporal_worker / _lite_profile (folder digest, upload list, issues unchanged) |
| No project code is executed | Checks run the platform's trusted engines on the derived snapshot exactly like a review; nothing is built, installed or tested ("not compiled") | change_set.py; test_p08_workspace.py (`compiled: false`) |
| Path and content safety | Canonical relative paths, `.git/` and directories refused; binary, oversized, excluded and non-UTF-8 files not editable (strict decoding, no replacement characters written); size and file-count limits; files added as `.env`-like secrets are excluded in checks exactly as at upload | test_edit_files_…, test_encodings_unicode_and_limits, test_derived_manifest_applies_revisions_over_the_base |
| Hiding is never fixing | Policy flags on every save; recipes refuse suppressions, weakened, skipped or focused tests and oversized changes; a finding that vanishes from a file that gained a suppression is `suppressed`, never `fixed` | test_manual_flags_are_information_and_recipe_flags_are_strict, test_policy_refuses_changes_that_hide_problems, test_p08_workspace.py |
| Results and downloads bound to content | Workspace digest on every check, event and download; patches name upload and hashes and fail on changed code; the full ZIP copies unchanged files byte for byte | test_exports_apply_only_to_the_exact_upload, test_git_am_commit_and_workspace_states |
| Conflicts are never merged | Optimistic versions (409); recipes that no longer match exactly are skipped with the reason | test_bulk_recipe_fixes_issue_queue_and_conflicts |
| Isolation | Workspaces, files, checks, exports and comparisons are workspace-scoped (404 for others); members edit, viewers read; derived copies hidden from lists and AI defaults; `change_set` scans never change issues | test_other_workspaces_see_nothing |
| AI suggestions are bounded and never trusted | Off unless the project's admin switched AI on; server provider, per-run and monthly budgets (honest `BUDGET_EXHAUSTED`); only fenced, masked excerpts of the workspace file and read tools on the upload; candidates are whole-line edits of that file only, checked by exact match, the strict policy and the P05 ladder with trusted engines; only checked candidates can be applied, by a person, recorded as `ai`; usage accounting survives workspace deletion | test_ai_fix_suggestions_on_the_temporal_worker / _lite_profile, test_ai_fix_requests_are_gated_and_applied_only_when_checked, test_ai_candidates.py |
| Workspace pull requests are exact and opt-in | Same publication rules as fix pull requests (admin opt-in, no forks, active installation, same repository); only a current checked workspace; exact-head freshness (`stale_patch`); one commit with repository-scoped write tokens; never merged; Markdown in the body escaped | test_workspace_pull_request_is_one_checked_commit, e2e p06-github |
| Tier 0 type-check executes nothing (P09, ADR 0017) | The platform's pinned TypeScript compiler in a bounded child process with a scrubbed environment; its host only sees the checked folder and the compiler's standard library; project compiler plugins, automatic types, outside `extends`, output paths and emitting are ignored | test_project_code_and_outside_files_are_never_used |
| Supply chain | CodeMirror packages MIT, exact pins plus overrides on releases public ≥ 2 weeks at adoption; loaded only on workspace/compare pages | ADR 0016, pnpm-workspace.yaml |

Residual risk: checks are source-level; an edited project can still fail to compile or behave differently (P09 adds sandboxed compilation). Exported ZIPs and patches contain the customer's code — they are served only to authorized users with `Cache-Control: no-store` and temporary files are removed after sending.

### Architecture rules (P10 slice 2, ADR 0019)

| Control | Implementation | Evidence |
|---|---|---|
| Imported YAML is data | PyYAML `SafeLoader`; anchors and aliases refused before construction (no expansion attacks), non-standard tags fail, one document, 64 KB; strict schema with unknown fields refused and every problem located | test_invalid_rules_say_where_and_why, test_invalid_rules_are_refused_with_reasons |
| Rules never come from uploads | Rules are saved in the portal by members; files in uploads (including any rules file) are never read as rules | ADR 0019 |
| Audit | Append-only versions with author, note, source and hash; stale saves are conflicts (409), not overwrites | test_rules_are_versioned_audited_and_exported |
| A rule edit is never a fix | Per-rule hashes: an absent breach is `VERIFIED_ABSENT` only under the identical rule; a changed rule gives `UNKNOWN`, a removed rule `RULE_OBSOLETE`; only verified absences resolve issues | test_recheck_compares_the_rules_own_hash, test_architecture_rules_on_real_reviews |
| Isolation | Rules, checks and exports are project-scoped (404 for other workspaces); members edit, viewers read | test_members_edit_rules_and_other_workspaces_see_nothing |
| Nothing executed | Evaluation reads the dependency map the platform built from source; no project code or configuration runs | crp_analysis/engines/architecture.py |

### GitHub (P06, ADR 0015)

| Control | Implementation | Evidence |
|---|---|---|
| Least-privilege, revocable access | One operator App; per-operation installation tokens limited to one repository and to read, checks or fix permissions; tokens only in memory (≤ 1 h); a revoked cached token is replaced once and an uninstall is recorded; suspended/uninstalled installations and removed repositories stop reviews | test_tokens_are_scoped_to_one_repository_and_the_needed_permissions, test_revoked_and_suspended_installations, test_partial_scans_outages_and_revoked_access |
| Verified linking | Forged `installation_id` ignored; one-time state (hash, 10 min, user + workspace bound); only installations the GitHub user can access; admins only, never the demo account; user token used once | test_linking_is_verified_and_bound_to_the_admin |
| Authentic, idempotent webhooks | HMAC-SHA256 `X-Hub-Signature-256` compared in constant time before parsing; size limit; delivery ids unique; nothing recorded for unauthenticated calls | test_webhooks_need_a_valid_signature_and_are_idempotent |
| No repository code or Git runs | Captures are GitHub's archive of an exact commit (no clone, hooks, filters or submodules); archive host gets no credentials; symlinks excluded; reconciled with the commit tree so `export-ignore` cannot hide files | test_capture_matches_the_commit_even_when_the_archive_hides_or_alters_files, test_connected_repository_is_reviewed_exactly_as_committed |
| Forks are untrusted | Not reviewed automatically unless a project admin allows it; never targeted by fix pull requests | test_fix_pull_requests_forks_and_stale_patches |
| Publication is opt-in and exact | Off by default per project (admin, audited); one check per commit and one updated comment; Markdown from repositories escaped; fix pull requests only on human request for validated fixes, exact-head freshness (`stale_patch`), never merged | test_publication_posts_one_check_and_keeps_one_comment, test_published_text_escapes_repository_content_and_caps_annotations |
| Workspace isolation | Installations bound to one workspace; connections, reviews and snapshots are project-scoped (other workspaces get 404); engine caches are project-scoped; tokens never span repositories | test_connections_are_admin_managed_and_scoped |

Residual risk: the App's private key on the server can mint tokens for every installation of the App — protect it like a production credential (owner-only file or secret store) and rotate it on suspicion (docs/GITHUB.md runbook). Repository content in reviews, comments and annotations is shown escaped but remains untrusted text.
