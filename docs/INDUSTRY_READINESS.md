# Industry readiness plan

Status: plan and self-assessment, 10 October 2026. Evidence comes from the phase reports,
SECURITY_MODEL.md, OPERATIONS.md and memory/KNOWN_ISSUES.md; nothing here is a release claim.
The release gate stays P07 (prompts/P07_PRODUCTION_HARDENING.md).

## The problem we solve, and what still stands between us and industry use

The product reviews enterprise Java, JavaScript/TypeScript, SAP Commerce and Salesforce code
from an upload or a repository, shows evidence-backed issues, and helps fix them safely
(PROJECT_BRIEF.md). Today it does this on one machine for one team, with real engines and
honest coverage. Before industry use, four things are missing:

1. **Proof on real code.** Accuracy is measured on synthetic fixtures; live AI quality, domain
   expert review of the SAP Commerce and Salesforce packs, and fixes verified by a build and
   tests are still open (K-P03-01, K-P04-01, K-P05-01).
2. **A production service.** Single instance, shared access token, no backups, no metrics or
   alerts, engines without an OS sandbox (K-P09-01, K-P00-06, K-P01-01; OPERATIONS.md).
3. **Answers beyond code defects.** Teams also need to know whether a system meets its
   non-functional requirements (performance, availability, recoverability, …). This is new phase
   P12 (docs/NFR_ASSESSMENT.md).
4. **An experience for a whole organisation:** a home page that says what to do next, reports
   for stakeholders, integrations with planning and chat tools, measured accessibility.

## refactorX answers its own NFR questionnaire

The same 24 questions P12 will answer for customers, answered for refactorX (state as in
NFR_ASSESSMENT.md).

| Aspect | What exists (evidence) | Gaps | State |
|---|---|---|---|
| Performance | Background jobs with live progress; paginated APIs; benchmark on a 1,010-file synthetic project: cold scan 8.7 s, warm 4.1 s, API pages 7–37 ms (P02_REPORT); architecture analysis of 50,000 files in about 7 s (ADR 0018, 0020) | Main web bundle 806 KB minified (239 KB compressed), no code splitting; no measured page load; no concurrent-user load test; no published capacity per tier | Measured (single machine), gaps open |
| Security and compliance | Workspace isolation (404 for others), roles, no project code executed, secrets never stored, redacted JSON logs, signed webhooks, YAML/XML parsed as data, AI off by default with masked excerpts (SECURITY_MODEL.md) | Shared access token in hosted mode, no SSO; engines without an OS sandbox; heuristic secret masking; no external penetration test; LGPL review for psycopg; no compliance certification (out of scope by design) | Evidence plus gaps |
| Recoverability | Durable, resumable workflows (Temporal); immutable, content-addressed uploads; lite profile re-runs an interrupted engine | No backup or restore, no RPO/RTO, no restore test (OPERATIONS.md) | Gap |
| Maintainability and operational excellence | Structured, redacted logs; health and readiness endpoints; System status page; `make doctor`; CI on every push; 569 automated tests and 20 browser journeys | No metrics, tracing, dashboards or alerts; no runbooks with named operators; no on-call | Evidence plus gaps |
| Reliability | Explicit failure and incomplete states (never shown as clean); timeouts and retries; resumable jobs | No soak or chaos tests; hosted mode uses the Temporal development server | Evidence plus gaps |
| High availability | Stateless API and workers by design; state in PostgreSQL and the artifact store | Single instance; no managed, replicated database; no Temporal cluster | Gap |
| Scalability and elasticity | Workers can be added (Temporal task queues); per-file result cache; bounded engine resources | No autoscaling, fair scheduling or per-tenant quotas (demo quotas only); not load-tested | Gap |
| Usability and accessibility | Reviewer-first UI in plain language, technical details collapsed, light, dark and 390 px screenshots in every journey | No automated WCAG checks (no axe), no keyboard-only audit, no user research, English only | Evidence plus gaps |
| Portability and interoperability | Linux x86_64 container image built and smoke-tested in CI; macOS arm64 development; OpenAPI contract; JSON and SARIF exports; patches for `git apply`/`git am`; GitHub App | Engines pinned for macOS arm64 and Linux x86_64 only; no GitLab, Bitbucket or Azure DevOps; no Jira or Azure Boards export | Evidence plus gaps |

When P12 exists, refactorX will run it on its own repository and publish the result with each
release (dogfooding).

## Readiness tracks

### A. Prove it on real code (the main problem statement)

