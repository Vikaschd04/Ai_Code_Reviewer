# NFR checkpoints — non-functional insight for an uploaded project (P12)

Status: implemented (10 October 2026). Checkpoints and their resolution: ADR 0024 (replaces the
NFR questionnaire of ADR 0021 at the owner's request). Configuration and infrastructure evidence:
ADR 0023. Validation: [P12_REPORT](validation/P12_REPORT.md). Phase prompt:
[prompts/P12_NFR_ASSESSMENT.md](../prompts/P12_NFR_ASSESSMENT.md).

## Goal

A team uploads its code (or connects a repository) and gets, for each non-functional requirement
that code and configuration can show:

- whether the project meets it, **with the evidence** (file and line) or **the issues** against it;
- **how to resolve it**, with steps for the project's stack, a focused fix workspace, automatic
  fixes where the change is narrow and known, and an optional AI plan with checked citations.

refactorX never turns "nothing found" into "requirement met", never treats checks that did not
run as clean, and never certifies compliance (PROJECT_BRIEF scope boundary).

## Areas and checkpoints

Six areas, from ISO/IEC 25010:2023 quality characteristics that code and configuration can speak
to. 28 checkpoints (`crp_analysis/insights/engine.py`):

| Area | Checkpoints (goal) | Evidence that counts |
|---|---|---|
| Security | No secrets in the code; untrusted input cannot reach dangerous calls; libraries have no known vulnerabilities; data access is checked and connections are encrypted; containers and settings are configured securely; no weak cryptography or unsafe constructs | Trivy (vulnerabilities, secrets, Dockerfile/Kubernetes/Helm misconfigurations), Opengrep, PMD, ESLint, PMD Apex, the `nfr` engine (Actuator exposure); authentication libraries |
| Reliability and availability | More than one instance runs; health checks restart and gate instances; releases and maintenance do not stop the service; remote calls are protected (calls without timeouts need attention); database changes are versioned and recoverable; platform versions and jobs are supported; errors are handled; automated tests protect behaviour | `nfr` engine (replicas, probes, Recreate, `ddl-auto`), Actuator and health probe settings, disruption budgets, graceful shutdown, Resilience4j/retry/timeout settings and libraries, migrations, backups, multi-zone (Terraform), tests |
| Performance and scalability | Containers declare CPU and memory requests and limits; database access stays efficient as data grows; no costly operations in hot code (including blocking calls in reactive code and unbounded thread pools); capacity follows demand | Trivy capacity checks, PMD/PMD Apex/Opengrep rules (queries in loops, unbounded queries, blocking reactive calls, cached thread pools), pool size and caching, autoscalers (HPA, KEDA) |
| Operations and monitoring | Metrics and alerts are in place; incidents can be diagnosed; changes ship through an automated pipeline | Metrics libraries, alert rules and dashboards, structured logging, tracing, error tracking, CI pipelines, runbooks |
| Architecture and maintainability | No cycles between parts; no hubs; stable parts do not depend on unstable ones; the code follows your architecture rules; code is easy to read and change | Architecture smells, architecture rules, code-quality rules |
| Experience and portability | Accessibility is checked in the build (web UIs); APIs are described for their consumers (HTTP APIs) | Accessibility lint and axe libraries; OpenAPI files and generators |

## Statuses

| Status | When | Shown as |
|---|---|---|
| Needs attention | Open tracked issues violate it; priority from the most severe | Card with the issues, steps and **Start fixing** |
| Not found | The mechanism it needs is not in the upload; catalog priority | Card with steps and **Handled outside this code?** |
| Handled elsewhere | Not found, and the team recorded how it is handled | The team's statement, with undo |
| In place | The upload shows the mechanism and no issue is open | Evidence with file and line |
| No issues found | The engines that look for it ran and report nothing open ("some files could not be checked" when one was partial) | Row in its area |
| Not checked | Those engines did not run, there is no review yet, or the configuration checks did not run for a Kubernetes checkpoint | Row in its area; never counted as passed |
| Not applicable | The upload has nothing it applies to (no Kubernetes workloads, no configuration, no platform code, no architecture rules, no web UI, no HTTP API) | Listed under "Not applicable here" |

Evidence makes a checkpoint applicable even when its condition is not met (for example a
multi-zone database in Terraform for "more than one instance runs"). An issue counts against the
first checkpoint that matches it by rule family, rule or engine; category catch-alls come last.

## Resolving

- **Steps**: each checkpoint lists how to resolve it; Spring Boot, Node.js and Kubernetes
  projects get stack-specific steps first.
- **Start fixing**: opens the upload's fix workspace with just the checkpoint's issues (up to 50).
- **Automatic fixes** for the configuration checks (`crp_analysis/fixes/config_recipes.py`):
  schema validation instead of `ddl-auto` changes, health details only for authorised users, a
  safe Actuator exposure list, rolling updates, two instances, a readiness probe. Each states the
  behaviour it changes and is checked by the validation ladder; the workspace check re-runs every
  engine on the changed copy.
- **Handled elsewhere**: for a missing mechanism, members record how it is handled outside the
  code; stored as versions with the author and shown as their statement.
- **Improvement plan (AI, optional)**: the advisor orders the checkpoints that need work; each
  step must cite checkpoints, facts or verified code, and use only numbers from what it cites.

## Evidence sources

- Declared libraries in `pom.xml` and `package.json`, with manifest and line (dependency map).
- Files in the upload (pipelines, Dockerfiles, runtime pins, API descriptions, runbooks, alert
  rules, dashboards, tests, Helm charts, Terraform, load-test scripts).
- Configuration read by the `nfr` engine (ADR 0023): Kubernetes replicas (overlays, kustomize and
  autoscalers considered), probes, rollout strategy, disruption budgets, autoscalers; Spring Boot
  Actuator, health probes, graceful shutdown, timeouts, pool sizes, Resilience4j; Terraform
  backups and multi-zone.
- Code patterns (P12 slice 3, owned Opengrep rules): outgoing calls without timeouts
  (`new RestTemplate()`, the JDK `HttpClient` without a connect timeout, `HttpURLConnection`
  without a read timeout, `axios.create` without `timeout`), blocking calls in methods that return
  `Mono`/`Flux`, unbounded cached thread pools. Platform rows come from the existing packs (SAP
  Commerce unbounded FlexibleSearch and saves in loops; Salesforce SOQL and DML in loops).
- Tracked issues of every engine (catalog rule families, categories and rules).
- The review's engine runs: which checks ran, completed or failed.

Material that refactorX does not assess is listed as "In the upload but not checked by
refactorX": Helm chart templates (replicas and probes), Terraform security settings (Trivy's
Terraform scanner downloads remote modules, ADR 0023), load-test scripts.

## Evaluation

- Every configuration rule has positive and negative examples (`fixtures/projects/nfr-config`),
  and so does every code-pattern rule (`fixtures/projects/nfr-code`);
  every recipe is shown to remove its issue on a re-check without breaking the file.
- Checkpoint statuses are tested per status, including checks that did not run and reviews where
  the configuration checks were missing.
- Precision and recall per detector on real systems are not measured yet.

## Limits

- Code and configuration show mechanisms, not behaviour under load: no checkpoint says the system
  is fast or available in production.
- Values set outside the upload (pipelines, other repositories, environment variables, a config
  server, the platform) are not visible; "handled elsewhere" records the team's statement.
- Targets (availability, latency, RTO, RPO) and regulations are not collected; whether evidence
  is enough for a target needs measurements, which refactorX does not import yet.
