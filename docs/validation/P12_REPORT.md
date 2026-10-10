# Phase P12 validation report — NFR assessment

Date: 10 October 2026. Environment: macOS arm64, Python 3.14, PostgreSQL 18.6, Temporal CLI dev
server, PMD 7.27.0, ESLint 10.11.0, Opengrep 1.30.0, Trivy 0.69.3. Decision record:
[ADR 0021](../adr/0021_NFR_QUESTIONNAIRE.md). Specification: [NFR_ASSESSMENT.md](../NFR_ASSESSMENT.md).

Status: **IN_PROGRESS.** Slice 1 is delivered and verified: the questionnaire, the NFR profile,
evidence from declared libraries and files, gaps from tracked issues, the readiness view and the
CSV and Markdown exports. With the product simplification (ADR 0022), the insight engine and
the advisor agent are also delivered: the agent part of slice 5, with live quality BLOCKED on the
owner's key. Slices 2–4 and 6 are planned (prompts/P12_NFR_ASSESSMENT.md):

2. configuration and infrastructure evidence;
3. code-pattern evidence;
4. measured evidence import;
5. AI NFR analyst (needs the owner's key);
6. labelled evaluation per detector and per question state.

## Slice 1 deliverables

| Deliverable (P12 prompt) | Delivered | Where |
|---|---|---|
| Question bank mapped to ISO/IEC 25010:2023 | 9 aspects, 23 questions verbatim, with help text, team-only flags and the targets that answer each. Not yet: custom questions | `crp_analysis/nfr/questionnaire.json`, `questionnaire.py` |
| NFR profile, versioned and audited | 10 targets, regulations, platforms, attested answers and "does not apply" with a reason; append-only versions; 409 on conflict | `nfr/profile.py`, migration 0013, `routes/nfr.py` |
| Existing findings tagged | Every catalog rule maps to at least one question; Trivy vulnerabilities and secrets; architecture smells | `questions_for_rule` |
| Evidence | 29 signals (28 catalogued plus automated tests; libraries from manifests with file and line, files in the upload, automated tests); `context` signals marked "not checked yet" | `nfr/signals.py` |
| Readiness view and exports | Six statuses per question; counts per aspect; CSV and Markdown. Not yet: HTML report | `nfr/assessment.py`, `pages/NfrView.tsx` |

## Mandatory tests that apply to slice 1

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Honesty | No upload: no evidence claims; nothing found → "Not checked yet"; team answers labelled attested; a resolved issue is not a gap; accepted risks counted apart; reports state that compliance is not certified | PASS | `test_status_precedence_is_honest`, `test_exports_list_every_question_and_never_claim_compliance`, `test_members_save_profiles_and_other_workspaces_see_nothing` |
| Questionnaire integrity | 9 aspects with 4/3/4/2/2/2/2/2/2 questions; wording verbatim; profile fields valid | PASS | `test_questionnaire_is_the_owner_list_mapped_to_iso_25010` |
| Mapping coverage | Every catalog rule (98) maps to at least one known question; Trivy and smell rules | PASS | `test_every_catalog_rule_is_a_gap_for_at_least_one_question` |
| Signals: positive and negative | Actuator at `pom.xml:21`, Resilience4j, Flyway, OpenTelemetry, Dockerfile, CI, OpenAPI, runbooks, Kubernetes, Terraform, application configuration, two test files; `redis-mock` and a look-alike Maven group not matched | PASS | `test_signals_cite_manifest_lines_and_files` |
| Profile validation | Ranges, whole numbers, unknown targets and questions, "does not apply" needs a reason, list limits, unknown fields — each with where and why | PASS | `test_profile_validation_says_where_and_why`; API 422 `invalid_profile`, 400 for request ranges |
| Real review | Fixture `nfr-mixed` uploaded and reviewed: Actuator cited at `pom.xml:9`; empty catch and `eval` make reliability and security "needs work"; Dockerfile and OpenAPI "evidence found"; Kubernetes manifest listed as not checked; RTO and availability targets turn "needs your input" into "evidence found"; an answer and "does not apply" recorded; stale save 409; CSV has 24 rows; Markdown states the profile version | PASS | `test_p12_nfr.py` |
| Isolation | Demo members save profiles; other workspaces get 404 on the view, the save and the export | PASS | `test_nfr_api.py` |
| Browser | Evidence with manifest line, targets saved, answer, not applicable, CSV link; light, dark, 390 px without horizontal scroll | PASS | `e2e/p12-nfr.spec.ts`; `p12-nfr*.png` |
| Detectors, imports, AI, scale | — | Not in slice 1 | — |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Tests | `caffeinate -i make test` | exit 0: 577 pytest (slice 1 added 8) + 29 vitest, 10 min |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 21/21 in 4.1 minutes |

## Findings during the slice

- **The questionnaire has 23 questions, not 24.** The planning documents of 10 October said 24;
  corrected everywhere after counting the article again.
- **Layout:** the first screenshots showed the 12 target fields in one long column and the status
  tiles wrapping 5 + 1; both now use existing grid classes (3 columns of fields, 3 × 2 tiles).
- **Request validation is a 400:** ranges checked by the request model return 400 like the rest
  of the API; domain problems (unknown question, missing reason) return 422 with each problem.

## Limitations

- Evidence is presence: a declared library or a file shows intent, not that it is configured or
  works. Configuration, deployment manifests and code patterns are assessed in slices 2–3.
- Gap mapping is by rule category and family, deliberately conservative; a defect can affect more
  questions than it is mapped to.
- The view is computed on request; it is not stored per review, so there are no trends yet.

## Simplification, insights and the advisor agent (10 October 2026; ADR 0022)

Owner request: keep only what users need, and let tools and agents together analyse the project
against NFR guidelines and guide its improvement, without hallucination.
Plan: [PRODUCT_SIMPLIFICATION.md](../PRODUCT_SIMPLIFICATION.md).

| Deliverable | Delivered | Where |
|---|---|---|
| Fewer, clearer screens | Project tabs 11 → 6 (Overview, Issues, Insights, Fixes, Uploads, Settings); Insights views Recommendations, NFR questionnaire, Architecture; one "Fix this" card; AI file review and "Coming soon" removed from view; old links redirect | `pages/ProjectPage.tsx`, `pages/InsightsView.tsx`, `components/Fixes.tsx`, `App.tsx` |
| Tools: recommendations by area | 15 issue guidelines (every catalog rule maps), 5 missing-mechanism guidelines (only after a review, only when the upload shows nothing, skipped when the team explained), per-area target requests, area health | `crp_analysis/insights/engine.py` |
| Agent: improvement plan | AI run kind `advisor` with a frozen evidence pack; `submit_plan`; deterministic citation and number checks; removed steps listed | `crp_analysis/insights/advisor.py`, `ai/results.py`, worker `ai_run.py`, migration 0014 |
| Start fixing | A recommendation opens the fix workspace narrowed to its issues | `issue` filter on the workspace issue list; `#/workspaces/{id}?issues=` |

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Every catalog rule is covered, first match wins | 98 rules; precedence cases (secrets before other security, SAP extension cycles as architecture, retired API as platform) | PASS | `test_every_catalog_rule_lands_in_one_recommendation` |
| Recommendations follow the evidence | Severity → priority, summaries ("2 open issues in 2 files (1 critical, 1 high)"), resolved issues ignored, missing mechanisms only when absent, team answers respected, area states | PASS | `test_insights.py` (4) |
| No hallucination in plans | Steps with unknown ids only, quotes that do not match the file, or invented numbers are removed; names like SHA-256 allowed; a summary with invented numbers is withheld; repository text cannot close the prompt's fences | PASS | `test_advisor.py` (3) |
| Real stack | The nfr-mixed upload is reviewed and gives the injection, defects, diagnostics and targets recommendations; no "missing" claims for mechanisms the upload shows. The agent is off by default (409, no model request). After the admin switches AI on, the fake model's plan keeps 2 grounded steps and removes "Add a web application firewall" (no evidence) and "Fix all 4242 issues" (invented number). Start fixing narrows the workspace to the recommendation's issues | PASS | `test_insights_advisor.py` |
| Browser | Six tabs; Overview health and next steps; recommendation steps; Start fixing to a focused workspace; AI switched on in Settings; plan with 2 steps and "2 suggestions removed for lack of evidence"; light, dark, 390 px | PASS | `e2e/insights.spec.ts`; `insights*.png` |
| Journeys updated to the new navigation | AI settings in Settings and questions in Insights (p03); GitHub connection in Settings and reviews in Uploads, pull request from a workspace (p06); AI switch in Settings (p08); tour through Insights (ui-tour). The single-fix journey (p05) was removed with its UI entry; the P05 backend tests remain | See checks below | `e2e/*.spec.ts` |

Checks for this work:

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Tests | `caffeinate -i make test` | exit 0: 585 pytest (8 new) + 30 vitest, 14 min 38 s |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 21/21 in 3.5 minutes (insights journey added, p05 single-fix journey removed). A first run had 3 failures from the new navigation in specs not yet updated (project deletion now under Settings, SAP architecture now under Insights, and a wrong test id in the GitHub journey); fixed and rerun clean |

Findings during this work:

- The first Overview screenshot clipped the area labels in the narrow health card; it now uses a
  list there and tiles only on the Insights page.
- Three separate "Tell us your targets" cards pushed real problems down; they are now one card
  after the problem recommendations.
- An `eval()` reported by ESLint and Opengrep counts as two tracked issues; recommendations
  count what the Issues tab shows.

Limitations: the advisor's real-world quality is unmeasured until the owner adds an AI key (as
for P03). The checks guarantee that every kept step is backed by the tools' evidence, not that
the plan is the best one.
