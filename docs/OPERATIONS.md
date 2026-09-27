# Operations and release responsibilities

Status: future runbook contract. Populate actual commands, owners, environments and evidence during implementation; never invent working recovery procedures.

Monitor intake rejection/failure, queue age, scan/engine outcomes, coverage gaps, runtime/memory/disk, model rate limits, token/cost usage, cancellation/cleanup failures and tenant fairness. Logs are structured with request/scan/engine IDs and redacted by default. Source text is not ordinary telemetry.

Use durable checkpoints and idempotent task/result publication. Retry only classified transient errors with bounded backoff; malformed input and policy rejections do not loop. Enforce hard quotas before expensive work, preserve partial results and report budget exhaustion. Jobs must terminate descendants on cancellation.

Keep a compatibility registry of worker images, engine/rulepack schemas, graph schemas and provider models. Stage upgrades against fixtures/holdouts; allow rollback. Data migrations need backup/restore or forward-recovery plans.

Before production define and test backup/restore, retention/deletion, access revocation, incident triage, job recovery, credential rotation, worker quarantine, tenant evacuation and capacity planning. Select RPO/RTO/SLO targets with owners after pilot measurements; no numerical guarantee is supplied here.

Provide deployment modes accurately: local development; hosted analysis with explicit source upload; later hosted control plane/private runner; fully private if qualified. Document network flows and what source/results leave each boundary. An optional deployment is not supported until tested.


## Local development operations (implemented in P00)

| Task | Command / location |
|---|---|
| Health | `make doctor`; `GET /v1/health/live`; authenticated `GET /v1/health/ready` |
| Logs | `make dev` prints prefixed API/worker/web logs (API/worker log JSON or text per `CRP_LOG_FORMAT`, redacted); PostgreSQL `.local/logs/postgres.log`; Temporal `.local/logs/temporal.log`; failed E2E runs copy logs to `.local/logs/e2e-*` |
| Start/stop infrastructure | `make infra-up` / `make infra-down` / `make infra-status` |
| Schema | `make migrate`; readiness reports `schema_out_of_date` when behind |
| Workflow inspection | Temporal UI http://127.0.0.1:8233 (dev server only) |
| Reset | see INSTALLATION.md "Local state and reset" |
| Credential rotation | delete `.local/secrets/local-api-token`, run `make bootstrap`; all sessions become invalid |

No backup/restore, retention job, alerting or capacity figure exists yet; none is claimed.
