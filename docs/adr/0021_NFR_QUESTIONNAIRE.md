# ADR 0021 — NFR questionnaire, profile and readiness (P12 slice 1)

Status: accepted; implemented and verified (unit tests, an API test, a real-stack review, a
browser journey). Date: 10 October 2026. Owner: repository owner (request of 10 October 2026;
prompts/P12_NFR_ASSESSMENT.md); implemented by the development agent.

Context and constraints:

- The owner wants the portal to answer non-functional requirement questions for an analysed
  system, starting from a published questionnaire (9 aspects, 23 questions).
- Code shows mechanisms, not behaviour under load. Targets, user numbers, regulations and the cost
  of downtime are not in code. The product never certifies compliance (PROJECT_BRIEF scope).
- The answer must never turn "nothing found" into "met".

Decision:

1. **Questionnaire** (`crp_analysis/nfr/questionnaire.json`, schema `crp-nfr-questionnaire-v1`):
   the 23 questions verbatim, each with a plain help text, whether only the team can answer it,
   and the profile fields (targets) that answer it. Aspects map to ISO/IEC 25010:2023
   characteristics.
2. **Evidence in slice 1** (`crp_analysis/nfr/signals.py`, `crp-nfr-signals-v1`), deterministic
   and cited:
   - libraries declared in `pom.xml` and `package.json`, from the dependency map's `depends_on`
     edges with manifest and line (for example Actuator, Resilience4j, OpenTelemetry, Flyway,
     caching, messaging, authentication, rate limiting, accessibility checks);
   - files in the upload (CI pipelines, Dockerfiles, runtime pins, OpenAPI and AsyncAPI files,
     runbooks, alert rules and dashboards, tests).
   - `supports` signals show intent; `context` signals (Kubernetes and Helm manifests,
     Terraform, application configuration, load-test scripts) are listed as "in the upload, not
     checked yet" until slices 2–3 assess them.
3. **Gaps** are the project's tracked issues, mapped to questions from the rule catalog's
   category and family (`questions_for_rule`, `crp-nfr-mapping-v1`). For example: secrets and
   access checks → unauthorised access; other security findings and vulnerable dependencies →
   malicious attacks; performance → latency (unbounded queries also growth and demand);
   correctness and reliability → consistency; maintainability → manual remediation; hub-like parts
   → fault tolerance. Every catalog rule maps to at least one question. Open issues are gaps;
   accepted risks are counted separately; resolved and false positives are not gaps.
4. **NFR profile** (`nfr_profile_versions`, migration 0013): 10 targets (availability, response
   time, page load, users, peak users, RTO, RPO, growth, cost of downtime, accessibility),
   regulations, platforms and attested answers or "does not apply" with a reason. Append-only
   versions with author and note; members save with the version they edited (409 on conflict).
   Identical profiles add no version.
5. **Status per question**, in order of precedence: not applicable, needs work (open issues),
   needs your input (only the team can answer and has not), evidence found, answered by your
   team, not checked yet. Evidence plus a missing team-only target is "needs your input".
6. **Serving**: `GET /v1/projects/{id}/nfr` computes on request from the newest upload whose
   review updated the issues; `PUT …/nfr/profile`; `GET …/nfr/export?format=csv|md`. UI tab
   "NFR readiness": status tiles, the team's targets, aspect cards with each question's evidence,
   gaps and answer editor; light, dark and 390 px.

Alternatives considered:

- A single readiness percentage: hides what is unknown and invites reading "unknown" as
  "fine". Counts per status are shown instead.
- Inferring targets from code (for example timeouts as latency targets): would present a guess
  as the team's requirement.
- Storing answers as tracked issues: answers are statements, not defects; they are versioned in
  the profile.

Consequences and migration/reversal approach:

- One new table (reversible migration). No change to reviews.
- Evidence depends on what the upload contains; infrastructure kept elsewhere is "not checked".
- Not in slice 1: custom questions, an HTML report (Markdown and CSV only), detectors that assess
  deployment manifests, configuration and code patterns (slices 2–3), measured answers (slice 4),
  the AI NFR analyst (slice 5).

Evidence and source/version references: "Questionnaire for Non functional requirements"
(Sridharrajdevelopment, Medium, 10 December 2023); ISO/IEC 25010:2023 (nine characteristics);
docs/NFR_ASSESSMENT.md; docs/validation/P12_REPORT.md.

Affected contracts, phases and tests: OpenAPI tag `nfr`; `test_nfr.py`, `test_nfr_api.py`,
`test_p12_nfr.py`, `e2e/p12-nfr.spec.ts`, fixture `fixtures/projects/nfr-mixed`.
