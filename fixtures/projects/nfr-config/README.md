# NFR configuration fixture

Synthetic deployment and configuration files for the configuration checks (engine `nfr`) and
Trivy's misconfiguration checks (P12 slice 2, ADR 0023). Every rule has a positive and a negative
example; `packages/analysis/tests/test_nfr_config.py` and `test_security_engines.py` hold the
expected results.

| File | Expected |
|---|---|
| `deploy/base/shop.yaml` | single instance (replicas 1), Recreate, no readiness probe on `shop`; none on the `metrics` sidecar (no port); Trivy: privileged container |
| `deploy/base/api.yaml` | no finding: probes present; replicas 1 raised to 3 by `deploy/overlays/prod/kustomization.yaml` |
| `deploy/base/orders.yaml` + `autoscaling.yaml` | no finding: the autoscaler's minimum is 3; a disruption budget |
| `deploy/base/cache.yaml` | single instance: replicas 3, but its autoscaler allows 1 |
| `deploy/base/worker.yaml` | single instance: replicas not set and no autoscaler; no readiness finding (no port) |
| `deploy/broken.yaml` | not valid YAML: failed, never clean |
| `chart/` | template: not attempted; Trivy cannot render it (its dependency is not vendored) and downloads nothing |
| `src/main/resources/application.yml` | Actuator exposes `*`, health details always, `ddl-auto: update`; its `dev` document (`create-drop`) is ignored; signals: graceful shutdown, health probes, pool, timeout, circuit breaker |
| `src/main/resources/application-prod.properties` | `ddl-auto=create` (high); exposure `health,info` is fine |
| `src/main/resources/application-local.properties` | ignored (development profile) |
| `src/test/resources/application.yml` | ignored (test resources) |
| `infra/database.tf` | signals: backups, multi-zone (Terraform security settings are not checked) |
| `Dockerfile` | Trivy: runs as root, no HEALTHCHECK |
| `services/hardened/Dockerfile` | Trivy: neither |
