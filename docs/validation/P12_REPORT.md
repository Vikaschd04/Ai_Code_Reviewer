# Phase P12 validation report — NFR assessment

Date: 10 October 2026. Environment: macOS arm64, Python 3.14, PostgreSQL 18.6, Temporal CLI dev
server, PMD 7.27.0, ESLint 10.11.0, Opengrep 1.30.0, Trivy 0.69.3. Decision record:
[ADR 0021](../adr/0021_NFR_QUESTIONNAIRE.md). Specification: [NFR_ASSESSMENT.md](../NFR_ASSESSMENT.md).

Status: **IN_PROGRESS.** Slice 1 is delivered and verified: the questionnaire, the NFR profile,
evidence from declared libraries and files, gaps from tracked issues, the readiness view and the
CSV and Markdown exports. Slices 2–6 are planned (prompts/P12_NFR_ASSESSMENT.md):

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
