# ADR 0024 — NFR checkpoints replace the NFR questionnaire

Status: accepted; implemented and verified (unit tests, API test, real-stack reviews, browser
journeys). Date: 10 October 2026. Owner: repository owner (request of 10 October 2026: "remove
the NFR questionnaire as it is not required; we just need to give insight to users of NFRs for
the project they uploaded, and help them resolve NFR checkpoints"). Supersedes the questionnaire,
profile and export parts of ADR 0021 and the recommendation list of ADR 0022.

Context and constraints:

- The questionnaire (23 verbatim questions, team targets, attested answers, CSV and Markdown
  exports) asked users to do work the product should do for them, and most questions can only be
  answered by the team. Insights showed the same evidence a second time as recommendations.
- Users want one answer per non-functional requirement: does the uploaded project meet it, what
  shows that, and how to fix it if not.
- Statuses must come from evidence only; "not found in the upload" is not "absent", "no issues"
  is not "verified at run time", and checks that did not run are not clean (AGENTS.md).

Decision:

1. **Checkpoints** (`crp_analysis/insights/engine.py`, `crp-insights-v3`): 28 concrete,
   code-checkable requirements in six areas (security, reliability and availability, performance
   and scalability, operations and monitoring, architecture and maintainability, experience and
   portability). Each has a goal-phrased title, why it matters, how to resolve it (with
   stack-specific steps for Spring Boot, Node.js and Kubernetes projects), the rule families,
   rules or categories whose open issues violate it, the engines that check for it, the signals
   that show it is in place, whether "not found" is a problem (and its priority) and when it
   applies (Kubernetes, configuration, platform code, architecture rules, web UI, HTTP API).
2. **Statuses**: needs attention (open issues; priority from the most severe), not found
   (missing mechanism; catalog priority), handled elsewhere (team statement), in place (evidence
   with file and line), no issues found (the checking engines ran), not checked (they did not
   run, or no review, or the configuration checks did not run), not applicable. An issue counts
   against the first checkpoint that matches it by family, rule or engine; checkpoints that match
   whole categories are tried last. Area health: needs attention (any high-priority item to
   resolve), could be better (any item), no problems found (something passed), not enough
   evidence.
3. **Resolving**:
   - *Start fixing* opens the upload's fix workspace narrowed to the checkpoint's issues
     (unchanged from ADR 0022).
   - **Fix recipes for the configuration checks** (`crp_analysis/fixes/config_recipes.py`):
     `ddl-auto` → `validate`; `show-details` → `when-authorized`; Actuator exposure → `health,info`
     for `*`, or the list without heapdump/env/configprops/threaddump; `Recreate` →
     `RollingUpdate`; `replicas`/`minReplicas`/kustomize `count` 1 → 2 (or `replicas: 2` added as
     the first entry of `spec`); a TCP readiness probe on the container's first port. Each states
     the behaviour it changes, edits only the reported line (or inserts right after it), refuses
     other shapes with a reason, and goes through the validation ladder.
   - **Handled elsewhere**: for "not found" checkpoints, members record how the mechanism is
     handled outside the code (3–500 characters). Stored as append-only versions in the existing
     table `nfr_profile_versions` (document schema `crp-nfr-decisions-v1`; no migration); shown as
     the team's statement; undo clears it. Documents of the retired questionnaire hold no
     decisions and stay in the table untouched.
   - The **advisor** (prompt `rx-ai-advisor-v2`) plans from the checkpoints that need work and
     their facts; targets are gone. Citation and number checks are unchanged.
4. **Removed**: the questionnaire (`questionnaire.json`/`.py`), the profile and the assessment
   modules, `GET /v1/projects/{id}/nfr`, `PUT /v1/projects/{id}/nfr/profile`,
   `GET /v1/projects/{id}/nfr/export` (now 404), the "NFR questionnaire" view, "Tell us your
   targets", and per-signal question mappings. Old links (`?tab=nfr`, `?view=nfr`) open the
   checkpoints.
5. **API**: `GET /v1/projects/{id}/insights` returns `areas` (with counts per status),
   `checkpoints` (to resolve first, by priority), `not_checked` (material refactorX does not
   assess), `decisions_version`, `can_edit` and the advisor state.
   `PUT|DELETE /v1/projects/{id}/insights/checkpoints/{checkpoint_id}/handled` (members; 404
   unknown checkpoint, 422 for checkpoints that are resolved through their issues).

Alternatives considered:

- Keep the questionnaire as an optional export: rejected by the owner's request; the checkpoint
  list carries the evidence the questionnaire showed.
- A new table for decisions: rejected for now; the existing versioned, audited table fits and
  keeps the retired data intact without a destructive migration. A later migration may rename it
  or drop the old documents if the owner wants.
- AI-written resolutions for missing mechanisms: not in this change; steps come from the catalog
  and the recipes, and the optional advisor orders them with checked citations.

Consequences and migration/reversal approach:

- Teams lose the place to state targets and regulations; judging whether evidence is enough for a
  stated target is out of scope until measured evidence exists.
- Stored questionnaire profiles are no longer shown; they remain in the database.
- Reversal: restore the removed modules and routes from Git; the table and data are unchanged.

Evidence and source/version references: docs/validation/P12_REPORT.md (checkpoints section);
docs/NFR_ASSESSMENT.md.

Affected contracts, phases and tests: OpenAPI tag `insights` (new response shape, two new
operations, three removed); `test_insights.py`, `test_advisor.py`, `test_nfr.py`,
`test_nfr_config.py` (recipes), `test_insights_api.py` (replaces `test_nfr_api.py`),
`test_p12_nfr.py`, `test_p12_config.py` (workspace round trip), `test_insights_advisor.py`;
`e2e/p12-nfr.spec.ts`, `e2e/insights.spec.ts`, `e2e/ui-tour.spec.ts`.
