# NFR assessment — answering non-functional requirement questionnaires with evidence (P12)

Status: slice 1 implemented (10 October 2026; ADR 0021, validation/P12_REPORT.md): questionnaire,
profile, evidence from declared libraries and files, gaps from tracked issues, readiness view, CSV
and Markdown exports. Slices 2–6 planned. Phase prompt:
[prompts/P12_NFR_ASSESSMENT.md](../prompts/P12_NFR_ASSESSMENT.md).

## Goal

A team uploads its code (or connects a repository) and gets an honest answer to a non-functional
requirements (NFR) questionnaire for that system:
- what the code and configuration **show** (with file and line);
- what **contradicts** the requirement (tracked as issues);
- what was **measured** (imported load tests, traces, scans);
- what only the **team** can answer (targets, user numbers, regulations, cost of downtime).

refactorX never turns "nothing found" into "requirement met", and never certifies compliance
(PROJECT_BRIEF scope boundary). It produces an evidence pack and a readiness view that the team
completes and signs off.

## Questionnaire and quality model

The built-in questionnaire starts from the owner-supplied list ("Questionnaire for Non functional
requirements", Sridharrajdevelopment, Medium, 10 December 2023): 9 aspects, 23 questions. Each
aspect is mapped to ISO/IEC 25010:2023 product quality characteristics, so that reports use a
recognised vocabulary and teams can add their own questions.

| # | Aspect (questionnaire) | ISO/IEC 25010:2023 characteristic |
|---|---|---|
| 1 | Performance | Performance efficiency |
| 2 | Security and compliance | Security (compliance stays the team's attestation) |
| 3 | Recoverability | Reliability (recoverability) |
| 4 | Maintainability and operational excellence | Maintainability |
| 5 | Reliability | Reliability (faultlessness, fault tolerance) |
| 6 | High availability | Reliability (availability) |
| 7 | Scalability and elasticity | Flexibility (scalability) |
| 8 | Usability and accessibility | Interaction capability |
| 9 | Portability and interoperability | Flexibility (adaptability, installability), Compatibility (interoperability) |

ISO/IEC 25010:2023 also has functional suitability and safety; they are available as optional
question sets for projects that need them.

## Answer states (per question)

| State | Meaning | Source |
|---|---|---|
| Supported by evidence | The code or configuration contains the mechanism (for example health checks, retries with backoff, autoscaling). It shows intent, not that it works at run time | Detectors, cited |
| Gap found | Evidence of the opposite (for example an HTTP client without a timeout, a single replica). Each gap is a tracked issue | Detectors, cited |
| Measured | Imported evidence gives numbers, compared with the team's target (met, missed) | Load-test, trace, APM, accessibility or DAST reports |
| Needs team input | Business or environment facts that code cannot hold (expected users, growth, regulations, RTO/RPO, cost of downtime) | Team answers, attested and versioned |
| Not determinable | Nothing in the upload either way (for example the infrastructure code was not uploaded). Shown as "not checked", never as fine | — |
| Not applicable | Declared by the team with a reason (versioned, audited) | Team |

A question can hold several states at once (evidence for retries, a gap for missing timeouts,
and a team-supplied latency target). Each aspect's readiness shows the count per state and
whether targets are met. There is no single "percent compliant" score.

## What refactorX can check, per question

"Code and config" items are deterministic detectors (existing engines first). "Measured" needs an
imported file. "Team" is asked in the portal.

### 1. Performance

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Load time / pre-load time | Front-end weight signals: large dependencies, no code splitting (dynamic import), large assets | Lighthouse or Web Vitals reports | Target (for example LCP at p75) |
| Access pattern: users, concurrency | Thread and connection pool settings (Spring, HikariCP), rate limits, session handling | Access-log or APM summaries | Expected and peak users |
| Network latency per interaction | Calls without timeouts, synchronous call chains, N+1 queries (JPA, SOQL, FlexibleSearch), missing pagination, no caching | Trace span durations (OpenTelemetry) | Latency targets |
| Projected growth | Unbounded queries (`findAll`), whole tables loaded in memory, missing indexes in migrations (Flyway, Liquibase) | Data volume statistics | Growth projection |

### 2. Security and compliance

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Unauthorised access | Authentication and authorization setup (Spring Security, method security), unprotected endpoints, Apex CRUD/FLS checks (P04), hard-coded credentials (Trivy secrets), cookie flags | Penetration-test or DAST report (for example OWASP ZAP) | Identity provider, roles |
| Malicious attacks | SAST (Opengrep, PMD), vulnerable dependencies (Trivy), infrastructure misconfiguration (Trivy misconfiguration checks), security headers, CSRF, input validation | DAST report | WAF, DDoS protection |
| Laws, regulations, audit | Evidence for selected controls: audit logging, encryption of personal data, retention and deletion jobs | — | Applicable regulations (GDPR, PCI DSS, BaFin, …) and sign-off. Never certified by refactorX |

### 3. Recoverability

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Recover from an outage | Idempotent handlers and consumers, retries with backoff, dead-letter queues, outbox pattern; infrastructure restart policies | Incident records | DR plan |
| Minimise recovery time | Health and readiness endpoints (Spring Boot Actuator), rolling deployments, automated failover, a CI/CD pipeline in the repository | Mean time to recover | RTO |
| Recover lost data | Backups and point-in-time recovery in infrastructure code, reversible migrations | Restore-test records | RPO, backup test cadence |
| Cost of downtime | — | — | Business impact (team only) |

### 4. Maintainability and operational excellence

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Continuous monitoring and alerts | OpenTelemetry, Micrometer or Prometheus libraries; structured logs; correlation ids; alert rules and dashboards kept in the repository | — | Alert routing, on-call |
| Support for manual remediation | Runbooks and docs in the repository, admin operations and feature flags; maintainability evidence (architecture smells, rule breaches, PMD design rules, tests present) | — | Support model |

### 5. Reliability

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Consistent behaviour | Timeouts, retries, circuit breakers and bulkheads (Resilience4j), resource limits, error-handling defects (empty catch, swallowed exceptions), tests and coverage configuration | Error rates and latency percentiles | SLOs |
| Inspect and correct glitches | Logging with context, tracing, error tracking (for example Sentry), feature flags | Incident data | Process |

### 6. High availability

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Continued availability | Replicas ≥ 2, disruption budgets, anti-affinity, multi-zone databases, load-balancer health checks; statelessness blockers (in-memory sessions, local files, schedulers that must run once) | Uptime history | Availability target |
| Fault tolerance | Circuit breakers, fallbacks, queue decoupling, graceful shutdown; architecture: hubs and cycles as shared failure points, synchronous chains | — | — |

### 7. Scalability and elasticity

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Growing demand | Horizontal-scaling blockers (local state, static caches, sticky sessions), data-access patterns, caching, asynchronous processing; Salesforce governor-limit patterns (P04), SAP cron job batching | — | Expected growth |
| Sudden spikes | Autoscaling (Kubernetes HPA, cloud autoscaling), queue buffering, rate limiting and backpressure, pool limits | Load-test reports (k6, JMeter, Gatling) | Peak expectations |

### 8. Usability and accessibility

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Simple to use | Little from code: consistent validation messages, internationalization present | Usability test results | Goals |
| Seamless, accessible experience | Accessibility lint (JSX and LWC accessibility rules, after a licence check), missing alternative text, ARIA misuse | axe or Lighthouse accessibility reports | WCAG level (for example 2.2 AA) |

### 9. Portability and interoperability

| Question | Code and config | Measured | Team |
|---|---|---|---|
| Target operating systems and platforms | Dockerfiles and base images, OS-specific code and paths, runtime version pins (Java, Node), native dependencies, cloud-provider lock-in (SDK use) | — | Supported platforms |
| Data exchange between tiers | API descriptions (OpenAPI, AsyncAPI), serialization formats, message brokers, API versioning; SAP integration objects and Salesforce APIs (P04 maps) | — | Integration partners |

## How it fits the platform

- **Existing findings are tagged** with NFR aspects through the rule catalog (for example Trivy
  vulnerabilities → security, empty catch → reliability, SOQL in loops → performance and
  scalability, cycles and hubs → maintainability).
- **New evidence detectors** (deterministic, versioned): infrastructure (Kubernetes, Helm,
  Terraform, Dockerfile, using Trivy's misconfiguration checks offline once verified), framework
  configuration (Spring Boot properties and YAML, HikariCP, Actuator, Resilience4j), observability
  and logging libraries from manifests, and code patterns (timeouts, unbounded queries, blocking
  calls). Gaps become findings of an `nfr` engine with the normal lifecycle; supporting evidence
  is stored with file and line.
  - **Delivered in slice 2 (ADR 0023):** engine `nfr` (Kubernetes: single instance with overlays,
    kustomizations and autoscalers considered, readiness probes, Recreate; Spring Boot: sensitive
    Actuator endpoints, public health details, `ddl-auto` schema changes) and Trivy's embedded
    misconfiguration checks for Dockerfile, Kubernetes, Helm, CloudFormation and Azure ARM.
    **Terraform is not scanned by Trivy**: its scanner downloads remote modules named in the code
    even offline (verified on 0.69.3); only two Terraform signals (backups, multi-zone) are read,
    and Terraform security settings are listed as not checked. Helm templates are not checked by
    the `nfr` engine (Trivy renders charts with their own values; unrenderable charts are listed).
  - Still to come (slice 3): code patterns (timeouts in code, unbounded queries, blocking calls).
- **NFR profile per project**: targets (availability, latency, RTO, RPO, peak users, growth),
  regulations and platforms, plus the team's attested answers. Versioned and audited like the
  architecture rules (ADR 0019).
- **Measured evidence** shares the importer with P10 slice 5 (bounded, validated files; nothing
  live): OpenTelemetry traces, k6/JMeter/Gatling summaries, Lighthouse and axe reports, OWASP ZAP
  reports.
- **AI NFR analyst** (optional, policy-gated, labelled): drafts the narrative answer per question
  from cited evidence; uncited claims are rejected; numbers only come from tools. The team
  approves.
- **Outputs**: NFR readiness view per project and per review (with trends), questionnaire export
  (CSV, spreadsheet), stakeholder report (HTML and Markdown, PDF later), SARIF for gaps.

## Evaluation

- Every detector has positive and negative fixtures (Java, TypeScript, SAP Commerce, Salesforce,
  infrastructure files). Precision and recall are measured per detector on a labelled set.
- Question-level accuracy: labelled synthetic systems with the expected state of each question.
- Real systems with expert-labelled answers when available. No accuracy claim without these
  measurements.

## Limits

- Code shows mechanisms, not behaviour under load: performance and availability numbers come
  only from measurements or team targets.
- Infrastructure not in the upload cannot be judged ("not determinable").
- Compliance is the team's attestation backed by an evidence pack, never a certification.