1. Real-code evaluation set: open-source Java, TypeScript, SAP Commerce and Salesforce projects
   (licence permitting) with expert-labelled findings, smells and NFR answers; precision and
   recall per engine and detector in every release.
2. Live AI evaluation with the owner's key (P03 gate), then AI fix and NFR analyst quality.
3. Domain expert review of the SAP Commerce and Salesforce packs (owner arranges).
4. Fixes verified by compilation and tests in sandboxes (P09 Tiers 1–2, owner's compute).
5. Deeper code understanding: type-resolved dependencies (TypeScript compiler, Java in the
   sandbox), call edges, Apex in the dependency map; more fix recipes.

### B. Run it like a production service (P07; owner decisions marked)

| Area | Work | Owner decision |
|---|---|---|
| Identity and access | OIDC/SAML single sign-on, SCIM provisioning, per-user sessions with revocation, audit log of administrative actions | Identity provider |
| Tenancy and isolation | Tenant isolation tests; engines and builds in OS-level sandboxes with no network or secrets | Sandbox compute (shared with P09) |
| Data protection | Encryption at rest, retention and deletion jobs (intake expiry, blob clean-up), customer-managed retention settings | Retention policy |
| Recoverability | Managed PostgreSQL with point-in-time recovery, artifact-store replication, scripted and tested restore, RPO/RTO | Hosting tier |
| Availability and scale | At least two API and worker instances, Temporal cluster or Temporal Cloud, autoscaling workers, fair scheduling and per-tenant quotas, load tests with published capacity | Hosting tier, budget |
| Operations | OpenTelemetry metrics and traces for refactorX itself, dashboards, alerts, status page, runbooks, on-call, incident process | Operators |
| Release | Supported-version matrix, upgrade and rollback tests, Linux arm64 qualification, licence review, threat model and external penetration test, security reporting | Vendor for the penetration test |

### C. Experience uplifts

1. **Project home "health at a glance":** code quality, security, architecture and NFR
   readiness on one page, with trends across reviews and "the 5 things to do next".
2. **Prioritised work queue:** risk = severity × change frequency (P10 slice 4) × reachability
   (graph) × NFR impact; "fix this sprint" list and bulk triage.
3. **NFR readiness and questionnaire flow** (P12): what the code shows, what is missing, what we
   need from you.
4. **Stakeholder reports:** one click to an architecture and NFR report (HTML, Markdown, later
   PDF) with an executive summary.
5. **Integrations:** Jira and Azure Boards export, Slack and Teams notifications, GitLab,
   Bitbucket and Azure DevOps sources; an IDE extension later.
6. **Onboarding:** guided first review, SAP Commerce and Salesforce samples, explanations of
   terms in place.
7. **Speed:** split the web bundle, virtualise long lists, server-side search, saved filters, a
   command palette.
8. **Accessibility:** axe checks in every browser journey (after a licence check), keyboard-only
   audit, WCAG 2.2 AA as the target.
9. **"Why it matters":** each finding tied to the NFR aspect it affects, with fix examples.
10. **Trust signals:** evidence badges (confirmed, likely, potential, needs input) and an
    exportable audit trail.

## Recommended order

1. **Next phase: P12 NFR assessment, slice 1** (questionnaire, profile, tagging of existing
   findings, readiness view, export). No owner input needed. Then slices 2–3 (infrastructure,
   configuration and code-pattern evidence).
2. **Alongside, uplifts that need no owner input:** project home, prioritised queue, web bundle
   splitting, axe checks, OpenTelemetry for refactorX, backup and restore scripts with a restore
   test on the local database.
3. **P10 slices 4–5** (Git-history hotspots, runtime evidence import): they turn "potential"
   into "measured" for both P10 and P12.
4. **P07 track as soon as the owner decides:** hosting tier, identity provider, retention policy,
   sandbox compute; plus the AI key and the GitHub App for the P03 and P06 live gates.

Sources for the questionnaire and quality model: "Questionnaire for Non functional requirements"
(Sridharrajdevelopment, Medium, 10 December 2023); ISO/IEC 25010:2023 product quality model
(nine characteristics, as summarised by [SonarSource](https://www.sonarsource.com/resources/library/iso-iec-25010-explained/)
and [arc42 quality](https://quality.arc42.org/standards/iso-25010)); Trivy built-in
misconfiguration checks embedded in the binary for offline use
([Trivy air-gap documentation](https://trivy.dev/docs/latest/advanced/air-gap/)).
