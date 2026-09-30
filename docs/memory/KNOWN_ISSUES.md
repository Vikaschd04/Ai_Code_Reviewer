# Known issues and gaps

Updated 30 September 2026 (P02 + hosted deployment). None of these block the P00–P02 gates.

| ID | Symptom / gap | Scope | Severity | Evidence | Workaround / next action |
|---|---|---|---|---|---|
| K-P00-01 | Only macOS arm64 verified | all commands | Medium | P00_REPORT environment | Verify on Linux (BACKLOG P00-F2) |
| K-P00-02 | Temporal is the CLI dev server (SQLite in hosted standard mode); the lite profile has no Temporal at all | deployment | Medium | ADR 0005, 0009, 0010 | Qualify hosted Temporal in P07 |
| K-P00-03 | psycopg/psycopg-binary are LGPL-3.0 | distribution | Low (dev) | TOOLCHAIN.md | License review before shipping images/binaries (P00-F4) |
| K-P00-04 | Git initialized 2026-09-27 (github.com/Vikaschd04/Ai_Code_Reviewer); `/security-review` and `/code-review` not run yet | process | Low | git log | Run the reviews on the branch (P00-F1) |
| K-P00-05 | Worker readiness may report OK for up to 90 s after a worker stops (Temporal poller freshness) | readiness | Low | readiness.py, INSTALLATION troubleshooting | Diagnostic workflow proves execution; tune `CRP_WORKER_POLLER_FRESH_SECONDS` |
| K-P00-06 | Browser sessions cannot be revoked individually | local auth | Low | ADR 0004 | Rotate the token file; hosted IdP in P07 |
| K-P01-01 | Engines run without an OS-level sandbox (no cgroup/VM/egress block) on macOS dev hosts | engine execution | Medium | ADR 0006 | Wall-time, output and heap caps, scrubbed env, read-only copies; add container/VM isolation before multi-tenant use (P01-F4) |
| K-P01-02 | Secret masking in source excerpts is heuristic | UI/API excerpts | Medium | `crp_analysis/redaction.py` | Keep secrets out of uploads (runner excludes known secret files); dedicated secret scanning later |
| K-P01-03 | No scheduled intake expiry or blob GC | storage | Low | P01_REPORT | Expiry runs on intake creation; delete `~/.local/share/code-review-platform/artifacts` to reset dev data (P01-F2) |
| K-P01-04 | Browser folder selection and registered mounts not implemented | intake | Low | FEATURE_MATRIX | Use ZIP or `crp-runner capture` (P01-F1) |
| K-P00-07 | Homebrew `postgresql@18` post-install needs Homebrew ≥ 7 | setup | Low | INSTALLATION troubleshooting | `brew update && brew postinstall postgresql@18` |
| K-P02-01 | `engine_cache` grows until the project is deleted (no eviction) | storage | Low | ADR 0008 | Delete the project or truncate `engine_cache`; eviction is P02-F1 |
| K-P02-02 | Offline Trivy DB goes stale (NextUpdate passes daily); refresh is manual in local development (hosted deployments refresh daily, ADR 0010); a refresh changes Trivy's rule-set hash so absent vulns become UNKNOWN | dependency findings | Medium | doctor warning, engine card | `make engines` (~1.3 GB download); P02-F2 |
| K-P02-03 | Graph is syntax-level: no classpath/type-checker resolution, no call edges; Gradle, parent POMs, tsconfig `extends`, `exports` fields not followed | graph/impact | Medium | P02_REPORT gaps | Unresolved counts shown in UI/API; P02-F3 |
| K-P02-04 | Opengrep/Trivy pinned for macOS arm64 and Linux x86_64 only | engines | Low | engines.py | Add more platform pins when needed (P02-F4) |
| K-P02-05 | Findings of scans made before migration 0003 have no issue link | UI/API | Low | dev-stack check | Rescan; backfill is P02-F5 |
| K-P09-01 | Hosted mode is single-user, single-instance (shared access token, Temporal dev server on SQLite, one disk) | hosted deployment | Medium | ADR 0009 | Do not share the URL/token; P07 brings SSO, managed Temporal, isolation |
| K-P09-02 | Live deployment not yet created by the owner (free: Render Blueprint `render.yaml` or GitHub Codespaces; paid: `deploy/render-standard.yaml`) | deployment | Info | DEPLOYMENT.md | Follow DEPLOYMENT.md |
| K-P09-04 | Codespaces deployment is not always on (stops when idle; free quota 120 h/month) and its devcontainer was validated only through CI's simulated codespace, not in a real codespace yet | deployment | Low | ci.yml container job | First real codespace start confirms; report issues from `bash deploy/codespace.sh logs` |
| K-P09-03 | CI runs unit tests, image build and a container smoke test; integration suites (PostgreSQL/Temporal/engines) run locally only | CI | Low | ci.yml | Add an integration job with pinned PG18/Temporal/engines |
| K-P09-05 | Render free limits: sleeps after 15 idle minutes (about a minute to wake), 0.1 CPU makes scans slow, one scan at a time, uploads ≤ 25 MB, free PostgreSQL 1 GB (includes uploads/artifacts) deleted 30 days after creation | free deployment | Medium | ADR 0010, DEPLOYMENT.md | Export what matters; upgrade the DB or move to `deploy/render-standard.yaml` |
| K-P09-06 | Lite profile: after a restart an interrupted engine is re-run from the start (other results kept); no workflow history UI; memory budget verified in CI and on macOS, not yet on a live Render instance | free deployment | Low | test_hosted_smoke.py, ci.yml | Watch the first live scans' logs |
| K-P10-01 | Demo account is one shared workspace: every demo visitor sees what others uploaded; demo archives run on the same unsandboxed engines as the owner's | demo | Medium | ADR 0011 | Keep the demo notice; set `CRP_DEMO_ENABLED=false` before storing real customer code; per-visitor/tenant isolation in P07 |
| K-P10-02 | No automatic cleanup of demo projects (quota 30 projects, 20 scans/hour); they count against the free database's 1 GB | demo | Low | ADR 0011 | Delete projects from the UI (reclaims rows, artifacts and unshared blobs); a retention job is later |
| K-P10-04 | Project deletion is permanent (no trash/undo) and audited only in logs; orphan blobs from a deferred sweep wait for the next deletion | projects | Low | project_deletion.py | Soft delete, audit table and scheduled sweeps in P07 |
| K-P10-03 | Internal names still say `crp`/Code Review Platform (packages, CLI, `CRP_*` settings, Render resource names); only user-facing text says refactorX | naming | Info | ADR 0011 | Intentional; rename internals only with a migration plan |
| K-P02-06 | Disk: Trivy DB + engines use ~1.75 GB; host had ~6 GB free | local dev | Low | INSTALLATION | Skip the DB with `uv run crp-dev engines --skip-trivy-db` (Trivy then reports UNAVAILABLE) |

Known planned limitations: no Git ingestion until P06; no AI provider required in P00/P01; no universal defect detection; SAP/Salesforce runtime checks depend on authorized platform environments; private runner execution is distinct from local snapshot upload.

For each actual issue record ID, symptom, reproduction, affected phase/capability, severity, evidence, workaround and next action. Do not hide mandatory gate failures in a generic future-work list.
