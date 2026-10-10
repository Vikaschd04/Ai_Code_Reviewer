# Changelog

## Unreleased — deployment and configuration checks (10 October 2026)

- **Deployment & configuration** (new check): Kubernetes workloads that can run as a single
  instance, containers that serve traffic without a readiness probe, and rollouts that stop
  everything (Recreate); Spring Boot settings that expose sensitive Actuator endpoints, show health
  details to everyone, or let Hibernate change (or drop) the database schema at startup. Overlays,
  kustomizations and autoscalers are taken into account; development profiles and test resources
  are ignored.
- **Containers and Kubernetes security** (Trivy, offline): root and privileged containers,
  writable file systems, missing CPU and memory requests and limits, and more, from the checks
  built into the pinned Trivy. Terraform is not scanned by Trivy, because its scanner downloads
  remote modules named in the code.
- **NFR questionnaire and Insights** now cite what the configuration shows, with file and line:
  replicas, autoscaling, disruption budgets, probes, graceful shutdown, timeouts, pool sizes,
  backups and multi-zone databases. New recommendations: deployments that can go down, automatic
  schema changes, containers without requests and limits, insecure container settings.
- Helm templates and charts that cannot be rendered are reported as not checked, never as clean.

## Unreleased — a simpler refactorX, with insights and an advisor (10 October 2026)

- **Simpler project page:** six tabs instead of eleven — Overview, Issues, Insights, Fixes,
  Uploads, Settings. Old links still work.
- **Overview** shows the latest review plus "Health and next steps": six areas (Security,
  Reliability, Performance and scalability, Architecture and maintainability, Operations and
  monitoring, Experience and portability) and the three most important things to do.
- **Insights** brings recommendations, the NFR questionnaire and the architecture together. Each
  recommendation says what was found, why it matters and how to improve it, with the issues
  behind it. **Start fixing** opens a fix workspace with just those issues.
- **Improvement plan (AI, optional):** when AI is on for the project, the advisor orders the
  recommendations into a plan. Every step must cite the tools' evidence; steps without it, or
  with numbers not in the evidence, are removed and listed.
- **One way to fix:** the finding page offers "Fix this" (a fix workspace). AI settings, the GitHub
  connection and deleting a project are under Settings; GitHub reviews are under Uploads.
- **Removed from view:** the sidebar's "Coming soon" list and AI "review selected files".
  Earlier single fixes stay available, collapsed under Fixes.

## Unreleased — P12 NFR readiness (10 October 2026)

- New project tab **NFR readiness** (ADR 0021): the non-functional requirements questionnaire (9 aspects, 23 questions, mapped to ISO/IEC 25010:2023) answered for your project.
- Each question shows what the code shows (for example "Health and readiness endpoints — pom.xml line 9", a CI pipeline, a Dockerfile, an OpenAPI file), what needs work (open issues that affect it), and what only your team can tell.
- Record your targets (availability, response time, RTO, RPO, users, growth, …), regulations, platforms and answers; mark questions that do not apply, with a reason. Every save is a new version with your name.
- Honest statuses: Needs work, Needs your input, Evidence found, Answered by your team, Not applicable, Not checked yet. Nothing found is never shown as met, and compliance is never certified.
- Download the questionnaire as a spreadsheet (CSV) or a report (Markdown).

## Unreleased — P10 architecture smells (9 October 2026)

- **Architecture smells** on every review (ADR 0020), reported as issues and labelled *potential* (found in the code structure):
  - **Dependency cycle** between parts, with the cheapest way to break it;
  - **Unstable dependency**: a part that relies on parts which change more easily than itself;
  - **Hub-like part**: used by many parts and using many parts.
- One issue per part (not per file), at the part's first file with the evidence; the issue follows the part if that file changes, so a moved anchor is never shown as "fixed".
- Generated code (for example SAP Commerce `gensrc`) is left out of the architecture model like test code, and counted.
- Salesforce Lightning Web Components: `c/<name>` imports are now followed, so cycles between components are found.
- Measured: precision and recall 1.0 for each smell on a labelled set of 10 synthetic cases (Java, TypeScript, SAP Commerce, Salesforce; `crp-dev arch-eval`); 50,000 files in about 6 seconds. Real-project accuracy is not yet measured.

