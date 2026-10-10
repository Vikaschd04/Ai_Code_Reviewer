# ADR 0023 — Configuration and infrastructure evidence (P12 slice 2)

Status: accepted; implemented and verified (unit tests, real Trivy runs, a real-stack review).
The questionnaire mapping and the four guidelines of decision 4 now live in the NFR checkpoints
(ADR 0024).
Date: 10 October 2026. Owner: repository owner (P12 plan, docs/NFR_ASSESSMENT.md "New evidence
detectors"); implemented by the development agent.

Context and constraints:

- Slice 1 listed Kubernetes and Helm manifests, Terraform and application configuration as "in
  the upload, not checked yet". The questionnaire's availability, scalability, recoverability and
  security questions depend on what those files say.
- Findings need evidence and provenance; nothing may be rendered, executed or downloaded from a
  customer upload (AGENTS.md: isolate analyzer execution; snapshots are untrusted).
- Trivy 0.69.3 (already pinned, ADR 0007) embeds 563 misconfiguration checks (trivy-checks, MIT)
  and can run them offline with `--skip-check-update`; verified on 10 October 2026.
- **Verified risk:** Trivy's Terraform scanner downloads remote modules named in the scanned code
  (`module { source = ... }`) even with `--offline-scan`: a trial scan fetched
  `terraform-aws-modules/vpc/aws` from the registry into `$TMPDIR/.aqua/cache`. In 0.69.3 the
  Terraform parser defaults to `allowDownloads: true` and `trivy fs` has no flag to turn it off
  (`--tf-exclude-downloaded-modules` only hides findings from downloaded modules). A module source
  can name any host (`git::`, `s3::`, HTTP), so scanning Terraform with Trivy would let uploaded
  code make the worker contact arbitrary hosts.

Decision:

1. **Trivy misconfigurations** (`engines/trivy.py`, same run as vulnerabilities and secrets):
   `--scanners vuln,secret,misconfig`, `--misconfig-scanners
   dockerfile,kubernetes,helm,cloudformation,azure-arm`, `--skip-check-update` (embedded checks of
   the pinned binary only; the hash of the rule set includes the binary version and the scanner
   list).
   - **Terraform is not scanned by Trivy** (risk above). Terraform security settings are listed as
     "in the upload; security settings are not checked".
   - Every Trivy run gets an unreachable HTTP proxy (`HTTP(S)_PROXY=http://127.0.0.1:9`) as a
     second guard; `TMPDIR` stays the run's own folder.
   - Helm charts are rendered by Trivy with their own values; charts that cannot be rendered (for
     example a dependency that is not vendored) are listed in `diagnostics.misconfig_unrendered`,
     never treated as clean. Chart dependencies are not downloaded (verified).
   - Findings: rule id `misconfig:<check>` (for example `misconfig:KSV-0017`), category security,
     Trivy's severity, title, description, resolution and link as guidance, cause lines when
     Trivy reports them (otherwise a file anchor), identity from check, resource and message (it
     names the container), so issues survive line moves. Code excerpts are dropped from the
     stored report.
2. **Configuration checks** (engine `nfr`, `engines/nfr.py` and `crp_analysis/nfr/config.py`,
   rule set `crp-nfr-config-v1`, in-process, YAML via PyYAML's safe composer as data):

   | Rule | Severity | When |
   |---|---|---|
   | `crp.nfr.k8s.single-replica` | medium | a Deployment or StatefulSet can run with fewer than 2 instances: the largest value any document declares (base, overlay patch, kustomization `replicas`) is below 2, or replicas is not set; when an autoscaler targets it, its minimum decides |
   | `crp.nfr.k8s.no-readiness-probe` | medium | a container that declares a port has no readiness probe in any document of the workload |
   | `crp.nfr.k8s.recreate-strategy` | low | a Deployment uses strategy `Recreate` |
   | `crp.nfr.spring.actuator-exposed` | medium | `management.endpoints.web.exposure.include` exposes heapdump, env, configprops or threaddump (directly or with `*`, minus `exclude`) |
   | `crp.nfr.spring.health-details-public` | low | `management.endpoint.health.show-details=always` |
   | `crp.nfr.spring.schema-auto-update` | medium; high for create and create-drop | `spring.jpa.hibernate.ddl-auto` (or `hibernate.hbm2ddl.auto`) is create, create-drop or update |

   - Spring Boot: `application*` and `bootstrap*` properties and YAML; keys compared in relaxed
     form; files and documents for development-like profiles (dev, local, test, it, e2e, ci, h2,
     demo, ...) and test resources are ignored; `${...}` placeholders and templated values are
     not judged.
   - Coverage: templates that only become YAML at deploy time are NOT_ATTEMPTED ("template");
     unreadable YAML is FAILED; files over the intake text limit are NOT_ATTEMPTED. Any of these
     makes the run PARTIAL. Anchors are bounded (20,000 nodes per file) and recursion is caught.
   - Duplicates with Trivy are avoided: CPU and memory requests and limits come from Trivy's
     checks (KSV-0011, -0015, -0016, -0018); the `nfr` engine does not repeat them.
3. **Supporting evidence with file and line**, stored in the `nfr` run's
   `diagnostics.signals` (count and up to five locations): more than one instance, autoscaling
   (HPA, KEDA), pod disruption budgets, Kubernetes probes, graceful shutdown, timeouts, connection
   pool size, Actuator health probes, Resilience4j circuit breakers and retries, and two Terraform
   signals read line by line (backups, multi-zone). The NFR assessment reads them for the
   reviewed upload (`crp-nfr-signals-v2`); when the `nfr` engine did not run (older reviews) or
   did not complete, the slice 1 "not checked yet" context stays.
4. **Questionnaire and insights**: mapping `crp-nfr-mapping-v2` (single instance → continuous
   availability and fault tolerance; probes → continuous availability and recovery time; Recreate
   → continuous availability; schema changes → data recovery and consistency; Actuator exposure →
   access; Trivy capacity checks → attacks and demand). Insights `crp-insights-v2` add four
   guidelines: deployments can go down, the schema changes automatically, containers have no
   requests and limits, insecure container and settings configuration.

Alternatives considered:

- Trivy for Terraform with network blocked by the host: rejected for now; local and lite hosts
  have no network sandbox, and `git::ssh` sources bypass HTTP proxies. Revisit when the sandbox
  compute decision (P07) gives every analyzer a no-network namespace, or when Trivy offers a flag
  to disable downloads.
- Checkov (Apache-2.0) or KICS for Terraform: not adopted in this slice; each is a new engine to
  pin and verify (licence, version, offline behaviour) and would need the same no-download proof.
- Our own replica and probe rules inside Trivy as custom Rego checks: rejected; repository-level
  overlays and autoscalers span files, which Trivy's per-file checks do not see, and custom checks
  would need their own evaluation.
- Liveness probes as a gap: rejected; Kubernetes does not require them and badly tuned liveness
  probes cause restarts. Present probes count as evidence.

Consequences and migration/reversal approach:

- No migration. Existing reviews keep their evidence; the next review adds the `nfr` engine and
  Trivy's misconfiguration findings. Trivy's rule-set hash changes once (binary version and
  scanners added), so absences of earlier Trivy issues in the next review are classified with the
  rule change, not as fixed.
- A typical Kubernetes Deployment without a security context produces many low and medium Trivy
  findings (Pod Security Standards); the insights group them into one recommendation.
- Reversal: drop `misconfig` from the scanner list and `nfr` from `ENGINE_NAMES`; nothing else
  depends on them.

Evidence and source/version references:

- docs/validation/P12_REPORT.md (configuration evidence section).
- Trivy 0.69.3 Terraform parser default `allowDownloads: true`:
  https://github.com/aquasecurity/trivy/blob/v0.69.3/pkg/iac/scanners/terraform/parser/parser.go;
  misconfiguration options: https://github.com/aquasecurity/trivy/blob/v0.69.3/pkg/misconf/scanner.go.
- Embedded checks for air-gapped use: https://trivy.dev/docs/latest/advanced/air-gap/;
  trivy-checks licence MIT: https://github.com/aquasecurity/trivy-checks.
- Kubernetes probes: https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/;
  Deployments and strategies: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/.
- Spring Boot Actuator endpoints:
  https://docs.spring.io/spring-boot/reference/actuator/endpoints.html; database initialization:
  https://docs.spring.io/spring-boot/how-to/data-initialization.html.

Affected contracts, phases and tests: engine `nfr` in `ENGINE_NAMES` (scans, reports, Issues
filter), catalog `crp-rules-v5`, Trivy diagnostics `misconfig_scanners`, `misconfig_unrendered`,
`config_files`; `test_nfr_config.py`, `test_security_engines.py`
(`test_trivy_misconfigurations_use_embedded_checks_and_never_download`), `test_p12_config.py`,
updated `test_p12_nfr.py`; fixtures `nfr-config` (new) and `nfr-mixed` (real manifest and Spring
settings).
