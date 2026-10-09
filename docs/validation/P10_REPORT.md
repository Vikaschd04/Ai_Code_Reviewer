# Phase P10 validation report — Architecture intelligence

Date: 9 October 2026. Environment: macOS arm64, Python 3.14, PostgreSQL 18.6, Temporal CLI dev
server, PMD 7.27.0, ESLint 10.11.0, Opengrep 1.30.0, Trivy 0.69.3. Decision record:
[ADR 0018](../adr/0018_ARCHITECTURE_METRICS.md).

Status: **IN_PROGRESS.** Slices 1–3 are delivered and verified: the architecture model and
structural metrics with Structure health (slice 1), intended architecture as code with breaches as
tracked issues (slice 2, [ADR 0019](../adr/0019_ARCHITECTURE_RULES.md)), and the structural smell
catalog (slice 3, [ADR 0020](../adr/0020_ARCHITECTURE_SMELLS.md)). Still planned
(docs/ROADMAP.md):

3. performance and scalability catalogs (the rest of the catalogs);
4. Git-history hotspots;
5. runtime evidence;
6. recommendations;
7. grounded AI architect, which needs the owner's key;
8. evaluation.

## Slice 1 deliverables

| Deliverable (P10 prompt) | Delivered | Where |
|---|---|---|
| 1. Architecture model (components) | Partly delivered: components from Java packages and folders; test code left out and counted. Not yet: team-edited components and layers, framework-aware grouping, the data-access map, interfaces | `services/architecture.py`, `crp_analysis/architecture/metrics.py` |
| 2. Metrics | Partly delivered: Ca, Ce, I, A, D, fan-in and fan-out, size, cycles with the cheapest cut; undefined ratios stay null. Not yet: complexity, modularity, data sharing, behavioural metrics | same; `GET /v1/snapshots/{id}/architecture` |
| 8. UI | Partly delivered: Structure health (tiles, cycles with what to cut, needs attention, all measurements, dependency matrix). Not yet: C4-style views, hotspot treemap, trends | `components/ArchitectureHealth.tsx` |
| Graph extractor v2 | Abstract types marked for Java (abstract classes, interfaces, annotation types) and TypeScript (abstract classes, interfaces); older builds show abstractness as not measurable | `graph/extract.py`, `graph/resolve.py` |

## Mandatory tests that apply to slice 1

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Metrics match hand-computed values | Layered fixture `app → svc → dom` with abstract types, duplicates, a same-component dependency and an unresolved one; zone fixture; on-demand package imports; test code | PASS. app (0, 2, 1.0, 0.0, 0.0), svc (2, 1, 0.333, 0.5, 0.167), dom (2, 0, 0.0, 0.5, 0.5); edge weights 2, 2 and 1; pain and uselessness detected, a one-dependent leaf not flagged; ratios null without evidence | `test_architecture_metrics.py` (8) |
| Cycles found with the minimal edge set suggested | Two overlapping cycles where cutting the heavy edge (cost 3) loses to two light edges (cost 2); two separate cycles; 60 random graphs comparing exact and heuristic | PASS. Exact cut = {y→x, y→z}. Every heuristic cut breaks its cycle and is never cheaper than the exact one | same |
| Real review | Synthetic Java and TypeScript project uploaded and reviewed (real graph extraction and resolution) | PASS. The API returns the hand-computed values, the package cycle x↔y with a one-dependency exact cut, folder components for TypeScript, and the test file excluded | `test_p10_architecture.py` |
| Scale | Pure computation on random (worst-case) graphs | 1,000 files: 0.2 s and 3 MB. 10,000 files: 1.0 s and 34 MB. 50,000 files and 400,000 dependencies: 7.4 s and 193 MB. Responses capped (400 edges, 50 per cycle list) | ADR 0018 |
| Browser | Architecture tab on the graph fixture | PASS. Structure health shows parts, the measurements table (`com.acme.core`) and the dependency matrix; no horizontal scroll at 390 px | `e2e/p02-analysis.spec.ts`; `p10-structure-health.png`, `p10-structure-health-mobile.png` |
| No history = not available | — | Not in slice 1 (behavioural metrics are slice 4) | — |
| Rule violations, catalogs, runtime import, AI architect, evaluation | — | Not in slice 1 | — |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Types for Linux | `uv run mypy --platform linux` | no issues in 179 source files |
| Tests | `make test` | exit 0: 512 pytest (P10 added 9: 8 metric tests, 1 real-stack review) + 29 vitest |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 19/19 in 5.3 minutes (the P02 architecture journey covers Structure health) |