## Unreleased — P10 architecture rules (9 October 2026)

- **Architecture rules** on the project's Architecture tab (ADR 0019): describe how your code should be organised — layers from top to bottom, dependencies that are never allowed, and exceptions with a reason and an expiry date. Start from an example, **check the rules on the latest upload** before saving, and see which parts fall in which layer.
- **Every review checks the rules.** Each breach is an issue at the exact import, with the rule's severity and reason, tracked like any other issue (and included in JSON and SARIF exports). Fixing the code resolves it; changing or removing the rule never counts as a fix ("rule changed" / "rule retired").
- Rules are versioned with author and note, can be downloaded as YAML and imported into another project. YAML is read as plain data (no anchors, aliases or custom tags; 64 KB at most).
- The Issues tab can be filtered by check (for example "Architecture rules"), and its counts follow the filter.
- New dependency: PyYAML 6.0.3 (MIT).

## Unreleased — P10 structure health (9 October 2026)

- **Structure health** on the Architecture tab (ADR 0018): the parts of your code (Java packages and folders, test code left out), which parts depend on which, and R. C. Martin's measures — who uses a part (Ca), what it uses (Ce), instability, abstractness and distance from the balance line. Anything not measurable shows "—", never zero.
- **Cycles** between parts are found, each with the cheapest set of imports to remove to break it (exact for small cycles, a proven heuristic for large tangles). Parts that are "hard to change" (concrete and depended on by at least 3 files) or "unused abstractions" are flagged with a plain reason. A dependency matrix of the most connected parts is in the details.
- Fast enough for large projects: 50,000 files with 400,000 dependencies in about 7 seconds. API `GET /v1/snapshots/{id}/architecture`.
- The architecture map now records abstract classes and interfaces (graph extractor v2); uploads mapped before show abstractness as not measurable until reviewed again.

## Unreleased — P09 Tier 0 type-check (9 October 2026)

- **Check my changes** now type-checks TypeScript (ADR 0017). The platform's own pinned TypeScript compiler (5.9.3) reads the upload's and the changed copy's TypeScript files and shows "No new type errors" or "N new type errors" (each with file and line), plus type errors your changes fixed. Moved lines do not count as new.
- Nothing from your project runs: no compiler plugins, no project-installed TypeScript, no settings that reach outside the upload, nothing written. Imports of packages that are not installed are counted and shown, not reported as your errors. Results are cached by exact content.
- The check now says exactly what was verified: "TypeScript is type-checked; nothing is built, run or tested, and Java is not compiled." Java compiling, builds and tests need isolated computing (Tiers 1–2), which is waiting for an owner decision.
- Settings `CRP_TYPECHECK_ENABLED`, `CRP_TYPECHECK_TIMEOUT_SECONDS` (300), `CRP_TYPECHECK_MAX_FILES` (3,000).

## Unreleased — P08 workspace pull requests (9 October 2026)

- For code reviewed from a connected GitHub repository, a checked fix workspace becomes **one pull request** (Changes tab → **Open pull request**). It is one commit on the reviewed commit with every change (added, changed and deleted files; executable bits kept). The body lists each file with how it was changed (by hand, automatic fix, AI suggestion), notes such as hiding markers, the check results and "not compiled, built or tested". Nothing is merged.
- It follows the fix pull request rules: a project admin must allow pull requests, never for forks, only if the branch has not moved since the review (otherwise "stale"), and only for changes that were checked as they are now. Asking again returns the same pull request; new changes need a new check.
- A configuration edit in a workspace (for example a vulnerable dependency in `pom.xml`) is now tested to surface the new dependency findings in the re-check.
- API: `POST /v1/change-sets/{id}/pull-request`, workspace `pull_request` status. Migration `0011`. The workspace status "Downloaded" is now "Delivered" (downloaded or sent as a pull request).

## Unreleased — P08 AI fix suggestions (9 October 2026)

