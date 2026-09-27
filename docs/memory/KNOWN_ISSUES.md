# Known issues and gaps

Updated 27 September 2026 after P02. None of these block the P00–P02 gates.

| ID | Symptom / gap | Scope | Severity | Evidence | Workaround / next action |
|---|---|---|---|---|---|
| K-P00-01 | Only macOS arm64 verified | all commands | Medium | P00_REPORT environment | Verify on Linux (BACKLOG P00-F2) |
| K-P00-02 | No container images or Compose; Temporal is the CLI dev server | local deployment | Medium | ADR 0005 | Add digest-pinned images later (P00-F3); qualify hosted Temporal in P07 |
| K-P00-03 | psycopg/psycopg-binary are LGPL-3.0 | distribution | Low (dev) | TOOLCHAIN.md | License review before shipping images/binaries (P00-F4) |
| K-P00-04 | Not a Git repository: no commit identity; `/security-review` and `/code-review` not run | process | Medium | P00_REPORT | User decides on `git init`; then run reviews (P00-F1) |
| K-P00-05 | Worker readiness may report OK for up to 90 s after a worker stops (Temporal poller freshness) | readiness | Low | readiness.py, INSTALLATION troubleshooting | Diagnostic workflow proves execution; tune `CRP_WORKER_POLLER_FRESH_SECONDS` |
| K-P00-06 | Browser sessions cannot be revoked individually | local auth | Low | ADR 0004 | Rotate the token file; hosted IdP in P07 |
| K-P01-01 | Engines run without an OS-level sandbox (no cgroup/VM/egress block) on macOS dev hosts | engine execution | Medium | ADR 0006 | Wall-time, output and heap caps, scrubbed env, read-only copies; add container/VM isolation before multi-tenant use (P01-F4) |
| K-P01-02 | Secret masking in source excerpts is heuristic | UI/API excerpts | Medium | `crp_analysis/redaction.py` | Keep secrets out of uploads (runner excludes known secret files); dedicated secret scanning later |
| K-P01-03 | No scheduled intake expiry or blob GC | storage | Low | P01_REPORT | Expiry runs on intake creation; delete `~/.local/share/code-review-platform/artifacts` to reset dev data (P01-F2) |
| K-P01-04 | Browser folder selection and registered mounts not implemented | intake | Low | FEATURE_MATRIX | Use ZIP or `crp-runner capture` (P01-F1) |
| K-P00-07 | Homebrew `postgresql@18` post-install needs Homebrew ≥ 7 | setup | Low | INSTALLATION troubleshooting | `brew update && brew postinstall postgresql@18` |
| K-P02-01 | `engine_cache` grows until the project is deleted (no eviction) | storage | Low | ADR 0008 | Delete the project or truncate `engine_cache`; eviction is P02-F1 |
| K-P02-02 | Offline Trivy DB goes stale (NextUpdate passes daily); refresh is manual; a refresh changes Trivy's rule-set hash so absent vulns become UNKNOWN | dependency findings | Medium | doctor warning, engine card | `make engines` (~1.3 GB download); P02-F2 |
| K-P02-03 | Graph is syntax-level: no classpath/type-checker resolution, no call edges; Gradle, parent POMs, tsconfig `extends`, `exports` fields not followed | graph/impact | Medium | P02_REPORT gaps | Unresolved counts shown in UI/API; P02-F3 |
| K-P02-04 | Opengrep/Trivy pinned for macOS arm64 only | engines | Medium | engines.py | Add Linux pins (P02-F4) |
| K-P02-05 | Findings of scans made before migration 0003 have no issue link | UI/API | Low | dev-stack check | Rescan; backfill is P02-F5 |
| K-P02-06 | Disk: Trivy DB + engines use ~1.75 GB; host had ~6 GB free | local dev | Low | INSTALLATION | Skip the DB with `uv run crp-dev engines --skip-trivy-db` (Trivy then reports UNAVAILABLE) |

Known planned limitations: no Git ingestion until P06; no AI provider required in P00/P01; no universal defect detection; SAP/Salesforce runtime checks depend on authorized platform environments; private runner execution is distinct from local snapshot upload.

For each actual issue record ID, symptom, reproduction, affected phase/capability, severity, evidence, workaround and next action. Do not hide mandatory gate failures in a generic future-work list.
