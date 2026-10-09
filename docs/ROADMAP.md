# Phase roadmap

Timing is a planning reference for a dedicated team, not a coding-session promise. This
upload-first sequence supersedes the original research's GitHub-first intake assumption; retain its
engine, evidence and security recommendations.

Updated 8 October 2026 with the owner's two new requirements:
- fixing issues inside the portal (manual and AI), compiling, comparing and exporting;
- architecture intelligence for efficiency, scalability and performance.

The market study behind phases P08–P11 is
[research/MARKET_ANALYSIS_2026.md](research/MARKET_ANALYSIS_2026.md); the architecture
specification is [ARCHITECTURE_INTELLIGENCE.md](ARCHITECTURE_INTELLIGENCE.md).

## Owner requirements and where they are delivered

Every owner requirement, the phase that delivers it, and how it is verified. "Secured" always means:
- changes are made on copies; the original upload or commit is never modified;
- every change carries its provenance (recipe, manual, AI);
- silencing a check or weakening a test is flagged, never counted as fixed;
- uploaded code runs only inside an isolated sandbox (P09);
- AI is bounded, policy-gated, labelled and checked against the code;
- results are bound to exact content hashes.

| # | Requirement (owner) | Delivered in | Verified by |
|---|---|---|---|
| R1 | Fix issues in the portal **by hand** | P08 slice 2 (editor) | Edits saved to a change set; P05 policy flags; re-check shows fixed, still present or new |
| R2 | Fix issues with **AI** | P08 slice 4 (AI candidates); P03 provider | Candidates labelled AI, policy-gated, validated like P05 fixes; live quality needs the owner's key |
| R3 | Fix **one or many** issues (bulk, all occurrences of a rule) | P08 slice 3 | Conflicts detected; per-issue outcome after re-check |
| R4 | **Export a patch** for the current project or repository | P08 slice 1 | Multi-file patch applies with `git apply` only on the exact base |
| R5 | Export **only the changed files** for an IDE or repository | P08 slice 1 | ZIP holds exactly the changed files and paths, plus a manifest (deleted files listed) |
| R6 | **Compile** in the portal where possible | P09 (Tier 0 now, Tiers 1–2 with paid sandboxes) | No project code runs outside a sandbox; malicious processors and scripts are not run |
| R7 | **Compare** old and new versions, simplified Git-like view | P08 slice 2 | Side-by-side and inline diff per file, per change set and between two uploads or commits |
| R8 | **Secured refactoring** of code | P05, P08, P09, P11 | The rules above, tested in every phase |
| R9 | Understand the **whole architecture** | P10 slices 1–4 | Metrics checked against hand-computed fixtures; evidence classes |
| R10 | Suggestions for **efficiency, scalability, performance** | P10 slices 3–7 | Catalogs with fixtures; "potential" versus runtime-confirmed; measured precision |
| R11 | **Production-grade fixes** and help applying them | P11 (plus P08 and P09) | Smell gone, rules pass, metrics moved, compile and tests in the sandbox |
| R12 | **World-class quality, efficient results** | All phases | Measured accuracy and performance in every report; no unmeasured claims |

## Phases, status and order