- **Ask AI** on an issue in a fix workspace (ADR 0016). It works only when a project admin switched AI on and the server has a provider with budget left. The AI sees the file as it is in the workspace and suggests up to three fixes.
- Every suggestion is checked before anyone can use it, exactly like an automatic fix: its lines must match the file, the change policy must allow it (no hiding markers, no skipped or weakened tests, no configuration changes, one file), and on a copy the file must still parse, the check must stop reporting the problem and nothing new may appear. The suggestion shows its diff, the checks, what to watch and the label "AI suggestion: review it before applying. Not compiled, built or tested."
- Only checked suggestions can be applied, once, by a person; they are recorded as AI changes in the workspace history and downloads. A suggestion whose lines changed since is stale and never merged. Runs stop honestly at their budget; usage is accounted even if the workspace is deleted.
- API: `/v1/change-sets/{id}/ai-fixes` (request, list) and `/ai-fixes/{run}/apply`; AI runs of kind `fix`; workspace `ai` status. Migration `0010`; setting `CRP_AI_FIX_MAX_CANDIDATES` (3). The labelled test model now proposes fixes (and a deliberately hiding one) so tests show both outcomes.

## Unreleased — P08 fix workspaces (8 October 2026)

- Fix workspaces (ADR 0016): fix many issues on top of one upload — the upload is never changed. Each change keeps where it came from (by hand, automatic fix, undone, downloaded), and one content digest binds checks and downloads to exactly these changes.
- Fix in bulk: select issues (or "all like this" for a rule) and apply the known safe fixes; a fix whose lines were already changed by hand is skipped with the reason instead of guessed. Fix by hand in a code editor (CodeMirror 6, MIT) with line numbers, search, Ctrl/⌘+S, Java/Apex, JavaScript/TypeScript, XML and JSON colouring; add and delete files. Binary, oversized, not-stored and non-UTF-8 files are not editable; CRLF line endings and Unicode are kept.
- Honest flags: markers that hide problems, skipped/focused or weakened tests and configuration changes are flagged on save; recipes refuse them. The fix policy now also catches `it.skip`, `xit`, `describe.only`, `@Disabled`, `@Ignore` and similar.
- Check my changes: a copy of the upload with the changes is reviewed by the same checks (unchanged files reuse earlier results) and compared with the original review — Fixed, Still present, **Hidden, not fixed** (never counted as fixed), Not rechecked and New problems. Says plainly that nothing is compiled, built or tested. Temporal workflow or in-process on the lite profile.
- Compare: every changed file side by side or inline (unchanged lines collapsed, word changes highlighted), and any two uploads of a project (added, changed, removed, moved).
- Downloads: a patch for `git apply`, one commit for `git am`, a ZIP of only the changed files (with a manifest listing deleted files), the full project ZIP (unchanged files byte for byte; a list of files that were never stored) and a summary (JSON `crp-change-set-export/v1` with a schema, and Markdown). Each names the exact upload and hashes.
- API: `/v1/projects/{id}/change-sets`, `/v1/change-sets/{id}` (file, file/delete, file/revert, fixes, issues, checks, export), `/v1/change-set-checks/{id}`, `/v1/snapshots/{id}/compare` and `/compare/file`. Migration `0009`; setting `CRP_CHANGE_SET_MAX_FILES` (500). Copies scanned by checks are hidden from upload lists, counts, Reviews and AI defaults and never change issues.
- UI: project "Fix workspaces" tab, workspace page (Issues, Changes, Edit), "Open in workspace" on findings, "Compare" on uploads. The editor loads only on these pages.

## Unreleased — P06 GitHub reviews (1 October 2026)

