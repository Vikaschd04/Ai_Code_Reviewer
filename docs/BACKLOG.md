# Initial implementation backlog

Items are TODO unless a status is shown. Expand into small vertical slices as needed; preserve IDs and verified history. Do not create external tickets unless requested.

| ID | Task | Acceptance anchor |
|---|---|---|
| P00-01 | Inspect workspace/toolchain and create coherent app layout | No user work overwritten; versions recorded — **DONE** (ADR 0003, TOOLCHAIN.md) |
| P00-02 | Web/API/local auth, DB migration and artifact/workflow contracts | Real health/readiness + migration — **DONE** (P00_REPORT) |
| P00-03 | Developer scripts, context utilities and test pipeline | Executed commands documented — **DONE** (INSTALLATION.md, P00_REPORT) |
| P01-01 | Streamed ZIP intake and freeze/manifest | Adversarial archive tests — **DONE** (P01_REPORT) |
| P01-02 | Local CLI capture/upload | Capture stable, input unchanged — **DONE** |
| P01-03 | Inventory/basic Java/JS parser | Honest per-file outcomes — **DONE** |
| P01-04 | Real PMD/ESLint worker adapters | Seeded/clean/crash cases — **DONE** (ADR 0006) |
| P01-05 | Source/progress/coverage/issues UI | End-to-end browser workflow — **DONE** (redesigned UI, E2E) |
| P02-01 | Snapshot graph and bounded queries | Provenance/stale-edge tests — **DONE** (P02_REPORT, ADR 0008) |
| P02-02 | Opengrep/Trivy and normalization | Approved versions/rules, duplicates preserved correctly — **DONE** (ADR 0007) |
| P02-03 | Comparison/export | Incompatible/partial scans do not resolve issues — **DONE** (also issue lifecycle, cache, benchmark) |
| P03-01 | Provider boundary and context builder | Scope/egress/budget tests — **NEXT** (live provider needs user-approved account/policy) |
| P03-02 | OCR evaluation and AI review/Q&A | Anchors and held-out evaluation |
| P04-01 | SAP Commerce pack | Mapping + domain fixtures |
| P04-02 | Salesforce pack | Metadata + permission/limit fixtures |
| P05-01 | Patch author/workbench | Separate copy and constrained diff — **DONE for deterministic recipes** (P05_REPORT, ADR 0014); AI patches wait for the P03 provider |
| P05-02 | Validation/export | Original-defect and regression evidence — **DONE source-level** (integrity/syntax/detector/regression, patch + `crp-fix-export/v1`); tests/build rungs need P05-F1 |
| P06-01 | GitHub connector/webhooks | Scoped auth/idempotency — **DONE** (fake GitHub; live check needs the owner's App, K-P06-01) |
| P06-02 | Incremental scopes/PRs | Merge-base/cache/freshness tests — **DONE** (P06_REPORT, ADR 0015) |
| P08-01 | Change-set core and exports (patch, changed-files ZIP, full ZIP, summary) | Patch applies only to the exact base; changed-files ZIP exact; upload unchanged — **DONE** (P08_REPORT, ADR 0016; plus a `git am` commit) |
| P08-02 | Editor and simplified compare (per file, per change set, two uploads/commits) | Policy flags on save; inert rendering; light/dark/mobile — **DONE** (CodeMirror 6; ignore-whitespace is P08-F2) |
| P08-03 | Bulk recipe fixes, conflicts and change-set re-check | Fixed / still present / new reported correctly — **DONE** (suppressed never fixed; Temporal and lite) |
| P08-04 | AI fix candidates in the workspace | Labelled, policy-gated, validated; budgets honest — **DONE offline** (labelled test model; live quality needs K-P03-01) |
| P08-05 | Change-set pull request (GitHub) and IDE apply guidance | P06 freshness rules — **DONE** (fake GitHub; live with K-P06-01) |
| P09-01 | Tier 0 type-check/parse in the workspace | No project code loaded — **DONE** (TypeScript in workspace checks; ADR 0017) |
| P09-02 | Tier 1 safe compile in a sandbox (Java `-proc:none`, npm `--ignore-scripts`) | Malicious processor/script not executed; egress blocked |
| P09-03 | Tier 2 builds/tests in microVMs, bring-your-own CI, Salesforce/SAP profiles | Containment tests; results bound to hashes |
| P10-01 | Architecture model and structural metrics with views | Hand-computed metric fixtures |
| P10-02 | Intended-architecture rules as code | Violations with lifecycle |
| P10-03 | Smell, performance and scalability catalogs (incl. SAP/Salesforce) | Positive/negative fixtures per row |
| P10-04 | Git-history hotspots and change coupling; runtime evidence import | Not-available honesty; span mapping |
| P10-05 | Recommendations board and grounded AI architect | Cited, labelled, measured |
| P11-01 | Refactoring recipes with what-if simulation | Predicted equals re-analysed metrics |
| P11-02 | AI multi-file plans and migration plans | Bounded, verified, staleness handled |
| P07-01 | Identity/tenant/execution hardening | Isolation/security assessment |
| P07-02 | Scale/recovery/retention/release | Measured limits and runbooks |


## Follow-ups discovered in P00 (non-blocking)

| ID | Task | Acceptance anchor |
|---|---|---|
| P00-F1 | Initialize Git (user decision) and run `/security-review` + `/code-review` on the foundation | Findings triaged or fixed |
| P00-F2 | Verify bootstrap/dev/test on Linux | Commands pass on a named distro; TOOLCHAIN updated |
| P00-F3 | Pinned container images / Compose for PostgreSQL + Temporal (digests) | `make dev` alternative verified |
| P00-F4 | License review for psycopg (LGPL-3.0) before binary/image distribution | Recorded decision |

## Follow-ups discovered in P01 (non-blocking)

| ID | Task | Acceptance anchor |
|---|---|---|
| P01-F1 | Optional browser folder selection with ZIP fallback; admin-registered server mounts by opaque ID | Same server-side checks as ZIP |
| P01-F2 | Scheduled intake-expiry job and content-addressed blob garbage collection | Retention tests |
| P01-F3 | User scope overrides within safety limits (versioned) | Overrides recorded; secrets still excluded |
| P01-F4 | OS-level sandbox for engine processes (container/VM) | Resource/egress limits enforced and tested (before P07) |

## Follow-ups discovered in P02 (non-blocking)

| ID | Task | Acceptance anchor |
|---|---|---|
| P02-F1 | Engine-cache eviction (age/size) and a `crp-dev cache-prune` command | Retention test; hit rates unchanged for recent entries |
| P02-F2 | Scheduled Trivy DB refresh with provenance record (still no DB update during scans) | DB age alert; refresh logged; absent vulns handled as UNKNOWN |
| P02-F3 | Deeper resolution: Maven/Gradle classpath from a sandboxed build, `tsconfig` `extends`/project references, `package.json` `exports`, CommonJS exports, call edges | Resolution precision measured on fixtures; no guessed edges |
| P02-F4 | Engine pins for Linux x86_64/arm64 | Checksums + signatures verified on Linux |
| P02-F5 | Link pre-0003 findings to issues by fingerprint (backfill) or label them in the UI | Old scans show issue status or an explicit note |

## Follow-ups discovered in P05

| ID | Task | Acceptance anchor |
|---|---|---|
| P05-F1 | Isolated runner for the tests/build rungs (container or VM, no network/secrets, CPU/memory/time caps, allow-listed test commands per ecosystem) | Malicious build/test fixtures stay contained; original-defect test fails before and passes after the fix |
| P05-F2 | Bounded AI patches for contextual findings (after K-P03-01) | Same policy and ladder; labelled AI; budget and spend recorded |
| P05-F3 | More recipes (rule by rule) and multi-file fixes | Positive/negative fixtures per recipe; scope list per fix |
| P05-F4 | Whole-project re-check of a validated fix (callers, overloads) | Regression evidence beyond the changed file |

## Follow-ups discovered in P06

| ID | Task | Acceptance anchor |
|---|---|---|
| P06-F1 | Live verification on the owner's GitHub App and test repository (`crp-dev` checklist in docs/validation/P06_REPORT.md) | Every P06 mandatory check repeated against github.com |
| P06-F2 | Re-publish a failed check/comment on demand (today the next review posts again) | Retry button; idempotent update |
| P06-F3 | Similarity-based rename detection for changed-and-moved files (Git `-M`) | Lineage kept for edited renames |
| P06-F4 | GitHub Enterprise Server and GitLab/Bitbucket adapters behind the same source-provider boundary | Adapter contract tests |
| P06-F5 | Fix pull requests that move a stale fix automatically (rebase + revalidate in one step) | Stale fix → new validated PR without manual steps |

## Follow-ups discovered in P08

| ID | Task | Acceptance anchor |
|---|---|---|
| P08-F1 | Move a workspace to a newer upload (three-way: apply each file's change where the upload's lines still match; conflicts listed per file) | Clean moves keep provenance; conflicts never merged silently |
| P08-F2 | Ignore-whitespace comparison and a per-change-set unified view (all files in one scroll) | Same counts as the per-file view; whitespace-only changes hidden on request |
| P08-F3 | Edit non-UTF-8 text files (declared encoding, e.g. Latin-1 Java) without changing other bytes | Round-trip byte equality for untouched lines |
| P08-F4 | Content-addressed blob garbage collection for workspace revisions (with P01-F2) | Unreferenced revisions removed; referenced ones kept |
| P08-F5 | Workspace archive and multi-user presence (who is editing) | Archived workspaces read-only; edits by two users conflict visibly |
| P08-F6 | Share the original-copy engine runs between AI candidates (the ladder runs each engine on the unchanged file once per candidate today) | Same results; fewer engine runs per request |

## Follow-ups discovered in P09

| ID | Task | Acceptance anchor |
|---|---|---|
| P09-F1 | Run the Tier 0 type-check in the P05 single-fix ladder (the "build" step becomes "type-checked" for TypeScript fixes) | A fix that breaks types fails the ladder; Java still "not compiled" |
| P09-F2 | Show customer CI results (check runs and statuses) on fix and workspace pull requests | Read through the P06 client; fake and live GitHub |
| P09-F3 | Several TypeScript projects in one upload (project references, nested tsconfig files) | Each file checked with its nearest tsconfig |