## Findings during the slice

- **Performance:** the first cycle-cut implementation, a repeated greedy, was O(E²): 7 s for 50
  tangled packages and impractical beyond. Replaced with weighted Eades–Lin–Smyth plus a restore
  pass: 0.2 s, and half the cut size.
- **Honesty:** Martin's zone of pain flagged leaf utilities used by a single file. Zones now need
  evidence (pain: at least 3 dependent files; uselessness: at least 2 abstract types).
- **Name clash:** a new helper in the graph routes shadowed an existing one; mypy caught it before
  it reached the neighborhood endpoints.

## Limitations

- Components are packages and folders. Layers, domains and framework units (SAP extensions,
  Salesforce packages) come with slice 2 and the framework mapping.
- Dependencies come from the syntax-level graph (K-P02-03), so resolution gaps lower the counts.
  The number of edges not counted is shown.
- Measured on request, without caching or history.

## Slice 2 — intended architecture as code (9 October 2026)

| Deliverable (P10 prompt) | Delivered | Where |
|---|---|---|
| 3. Rules in the portal, versioned and audited, YAML export and import | Layers (top to bottom, patterns over parts), layering `lower`/`next`/`none`, forbid rules, exceptions with reason and expiry; append-only versions with author, note and source; YAML parsed as data | `crp_analysis/architecture/rules.py`, migration 0012, `routes/architecture_rules.py` |
| 3. Every review reports deviations as findings with lifecycle | Engine `architecture` after the graph step; one finding per source file and target at the import line; per-rule hashes for honest rechecks | `crp_analysis/engines/architecture.py`, `crp_worker/scan.py`, `crp_analysis/lifecycle.py` |
| 8. UI: rules editor | "Architecture rules" card on the project's Architecture tab (summary, editor, check on the latest upload, history, YAML download); Issues filter by check | `components/ArchitectureRules.tsx`, `pages/IssuesView.tsx` |
| 9. Export: rules YAML, SARIF for violations | YAML export; breaches are findings, so the existing JSON and SARIF exports include them | `GET …/architecture-rules/export`, scan exports |