- GitHub through your own GitHub App (ADR 0015, docs/GITHUB.md): verified linking (the admin confirms on GitHub; forged installation ids are ignored), repositories per installation, one project per repository. Tokens are limited to one repository and to read, checks or fix permissions, and are never stored.
- Reviews of the default branch on every push and of pull requests (opened, updated, base changed), compared with the previous review or the merge base. Late, duplicate and out-of-order events converge on the newest commit; older reviews are replaced; closed pull requests stop. Forks only when a project admin allows it (members can start them by hand).
- Captures are GitHub's archive of the exact commit, checked against the commit's file list: files hidden with `export-ignore` or changed in the archive are fetched as committed; symlinks, submodules and Git LFS files are listed as not included. No Git command or repository script runs.
- Incremental by design: unchanged files reuse their results; dependency, framework and architecture checks always cover the whole snapshot; scheduled full re-checks every 7 days (configurable). Pull request scans never change the project's issues; renamed files keep their issues on the default branch and are not reported as new in pull requests.
- Optional publication per project (off by default): one `refactorX` check per commit with annotations on new problems and one summary comment per pull request, updated in place; the check fails only at a threshold the project sets. Fix pull requests from validated fixes on request, only when the branch has not moved (otherwise "stale"); never merged.
- API: `/v1/github/*`, `/v1/workspaces/{id}/github/*`, `/v1/projects/{id}/git-connection`, `/v1/projects/{id}/code-reviews`, `/v1/code-reviews/{id}`, `POST /v1/fix-proposals/{id}/pull-request`; Git details on snapshots. Migration `0008`. Dependency `cryptography` 50.0.2.
- UI: Administration → GitHub, project GitHub tab, review page, "From GitHub" on uploads, "Open pull request" on fixes. The "Coming soon" list is gone.
- Fixes found while building: a worker bookkeeping write no longer bumps the connection's settings version; a revoked cached token is replaced so uninstalls are recognised.

## Unreleased — P05 validated fixes (1 October 2026)

- Fixes for findings (ADR 0014): deterministic, approved recipes — ESLint's own safe fixes for `prefer-const`, `no-var` and `eqeqeq` (captured at scan time), `"literal".equals(value)` for Java string comparison with `==`, and moving retired Salesforce metadata API versions to the project's `sourceApiVersion`. Each fix explains what could behave differently.
- Fixes are bound to one upload, the file's base hash and the patch hash; the upload is never changed. Reviewers can edit a fix (suppressions, weakened tests, configuration changes and oversized changes are refused), reject it, and move it to a newer upload when the lines still match exactly.
- Checks on a copy: the patch applies to this upload, the file still parses, the check that found the problem no longer reports it and nothing new appears. Project tests and builds are shown as "not run" (they would execute uploaded code; SAP builds and Salesforce deployments need licensed or authorized environments). 5 checks per fix, one at a time, cancellable; Temporal workflow or in-process on the lite profile.
- Downloads: a Git-compatible patch with a header naming the upload and hashes, and a JSON change summary (`crp-fix-export/v1` with its own schema).
- API: `fix-options`, `fix-proposals` (create, list, get, edits, reject, validations, patch, summary, rebase) and `fix-validations/{id}/cancel`. Migration `0007`. The `fix_workbench` capability is available.
- UI: "Fix" card on findings, fix page (diff, what to watch, editor, checks, labels, downloads, technical details) and a project "Fixes" tab; "Fix workbench" left the "Coming soon" list.

## Unreleased — P04 SAP Commerce and Salesforce packs (30 September 2026)

- Framework packs (experimental, ADR 0013): SAP Commerce (2105–2211) and Salesforce (API 31.0+) detection with version evidence, capability coverage and configuration-driven architecture links (extensions, Spring beans and injection, item types, interceptors, ImpEx; Salesforce packages, Apex, triggers, LWC, objects, Flows, permission sets, custom metadata). Metadata is read with a secure XML reader that refuses DTDs and entities.
- New checks: 8 owned Opengrep rules for SAP Commerce/Java hygiene, 11 PMD Apex rules on the same pinned PMD (engine `pmd-apex`), and configuration checks for extension dependency cycles and retired Salesforce API versions (engine `frameworks`). Catalog `crp-rules-v3`; every rule cites a source.
- UI: "Platform support" card on reviews and the Architecture tab; platform checks appear only when they apply; framework components searchable in the architecture map.
- LWC JavaScript is parsed with the TypeScript parser so decorators no longer make those files fail the JavaScript check.
- Migration `0006` (graph node kind `component`). Synthetic fixtures `sap-commerce-mixed` and `salesforce-mixed`.

