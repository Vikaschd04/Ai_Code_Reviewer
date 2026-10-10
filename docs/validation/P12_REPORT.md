# Phase P12 validation report — NFR assessment

Date: 10 October 2026. Environment: macOS arm64, Python 3.14, PostgreSQL 18.6, Temporal CLI dev
server, PMD 7.27.0, ESLint 10.11.0, Opengrep 1.30.0, Trivy 0.69.3. Decision record:
[ADR 0021](../adr/0021_NFR_QUESTIONNAIRE.md). Specification: [NFR_ASSESSMENT.md](../NFR_ASSESSMENT.md).

Status: **IN_PROGRESS.** Update 10 October 2026: at the owner's request the questionnaire was
replaced by NFR checkpoints with resolution help ([ADR 0024](../adr/0024_NFR_CHECKPOINTS.md); see
the last section). Slice 1 is delivered and verified: the questionnaire, the NFR profile,
evidence from declared libraries and files, gaps from tracked issues, the readiness view and the
CSV and Markdown exports. With the product simplification (ADR 0022), the insight engine and
the advisor agent are also delivered: the agent part of slice 5, with live quality BLOCKED on the
owner's key. Slice 2 (configuration and infrastructure evidence, [ADR 0023](../adr/0023_CONFIGURATION_EVIDENCE.md))
is delivered and verified; see its section below. Slices 3, 4 and 6 are planned
(prompts/P12_NFR_ASSESSMENT.md):

2. configuration and infrastructure evidence (delivered);
3. code-pattern evidence (delivered: timeouts, blocking reactive calls, unbounded thread pools);
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

## Configuration and infrastructure evidence (slice 2, 10 October 2026; ADR 0023)

| Deliverable (P12 plan) | Delivered | Where |
|---|---|---|
| Trivy misconfiguration checks offline, after verification | 563 checks embedded in the pinned binary (`--skip-check-update`; trivy-checks, MIT) for Dockerfile, Kubernetes, Helm, CloudFormation and Azure ARM, in the same Trivy run; cause lines, Trivy's guidance and link; code excerpts not stored; unrenderable Helm charts listed. **Terraform not scanned by Trivy** (it downloads remote modules, see findings) | `engines/trivy.py` |
| Gaps as `nfr` findings with the normal lifecycle | 6 rules: Kubernetes single instance (overlays, kustomizations, autoscalers considered), no readiness probe on a container with a port, Recreate; Spring Boot sensitive Actuator endpoints, public health details, `ddl-auto` schema changes (high for create and create-drop) | `engines/nfr.py`, `nfr/config.py`, catalog `crp-rules-v5` |
| Supporting evidence with file and line | 9 new signals (more than one instance, autoscaling, disruption budgets, probes, graceful shutdown, timeouts, pool size, backups, multi-zone) and locations for Actuator health probes, circuit breakers and retries, stored in the run's diagnostics; Helm templates and Terraform security listed as not checked | `nfr/signals.py` (`crp-nfr-signals-v2`), `services/nfr.py` |
| Questionnaire and insights | Mapping `crp-nfr-mapping-v2`; 4 new guidelines (deployments can go down, automatic schema changes, no requests and limits, insecure configuration) | `nfr/questionnaire.py`, `insights/engine.py` (`crp-insights-v2`) |
| Positive and negative fixtures | `nfr-config`: every rule has a positive and a negative example (README table); `nfr-mixed` now has a real manifest and Spring Boot settings | `fixtures/projects/nfr-config`, `fixtures/projects/nfr-mixed` |

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Rules: positive and negative | 9 expected findings with exact lines; overlay-raised and autoscaled workloads, probed and portless containers, development profiles and test resources produce none; create is high, update medium; `shutdown` not listed as exposed (exposure does not enable it) | PASS | `test_rules_fire_on_positive_examples_only`, `test_relaxed_keys_lists_and_profiles`, `test_autoscaler_owns_the_replica_count` |
| Honest coverage | Broken YAML FAILED with its line; Helm template NOT_ATTEMPTED; run PARTIAL | PASS | `test_coverage_is_honest_about_templates_and_broken_files` |
| Parsing bounds | Alias expansion stops at 20,000 nodes; a recursive anchor fails instead of looping; placeholders and templated values not judged | PASS | `test_anchor_expansion_and_recursion_are_bounded`, `test_placeholders_and_templated_values_are_not_judged` |
| Real Trivy, no downloads | Root Dockerfile flagged (DS-0002, DS-0026), hardened one not; privileged container KSV-0017 with lines and link; no Terraform results; the chart with an unvendored dependency listed as unrendered; nothing under `.aqua` in the run's folder; no `"Lines"` in the stored report | PASS | `test_trivy_misconfigurations_use_embedded_checks_and_never_download` |
| Evidence reaches the questionnaire | Stored signals replace the slice 1 "not checked yet" context; capped locations keep the full count; unknown signals ignored; older reviews keep the old context | PASS | `test_detect_uses_config_evidence_and_says_what_is_not_checked` |
| Real review (configuration fixture) | `nfr` PARTIAL (1 failed, 1 template), 9 tracked `nfr` issues, Trivy misconfigurations; continuous availability "needs work" with replicas, disruption budget and graceful shutdown as evidence and Helm charts as not checked; data recovery has the 2 schema issues; autoscaling cited for spikes; the 4 new recommendations | PASS | `test_p12_config.py` |
| Real review (NFR fixture) | Kubernetes probe cited at `deploy/k8s/deployment.yaml:20`, replicas at line 6, Actuator health probes at `application.yml:7`; no "not checked yet" for the manifest | PASS | `test_p12_nfr.py`, `e2e/p12-nfr.spec.ts` |
| Detector precision and recall on real systems | — | Not measured (slice 6) | — |

