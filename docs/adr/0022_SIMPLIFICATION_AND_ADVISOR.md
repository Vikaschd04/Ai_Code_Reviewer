# ADR 0022 — Simplified product and the grounded insights advisor

Status: accepted; implemented and verified (unit tests, real-stack tests with the labelled fake
model, browser journeys). The Insights views and the recommendation catalog were replaced by the
NFR checkpoints (ADR 0024); navigation, the advisor checks and Start fixing are unchanged. Date: 10 October 2026. Owner: repository owner (request of
10 October 2026: keep only what users need; let tools and agents together analyse a project
against NFR guidelines and guide its improvement, without hallucination). Plan:
docs/PRODUCT_SIMPLIFICATION.md.

Context and constraints:

- The project page had 11 tabs, two ways to fix one problem and three action cards on a finding.
  Architecture, NFR readiness and AI lived in separate places.
- Users need one answer to "how do I make this system better?", across security, reliability,
  performance and scalability, architecture, operations and experience. The answer must be backed
  by evidence and work without AI.
- AI must never invent problems, counts or targets (AGENTS.md: findings need evidence; AI
  hypotheses stay labelled; no source to a model without the project's policy).

Decision:

1. **Navigation.** Project tabs Overview, Issues, Insights, Fixes, Uploads, Settings (Settings
   for members). Insights has three views: Recommendations, NFR questionnaire, Architecture.
   Old tab links redirect. The finding page has one "Fix this" card. The AI file review is hidden
   from the UI (API kept). Earlier single fixes are collapsed under Fixes. The sidebar's "Coming
   soon" list is removed. Full keep/merge/hide table: PRODUCT_SIMPLIFICATION.md.
2. **Insight engine (tools, always on)** — `crp_analysis/insights/engine.py`, `crp-insights-v1`.
   - A curated guideline catalog maps open issues (by rule family, rule or category; first match
     wins; every catalog rule maps) to recommendations. Each has an area, a priority (from the
     most severe issue), what was found, why it matters, guided steps and the issues.
   - "Missing mechanism" recommendations are made only after a review and only when the upload
     shows nothing for them (monitoring, diagnostics, tests, health and pipeline, fault
     handling). They are skipped when the team explained how it is handled.
   - Per-area target requests come from NFR questions that only the team can answer.
   - Area health: attention (any high), improve (any medium or low), no problems found (evidence
     and no recommendations), not enough evidence.
3. **Advisor agent (optional, policy-gated, labelled AI)** — AI run kind `advisor` (migration
   0014 adds `ai_runs.context`), prompt `rx-ai-advisor-v1`, final tool `submit_plan`.
   - At request time, the recommendations, a numbered fact sheet (recommendations, their most
     severe issues, mechanisms found) and the team's targets are frozen in the run.
   - The agent may read code through the existing read-only, masked tools.
   - **Checks before anyone sees the plan:**
     - a step must cite at least one known recommendation or fact, or a code anchor whose quoted
       text matches the file; unknown ids are dropped and reported;
     - every number in a step must appear in what it cites (numbers glued to letters, dots or
       hyphens, such as SHA-256, are names);
     - a summary with numbers not in the facts is withheld.
   - Removed steps are listed with their reason.
4. **Start fixing.** A recommendation opens the upload's fix workspace (reused if it exists),
   narrowed to its issues (`issue` filter on the workspace issue list, up to 50 issues).
5. **API.** `GET /v1/projects/{id}/insights` (areas, recommendations, advisor state and latest
   plan); `POST /v1/projects/{id}/insights/plan` (members; AI gate: project policy, provider,
   monthly budget); AI runs expose `plan`.

Alternatives considered:

- Let the model write recommendations: rejected; recommendations, priorities and counts come from
  deterministic tools, and the model orders and explains them.
- Free-text plans checked by a second model: model agreement is not verification. Citations and
  numbers are checked deterministically.
- Removing the single-fix backend (P05): kept. The validation ladder and recipes power the fix
  workspace; only its separate UI entry was removed.

Consequences and migration/reversal approach:

- Bookmarks keep working through redirects; browser journeys follow the new navigation. The P05
  single-fix journey was removed with its UI entry; P05 backend tests remain.
- The advisor's real-world quality needs the owner's AI key (as for P03); the checks, budgets and
  policy gate are verified with the labelled fake model.
- One migration (0014, reversible).

Evidence and source/version references: docs/validation/P12_REPORT.md (simplification and
advisor section).

Affected contracts, phases and tests: OpenAPI tags `insights`, AI run `plan`, workspace issues
`issue` filter; `test_insights.py`, `test_advisor.py`, `test_insights_advisor.py`,
`e2e/insights.spec.ts`; updated `p03`, `p06`, `p08` and `ui-tour` journeys; removed
`p05-fixes.spec.ts`.