## Unreleased — P03 AI review (30 September 2026)

- AI results can be downloaded per run (JSON with its own schema, SARIF with AI findings only; rejected suggestions never appear as SARIF results), and a finding's page lists its earlier AI second opinions. AI findings deliberately stay out of tracked issues and scan exports: issues need deterministic re-checks.
- AI review, off until configured: Anthropic or any OpenAI-compatible provider through `CRP_AI_*` settings (key only on the server, https endpoints), with per-run limits, monthly token/cost caps and owner-set prices (costs "unknown" without them). ADR 0012.
- Per-project sharing switch, off by default, admin-only with explicit confirmation, audited.
- Ask questions about a project's code, review up to five files, or get a second opinion on a finding. The AI reads masked excerpts through five read-only tools; every cited line is checked against the upload ("Checked against your code" / "Not verified" / discarded). Runs are durable, cancellable and never retried automatically; usage and cost are recorded per call.
- API: `GET /v1/ai/status`, project `ai-policy` (GET/PUT), project `ai-runs` (POST/GET), `GET /v1/ai-runs/{id}`, `POST /v1/ai-runs/{id}/cancel`; `project_id` on finding detail; capability state `not_configured`. Migration `0005_ai_review`.
- UI: project "AI review" tab, AI run page, "AI second opinion" on findings; the sidebar's data note reflects whether AI is set up.
- Evaluation: labelled set `fixtures/ai-eval/` and `crp-dev ai-eval [--live]`; `crp-dev ocr-eval` isolated evaluation of Alibaba open-code-review 1.12.11 (not adopted: see docs/validation/P03_OCR_EVALUATION.md). `make test-e2e` starts a labelled loopback fake model.
- Live model evaluation is blocked until the owner configures a provider key (docs/DEPLOYMENT.md "AI review").

## Unreleased — Delete projects (30 September 2026)

- `DELETE /v1/projects/{id}` (creator or workspace admin): removes every project row (cascade) and its artifacts, then reclaims content blobs no other snapshot uses; refused with 409 while a review or upload check runs.
- UI: "Delete project" on the project page and a delete action in the projects list, with a confirmation dialog that requires typing the project name.
- Artifact stores gained `list_keys(prefix)` (filesystem and PostgreSQL). The CI smoke test deletes the demo sample project on the Render-free (lite) configuration.

## Unreleased — Futuristic backdrop and review fixes (30 September 2026)

- Futuristic backdrop on every page: drifting aurora glows, a fading grid, frosted chrome and cards, a light beam under the top bar, glowing primary buttons; sign-in adds an orbit glow and a gradient-edged card (motion off with reduced-motion preferences).
- Fix: the "Files checked" tab could scroll thousands of pixels past its content (screen-reader text from rows inside the scrolling table escaped its frame); tables and file locations now contain it. Regression check in the UI tour.
- "What was checked": every check card has the same structure (name and status, description, optional error, then the file meter pinned to the bottom with "x of y files checked"); checks without matching files keep an empty meter and say so. Alignment is asserted in the UI tour.

## Unreleased — Minimal design system (30 September 2026)

- UI redesign on a strict token system: Inter/JetBrains Mono (self-hosted, OFL-1.1), one type scale, 4 px spacing grid, fixed control heights and radii, neutral palette with one accent in light and dark themes; glass, glows and gradient buttons removed.
- Layout containers own spacing; 83 inline style overrides removed (only data-driven values remain); new `SectionHeader`, `StatusIcon` and compact review "Progress" card; underlined tabs; consistent tables, forms, badges, alerts and disclosures.
- Responsive refinements: centred content width, two-column stat tiles and progress steps on phones, controls stay inline, numbers never wrap, file paths shown name-first with an accessible full path, proper language names.

## Unreleased — refactorX: demo account, sample project and reviewer-first UI (30 September 2026)