Checks for slice 2:

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Tests | `caffeinate -i make test` | exit 0: 597 pytest (12 new) + 30 vitest, 10 min. A first run had 8 failures, all from expectations that list every engine (sample project, hosted smoke, upload comparison) and from changing `nfr-mixed` while the run was in progress; updated and rerun clean |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 21/21 in 3.2 minutes; the NFR journey now checks the Kubernetes probe cited at `deploy/k8s/deployment.yaml:20`; screenshots reviewed in light, dark and at 390 px (long paths wrap, no horizontal scroll) |

Findings during slice 2:

- **Trivy downloads remote Terraform modules even with `--offline-scan`.** A trial scan of a
  `module { source = "terraform-aws-modules/vpc/aws" }` block fetched the module from the
  registry into `$TMPDIR/.aqua/cache` (deleted after the trial). Trivy 0.69.3's Terraform parser
  defaults to `allowDownloads: true` and `trivy fs` has no flag to turn it off. Terraform was left
  out of the misconfiguration scanners and every Trivy run now gets an unreachable proxy.
- **Helm render failures appear only in Trivy's log**, not in its JSON. The adapter no longer
  passes `--quiet` and reads the failures from the log, so such charts are listed instead of
  looking clean.
- **Autoscalers own the replica count**: a first draft took the largest of `spec.replicas` and the
  autoscaler minimum, which would have hidden an autoscaler that allows one instance.
- **`shutdown` is not exposed by `include=*`** (it is disabled unless switched on), so it is not
  named in the Actuator finding.
- Existing expectations listing every engine (sample project, hosted smoke, deploy smoke script,
  upload comparison) were updated for the new engine, which is NOT_APPLICABLE there.
- **Memory:** loading the embedded checks raises Trivy's peak resident memory on `nfr-mixed`
  from about 95 MB (vulnerabilities and secrets) to 158 MB (`/usr/bin/time -l`, macOS arm64), within
  the lite profile's 512 MB budget; CI's lite smoke test under 512 MB checks it on Linux.

Limitations:

- Helm templates are not checked by the `nfr` engine, and Terraform security settings are not
  checked at all (until a no-network sandbox or a download switch in Trivy, ADR 0023).
- Workloads are matched across files by kind and name (namespaces set at deploy time are not
  known); values set outside the upload (pipelines, other repositories, environment variables,
  a config server) are not visible, which the messages and the catalog say.
- A Deployment without a security context gets many Trivy findings (Pod Security Standards); the
  insights group them, but the Issues list is long.
- Precision and recall per detector are measured only on the synthetic fixtures so far (slice 6).

## NFR checkpoints replace the questionnaire (10 October 2026; ADR 0024)

Owner request: "remove the NFR questionnaire as it is not required; we just need to give insight
to users of NFRs for the project they uploaded, and help them resolve NFR checkpoints."