| Order | Phase | Target | Depends on | Status (see PHASE_STATUS.md) |
|---|---|---|---|---|
| 1 | P00 Foundation | Web/API/DB/workflow foundation and docs | Specification | COMPLETE |
| 2 | P01 Source and baseline | ZIP and folder capture, inventory, PMD/ESLint, dashboard | P00 | COMPLETE |
| 3 | P02 Graph and analyzers | Snapshot graph, Opengrep/Trivy, lifecycle, exports | P01 | COMPLETE |
| 4 | P03 Agentic analysis | Bounded AI review and usage controls | P02 | BLOCKED (owner's AI key) |
| 5 | P04 Frameworks | SAP Commerce and Salesforce packs | P02 | COMPLETE (experimental) |
| 6 | P05 Validated fixes | Deterministic fixes, validation ladder, patch | P03–P04 | IN_PROGRESS (tests/build need P09; AI patches need the key) |
| 7 | P06 GitHub and incremental | GitHub App, branch and PR reviews, publication | P02, P05 | BLOCKED (owner's GitHub App for the live check) |
| **8 (current development)** | **P08 Fix workspace** | Change sets, manual and AI fixes in bulk, re-check, simplified compare, export (patch, changed files, full ZIP, PR) | P05; P03 for AI; P06 for PRs | BLOCKED (all slices done; live AI quality needs the owner's key) |
| **9** | **P09 Isolated build** | Tiered compile and build in the portal; completes P05 tests and build steps | P05, P08; paid compute (owner) | IN_PROGRESS (Tier 0 done; Tiers 1–2 need compute) |
| **10 (next phase focus)** | **P10 Architecture intelligence** | Architecture model, metrics, intended-architecture rules, smell, performance and scalability catalogs, Git-history hotspots, runtime import, recommendations, grounded AI architect | P02, P04, P06; P03 for AI | IN_PROGRESS (slices 1–3: metrics, cycles, Structure health; architecture rules with breaches as issues; structural smells) |
| **11** | **P11 Architecture remediation** | Deterministic refactorings, what-if simulation, AI multi-file plans, verified migrations | P08, P09, P10 | NOT_STARTED |
| 12 | P07 Production hardening | SSO/tenancy, isolation qualification, retention, scale, release | Applicable earlier gates | NOT_STARTED |

Phase ids are stable identifiers; the order column is the plan. P07 tasks needed for paying
external customers (identity, tenant isolation, encryption, backups) can start in parallel with
P10 once infrastructure is chosen. P07 qualifies the release; it does not introduce basic
protection for the first time.

## Slices (each one is shippable and tested)

### P08 Fix workspace (current development)

1. **Change-set core:**
   - model and migration;
   - derived snapshot (base + edits);
   - export of a multi-file patch, a ZIP of changed files only, a full patched ZIP and a summary;
   - original upload immutability tests.
2. **Edit and compare:**
   - CodeMirror 6 editor with the P05 policy run on save (flags, never silent);
   - simplified diff per file and per change set;
   - comparison of any two uploads or commits.
3. **Fix in bulk:**
   - select issues;
   - apply recipes (including "all occurrences");
   - conflict detection;
   - re-check of the change set (fixed, still present, new).
4. **AI candidates:** several labelled candidates per issue through the P03 policy and P05
   validation. Tests use the fake model; live quality needs the owner's key.
5. **Delivery:** one pull request per change set for GitHub projects (P06); IDE apply
   instructions.

### P09 Isolated build

1. **Tier 0 (no execution):** TypeScript type-check and Java parse results in the workspace.
2. **Owner infrastructure decision** (managed microVM sandboxes or a self-hosted Firecracker or
   gVisor node, package proxy).
3. **Tier 1 safe compile** (Java `-proc:none`, npm `--ignore-scripts`), results bound to hashes.
4. **Tier 2:**
   - full build and tests in microVMs with allow-listed commands;
   - bring-your-own CI through pull requests;
   - Salesforce scratch-org profile;
   - SAP customer runner.

### P10 Architecture intelligence (next phase focus)

1. Architecture model and structural metrics (coupling, instability, distance, cycles, size,
   data sharing) with views (C4-style, DSM, cycles).
2. Intended-architecture rules as code, with violations as lifecycle-tracked findings.
3. Structural smell catalog, then performance (potential), then scalability and resilience,
   including SAP Commerce and Salesforce rows.
4. Behavioural evidence from Git history: hotspots, change coupling, knowledge concentration.
5. Optional runtime evidence import (OpenTelemetry, APM) to confirm performance signals.
6. Recommendations board (priority = effect × hotspot weight ÷ effort) and exports.
7. Grounded AI architect review and draft ADRs (needs the owner's key for live quality).
8. Evaluation set and measured precision and recall per smell.

### P11 Architecture remediation

1. Deterministic refactoring recipes into change sets, with what-if simulation.
2. AI multi-file change plans (bounded, labelled).
3. Verification: smell gone, rules pass, metrics moved, compile and tests (P09).
4. Ordered migration plans with progress across reviews.

## Owner decisions and inputs

| Needed for | Decision or input |
|---|---|
| P03 live, P08 AI candidates, P10 AI architect | AI provider key (Render environment) |
| P06 live | GitHub App and a private test repository (docs/GITHUB.md) |
| P09 Tier 1–2 | Paid isolated compute: managed microVM sandboxes or a self-hosted node; package proxy |
| P04 and P10 claims | Domain expert review (SAP Commerce, Salesforce); permission to use real or open-source repositories for the labelled architecture evaluation |
| P07 | Identity provider, hosting tier, data-retention policy |

## Rules that keep applying

- Every phase prompt specifies implementation, tests and evidence. Security, authorization and
  source isolation started in P00/P01.
- No phase claims that every language or framework scenario is covered. A conditional capability
  can be unavailable, but it cannot be called verified. Mandatory gate failures keep a phase open.
- **Product milestones:**
  - first usable release: P01;
  - framework-aware repair product: P01–P05;
  - in-portal fixing with exports: P08;
  - verified builds: P09;
  - architecture intelligence and remediation: P10–P11;
  - qualified external release: P07.
- GitHub is an optional input and publication channel, never a hidden dependency of upload-based
  analysis.