- Product renamed **refactorX** in every user-facing place (UI, brand mark and favicon, page title, OpenAPI title, SARIF tool name, docs); internal `crp` identifiers unchanged (ADR 0011).
- Demo account: `GET /v1/auth/options` (public), `POST /v1/auth/demo-session`, `is_demo` on `/v1/auth/me`; a separate member-only demo workspace with project and hourly scan quotas (`CRP_DEMO_ENABLED`, `CRP_DEMO_MAX_PROJECTS`, `CRP_DEMO_MAX_SCANS_PER_HOUR`); enabled in `render.yaml`, Codespaces and local development.
- Sample project: `POST /v1/projects/sample` creates "Sample: Online store" (Java + TypeScript with deliberate security flaws, bugs, vulnerable log4j/lodash and a generated fake token) through the normal intake path; one-click "Run the sample review" in the UI.
- Reviewer-first UI redesign: new sign-in page, overview with onboarding, plain-language reviews/uploads/findings/issues/changes/architecture, merged duplicate findings, file-name-first locations, "Technical details" disclosures for provenance, System status for administrators only, compact mobile layout; UI tour E2E with light/dark/mobile screenshots.

## Unreleased — Free deployment on Render (30 September 2026)

- Lite profile `CRP_PROFILE=lite` (ADR 0010) for 512 MB free instances: one process (`crp_devtools.lite_server`) with `InlineWorkflowGateway` running the same intake/scan activities without a Temporal server, resuming unfinished intakes and scans after a restart; analyzers one at a time with smaller heaps (`CRP_ESLINT_HEAP_MB`, `CRP_OPENGREP_JOBS`); uploads up to 25 MB.
- `CRP_ARTIFACT_BACKEND=postgres`: artifacts stored in PostgreSQL (migration `0004_artifact_objects`) for hosts without a persistent disk.
- The offline Trivy vulnerability DB is baked into the image; hosted starts use the newest copy and the daily refresh swaps a symlink atomically (`crp_devtools/trivy_db.py`).
- `render.yaml` is now the free Blueprint (free web service + free PostgreSQL 18); the paid Blueprint moved to `deploy/render-standard.yaml`. Railway fallback removed.
- CI reruns the container smoke test in the lite profile with 512 MB, no swap and 0.1 CPU (`deploy/docker-compose.lite.yml`) and fails on restarts or out-of-memory kills; smoke tests now require Trivy to succeed.
- `deploy/codespace.sh reset` deletes all data; `CRP_PROFILE=lite` selects the free configuration.

## Unreleased — Free deployment on GitHub Codespaces (29 September 2026)

- `.devcontainer/devcontainer.json` + `deploy/codespace.sh` + `deploy/docker-compose.yml`: a codespace builds and starts the complete application with PostgreSQL automatically and prints its private https URL; secrets are generated into `.local/codespace/`.
- CI's container job now starts the app through the same script and Compose file (simulated codespace) before the smoke test.
- docs/DEPLOYMENT.md compares free/paid options with current limits (Render free, Railway, Hugging Face, Codespaces).

## Unreleased — Complete application on Render (27 September 2026)

- The container serves the built web UI on the API's origin (`CRP_WEB_STATIC_DIR`, strict CSP, immutable hashed assets); hosted mode defaults the web origin to the service's own address, so the Render Blueprint needs no input.
- `render.yaml` now deploys everything (service `ai-code-reviewer` + PostgreSQL); `vercel.json` removed; Dockerfile without BuildKit heredocs (`deploy/fetch_temporal.py`).
- CI container smoke test also checks the served UI and its CSP.

## Unreleased — Git, CI and single-user hosted deployment (27 September 2026)

- Repository on GitHub (`Vikaschd04/Ai_Code_Reviewer`); GitHub Actions CI (lint, types, contracts, unit tests, web build, Linux image build + container smoke test), actions pinned to commit SHAs.
- Hosted tier `CRP_ENVIRONMENT=hosted`: Host allowlist, https-only origins (+ preview regex), Secure cookies, HSTS, token ≥ 32 characters; platform database URLs normalized.
- Direct archive uploads with 15-minute single-intake tickets (`POST /v1/intakes/{id}/upload-ticket`); the web UI always uploads via tickets.
- `Dockerfile` (digest-pinned bases, SHA-256/cosign-verified Linux engines), `crp-dev hosted` single-container entrypoint (Temporal on SQLite, migrations, daily offline Trivy DB refresh, supervised API/worker, unprivileged user), `render.yaml` Blueprint, `vercel.json` (proxy + CSP), docs/DEPLOYMENT.md, ADR 0009.
- ESLint runner: `typescript` is now a runtime dependency (required by typescript-eslint).