| Deliverable | Delivered | Where |
|---|---|---|
| NFR insight for the uploaded project | 28 checkpoints in six areas; statuses needs attention, not found, handled elsewhere, in place, no issues found, not checked, not applicable, from open issues, evidence with file and line, and the review's engine runs; area health; to-resolve list first | `crp_analysis/insights/engine.py` (`crp-insights-v3`), `routes/insights.py`, `pages/InsightsView.tsx` |
| Help to resolve | Stack-specific steps (Spring Boot, Node.js, Kubernetes); Start fixing; recipes for all six configuration rules (behaviour notes, validation ladder); "handled elsewhere" for missing mechanisms (versioned, attested, undo); advisor plans from the checkpoints (prompt v2) | `fixes/config_recipes.py`, `insights/decisions.py`, `insights/advisor.py` |
| Questionnaire removed | Questionnaire, profile, assessment and exports, three endpoints (404 now), the view, "Tell us your targets", per-signal question mappings; old links redirect; stored profiles kept in the table, unread | ADR 0024 |

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Catalog coverage | Every catalog rule lands in one checkpoint; specific checkpoints before category catch-alls (an SAP extension cycle, category reliability, lands in "no cycles", not "errors are handled") | PASS | `test_every_catalog_rule_lands_in_one_checkpoint` |
| Honest statuses | Attention with priority and counts; resolved issues ignored; in place with file and line; not found; not applicable without Kubernetes, rules or an HTTP framework; engines that did not run give "not checked"; a partial run says so; no review gives "not checked" everywhere | PASS | `test_statuses_follow_the_evidence`, `test_checks_that_did_not_run_are_not_clean` |
| Team decisions | Only for missing mechanisms; 3–500 characters; ignored once the mechanism appears; old questionnaire documents hold none; API 422/404, isolation 404, versions only on change | PASS | `test_team_decisions_count_only_while_the_mechanism_is_missing`, `test_decisions_are_validated_and_old_documents_hold_none`, `test_checkpoint_decisions_and_isolation` |
| Recipes | Each of the six recipes removes its issue on a re-check without breaking the file; narrow shapes only (multi-line lists and one-line containers refused with a reason) | PASS | `test_recipes_resolve_every_configuration_gap_in_the_fixture`, `test_recipes_change_only_the_narrow_shapes_they_understand` |
| Workspace round trip | All 9 configuration issues of `nfr-config` fixed by recipes in one workspace; the workspace check reports every one fixed and nothing new | PASS | `test_p12_config.py` |
| Real reviews | `nfr-mixed`: health checks in place (pom.xml:9, application.yml:7, deployment.yaml:20), two instances at deployment.yaml:6, rollouts in place, autoscaling not found, monitoring handled then undone; advisor plan cites checkpoints | PASS | `test_p12_nfr.py`, `test_insights_advisor.py` |
| Browser | Checkpoints with evidence, handled elsewhere and undo, old `?tab=nfr` link, Start fixing, AI plan; light, dark, 390 px | PASS | `e2e/p12-nfr.spec.ts`, `e2e/insights.spec.ts` |

Checks for this change:

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Tests | `caffeinate -i make test` | exit 0: 599 pytest + 30 vitest, 8 min 40 s |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 21/21 in 3.6 minutes; screenshots reviewed in light, dark and at 390 px |

Limitations: targets and regulations are no longer collected, so no checkpoint judges whether the
evidence meets a stated target; checkpoint statuses describe the upload, not run-time behaviour.

## Code-pattern evidence (slice 3, 10 October 2026)

| Deliverable | Delivered | Where |
|---|---|---|
| Outgoing calls without timeouts | `new RestTemplate()` without a request factory set later; JDK `HttpClient.newHttpClient()` or a builder without `connectTimeout`; `HttpURLConnection` without `setReadTimeout` in the same method; `axios.create` without `timeout` — category reliability, family `reliability.no-timeout`, medium | `rules/opengrep-rules.yml`, catalog `crp-rules-v6` |
| Blocking calls in reactive code | `block()`, `blockFirst()`, `blockLast()`, `Thread.sleep()` inside methods returning `Mono`/`Flux` — performance, medium | same |
| Unbounded thread pools | `Executors.newCachedThreadPool()` — performance (scalability), low | same |
| Checkpoints | Calls without timeouts put "Remote calls are protected" on the list to resolve even when resilience libraries are present; the others land in "No costly operations in hot code" | `insights/engine.py` |
| Platform rows | Already covered by the packs: SAP Commerce unbounded FlexibleSearch and saves in loops, Salesforce SOQL/DML in loops (PMD Apex) | P04 |

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Positive and negative examples | 9 expected findings with exact lines in `nfr-code`; clients with timeouts or a timed request factory, `block()` outside reactive code, a bounded `ThreadPoolExecutor` and an axios client with a timeout stay silent; no new rule fires on the security fixture | PASS | `test_resilience_rules_fire_only_on_positive_examples`, `test_every_owned_rule_fires_on_positive_and_not_on_negative_examples` |
| Catalog and checkpoints | Every new rule is catalogued with a sourced rationale and lands in a checkpoint | PASS | `test_every_catalog_rule_lands_in_one_checkpoint` |
| Real review | `nfr-code` reviewed: "Remote calls are protected" needs attention with 6 issues (medium), "No costly operations in hot code" with 3; nothing from these rules in the negative files | PASS | `test_p12_code.py` |

Checks for slice 3:

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Tests | `caffeinate -i make test` | exit 0: 601 pytest + 30 vitest, 8 min 39 s. The first run had 1 failure: a test pinned the owned Opengrep rule count at 18 (now 24 by design); updated, the same test now also checks the `nfr` rules' catalog entries, and the rerun was clean |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 21/21 in 2.9 minutes |

Limitations: the rules see one file at a time — a timeout set in another class (a shared bean or
`axios.defaults.timeout`) is not seen, so such findings can be resolved as false positives;
statelessness (server-side sessions) is not checked yet; precision on real code is not measured.