### Mandatory tests that apply to slice 2

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Breaches detected | Fixture with three layers, a forbid rule, an active and an expired exception, a sub-package, an on-demand import, a same-part and a same-layer dependency, a part in no layer and test code; `next` layering variant | PASS. Exactly 3 breaches (2 forbid, 1 layering) at the hand-computed lines; 1 use allowed; the expired exception listed; 9 dependencies checked; test code left out | `test_architecture_rules.py` (40) |
| Lifecycle-tracked on real reviews | Java project uploaded and reviewed: before rules, with rules, after a code fix, after a rule edit, after a rule removal | PASS. Not applicable without rules (with the reason); 2 findings at the imports (lines 3 and 4) and 2 OPEN issues; the code fix resolves the layering issue (VERIFIED_ABSENT) while the forbid issue stays | `test_p10_architecture.py::test_architecture_rules_on_real_reviews` |
| Exceptions with expiry | Rule-level `allow … until`; issue-level accepted risk with expiry (P02 lifecycle) | PASS. An exception until 2099 allows a use; one that expired in 2020 does not and is listed | same, and unit tests |
| A rule change re-evaluates honestly | Edit the forbid rule so it matches nothing; then remove it | PASS. Edit → issue OPEN with UNKNOWN "rule arch.forbid.domain-no-legacy changed since the earlier observation"; the untouched layering issue stays RESOLVED. Removal → RULE_OBSOLETE, still OPEN. Never "fixed" | same; `test_recheck_compares_the_rules_own_hash` |
| Rules run after the dependency map on Temporal | Same project reviewed on the Temporal stack, where other engines run in parallel | PASS. Architecture SUCCEEDED with 2 findings; it started after the graph step finished | `test_architecture_rules_run_after_the_graph_on_temporal` |
| Untrusted YAML | Anchors and aliases, two documents, a Python tag, broken YAML, more than 64 KB, unknown fields, bad patterns, duplicate names and keys, missing reasons, bad dates and severities | PASS. All refused with where and why; nothing saved | unit and API tests |
| Versioning and audit | Save, identical save, stale save, export and re-import into another project, second version, old version | PASS. Identical rules add no version; stale `base_version` → 409; the export imports with the same hash; history newest first with author and note | `test_architecture_rules_api.py` (3) |
| Workspace isolation | Demo member edits own project; another workspace's rules | PASS. Members can edit; other workspaces get 404 | same |
| Browser | Write rules from the example, invalid rules, check on the latest upload, save, re-review, breaches in Issues | PASS. Light, dark and 390 px without horizontal scroll | `e2e/p10-architecture-rules.spec.ts`; `p10-rules*.png` |

### Checks (slice 2)

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Types for Linux | `uv run mypy --platform linux` | no issues in 185 source files |
| Tests | `caffeinate -i make test` | exit 0: 557 pytest (slice 2 added 45) + 29 vitest, 8 min 37 s |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 20/20 in 3.3 minutes |

### Findings during the slice

- **Counts that did not follow the filter:** with the new check filter, the Issues tiles still
  counted every issue of the project (7 open while 4 rows showed). The check now scopes the counts
  too; the real-stack test asserts it.
- **Phone width:** long file paths in the breach list overflowed by 80 px at 390 px; they now wrap.
- **Build-only type error:** optional lists in the generated rules type passed `tsc --noEmit` but
  failed the build's `tsc -b`; fixed with explicit defaults.

### Limitations (slice 2)

- Parts are packages and folders; SAP extensions and Salesforce packages as parts come with
  framework-aware grouping.
- Breaches depend on the dependency map's resolution (K-P02-03): an unresolved import cannot be
  judged and is counted as not counted in Structure health.
- Rules are not read from uploads (uploads are untrusted); teams keep the exported YAML in their
  repository and import it.

## Slice 3 — structural smell catalog (9 October 2026)

| Deliverable (P10 prompt) | Delivered | Where |
|---|---|---|
| 4. Catalog: structural smells with evidence class | Cyclic dependency, unstable dependency, hub-like part; labelled *potential*; one finding per part; sourced rationale in the catalog (`crp-rules-v4`) | `crp_analysis/architecture/smells.py`, `engines/smells.py`, `rules/catalog.json` |
| Generated and test code handled | Generated code (`gensrc`, `generated`, `generated-sources`, `__generated__`, `*.generated.*`) and test code left out of the model and counted | `architecture/model.py`, `metrics.py` |
| Tracked findings that never fake a fix | `anchor_key` on part-level findings: issues and comparisons follow the part when its anchor file changes | `crp_worker/lifecycle.py`, `sources/changes.py`, `routes/reports.py` |
| Salesforce coverage | LWC `c/<name>` imports resolved (graph extractor v3) | `graph/resolve.py` |
| Evaluation | 10 hand-labelled synthetic cases; `crp-dev arch-eval` | `fixtures/architecture-eval`, `architecture/evaluation.py` |