## Unreleased — Phase 2 persistent mappings and broader analysis (27 September 2026)

- Opengrep 1.30.0 with 10 platform-owned rules and Trivy 0.69.3 (offline vulnerabilities + secrets), SHA-256 and Sigstore verified; repository tool config inert; secret values never stored (ADR 0007).
- Migration `0003`: lineless dependency/file anchors, correlation keys, issues and audit events, graph builds/nodes/edges, per-file engine cache, cache mode and lifecycle flags.
- Cross-engine correlation ("also reported by") without collapsing distinct occurrences; catalog `crp-rules-v2`.
- Durable issues with strict recheck states, triage with optimistic versions, exceptions with expiry; scan comparison; JSON (`crp-scan-export/v1`) and SARIF 2.1.0 exports validated against their schemas (ADR 0008).
- Snapshot graph: Tree-sitter relation extraction, deterministic resolution with classifications and reasons, superseding builds (failed rebuilds hide older links), bounded summary/search/neighborhood/impact APIs.
- Per-file engine cache keyed by content + engine/rule/config/normalization scope; `cache_mode=refresh` full rescans.
- UI: Architecture, Issues and Compare views, triage panel, dependency details, cache and DB-age indicators, JSON/SARIF downloads.
- `make benchmark`; `make doctor` checks Opengrep/Trivy and DB freshness.

## Unreleased — Phase 1 source intake and baseline analysis (26 September 2026)

- ZIP intake: streamed bounded upload, validation before storage (traversal, symlinks, collisions, encryption, bombs, CRC), idempotent finalize, frozen content-addressed snapshots, manifest with per-entry dispositions; migration `0002`.
- `crp-runner capture`: local folder capture with server-published scope policy, local exclusion of secrets/VCS/vendor/build output, change detection, deterministic archive, disclosure + confirmation, server-verified manifest.
- Inventory with version confidence; Tree-sitter Java/JS/TS structure with parse status.
- Real PMD 7.27.0 and ESLint 10.11.0 adapters with trusted rulesets, neutralised in-source suppressions, bounded execution, cancellation; per-file coverage; fingerprinted findings with static guidance (ADR 0006).
- Scans with idempotency keys, SSE progress, cancellation, limitations; findings/coverage/finding-detail APIs with masked source excerpts.
- Redesigned web UI (dark-first design system with light theme, dashboard, projects, upload, scope review, live scan pipeline, findings, source evidence, coverage).
- `make engines`; doctor reports analyzer availability.

## Unreleased — Phase 0 foundation (26 September 2026)

- Added uv (Python 3.14) and pnpm workspaces: `crp_core`, `crp_api`, `crp_worker`, `crp_devtools`, `crp_runner`, `@crp/web`, `@crp/contracts` (ADR 0003).
- PostgreSQL 18 schema `0001` for workspaces, users, memberships, projects, sources, snapshots and scans with scope-compatible composite foreign keys.
- Loopback-only local-token authentication with HttpOnly browser sessions, Origin checks, throttling and an identity-provider boundary (ADR 0004).
- Real readiness checks (database/schema, Temporal, worker pollers, artifact store) and a diagnostic Temporal workflow.
- Filesystem artifact store with traversal/symlink containment, outside the repository.
- React UI: sign-in, readiness, workflow diagnostic, projects, capability-driven navigation.
- Generated OpenAPI + TypeScript contract with drift checks.
- `make bootstrap|dev|doctor|migrate|seed-fixtures|check|test|test-e2e|package|context-map|context-pack`; native PostgreSQL/Temporal and real ephemeral test infrastructure (ADR 0005).
- No source intake, analysis engines or findings yet (Phase 1).

## Specification kit

- Defined upload-first/local-folder intake and phased code intelligence platform.
- Added master/phase prompts, shared agent instructions and documentation seeds.