### Mandatory tests that apply to slice 3

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Catalog accuracy: positive and negative fixtures | Per smell: Java, TypeScript, SAP Commerce extension with `gensrc`, Salesforce LWC; negatives include layered code, stable dependencies, a widely used facade (not a hub), an acyclic LWC chain | PASS, see the evaluation | `fixtures/architecture-eval` (10 cases: cycle 4 positive / 6 negative, unstable 1 / 9, hub 1 / 9) |
| Precision and recall per smell | `uv run crp-dev arch-eval` | Cycle: 8 TP, 0 FP, 0 FN (precision 1.0, recall 1.0). Unstable dependency: 1, 0, 0 (1.0, 1.0). Hub: 1, 0, 0 (1.0, 1.0). Synthetic set: it shows the detectors meet their specification, not real-project usefulness | `.local/arch-eval/report.json`; `test_labelled_evaluation_set_has_no_mismatches` |
| Hand-computed fixtures | Three-part cycle with cut c → a (1 import); instability 0.25 → 0.50 flagged at the import; 1 of 4 parts below 30% and a 0.056 difference within the delta not flagged; hub with fan-in 4, fan-out 4, upper quartiles 1; fewer than 8 parts or a fan-out of 2 not flagged | PASS | `test_architecture_smells.py` (11) |
| Generated and test code | A generated `gensrc` model class and a test class each close a cycle | Not reported; counted as generated and test files | same; SAP Commerce evaluation case |
| Real review and lifecycle | Java upload with a hub (9 packages) and a two-package cycle; second upload breaks the cycle and adds a new first file to the hub package | PASS. 3 findings (hub at `Hub.java`, cycle at each import, line 3); the hub issue moves to `AHelper.java` with event "the part's anchor file changed" and stays OPEN; both cycle issues RESOLVED (VERIFIED_ABSENT) | `test_architecture_smells_on_real_reviews` |
| Scale | Pure computation on random (worst-case) graphs | 1,000 files: 0.1 s and 3 MB. 10,000 files: 0.7 s and 35 MB. 50,000 files with 400,000 dependencies: 6.2 s and 196 MB, 2,128 findings (one per affected part) | ADR 0020 |

### Checks (slice 3)

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Types for Linux | `uv run mypy --platform linux` | no issues |
| Tests | `caffeinate -i make test` | exit 0: 569 pytest (slice 3 added 12) + 29 vitest, 11 min 23 s. A first run had one failure: the slice 1 test still expected graph extractor v2; updated to v3 and rerun clean |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 20/20 in 4.6 minutes (the rules journey also checks that "Architecture smells" runs) |
| Smell evaluation | `uv run crp-dev arch-eval` | 10 cases, no mismatches |

### Findings during the slice

- **Duplicate warnings inside cycles:** the evaluation flagged an unstable dependency between two
  LWC bundles that already form a cycle. Comparing stability inside a cycle is not meaningful (the
  parts change together), so those dependencies are now left to the cycle smell.
- **One issue per file did not scale for people:** the worst-case tangle produced 50,108
  findings with one per participating file. Smells are now one per part (2,128 on the same graph),
  with the part's files listed in the message.
- **Quadratic message building:** the cycle detector rebuilt the cut and the member list for
  every file; on 50,000 files it did not finish in 10 minutes. Both are now computed once per
  cycle.
- **Generic titles:** catalog titles replaced the specific ones ("Hub-like part" instead of
  "Hub-like part: com.acme.hub"); findings can now carry their own title.
- **Salesforce blind spot:** LWC `c/` imports were not followed, so cycles between components
  were invisible; they are now resolved.

### Limitations (slice 3)

- Thresholds are refactorX's (instability difference 0.1, 30% share, upper quartile and at least
  3 for hubs, 8 parts); they are not Arcan's benchmark-based thresholds.
- Not yet detected: god component, dead or isolated code, shared persistence, chatty
  interfaces, framework-specific smells beyond SAP extension cycles.
- Apex classes are not in the dependency map, so Salesforce smells cover LWC only.
- Accuracy on real projects is not measured (no expert-labelled real projects yet).
