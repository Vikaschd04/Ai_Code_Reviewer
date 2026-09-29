# ADR 0010 — Lite profile for free hosting (Render free)

Status: accepted and implemented, 30 September 2026 (user request: "choose the best option, either Render or Railway, with no limitation of complete functionality"). Owner: development agent for the repository maintainer. Extends ADR 0009 (single-user hosted mode).

Context: the owner wants to trial the complete application at a public URL without paying. The standard container (ADR 0009) needs ~2 GB of memory, a persistent disk and a Temporal server. Checked options (29–30 September 2026, render.com/docs/free and /blueprint-spec; railway.com pricing):

- **Render free:** web service 512 MB / 0.1 CPU, sleeps after 15 idle minutes (wakes in about a minute on the next request), ephemeral filesystem (no persistent disks), 750 free instance hours per workspace per month; free PostgreSQL 1 GB, one per workspace, expires 30 days after creation (14-day grace). Docker builds supported; build layers cached.
- **Railway:** one-time $5 trial credit (1 GB memory, 1 GB ephemeral disk), then the free plan's $1/month of credit with 0.5 GB memory — too small for the 1.3 GB offline vulnerability DB, and the credit runs out.

Decision: **Render free with a lite profile** (`CRP_PROFILE=lite`, set by `render.yaml`); the paid always-on Blueprint moves to `deploy/render-standard.yaml`; Railway is not supported.

- **One process, no Temporal server.** `crp-dev hosted` migrates, then `exec`s `crp_devtools.lite_server`: the same FastAPI app (UI + API) plus `InlineWorkflowGateway` (`services/worker/src/crp_worker/inline.py`), which implements the `WorkflowGateway` contract by running the **same activity code** (intake, scan prepare/engine/finalize, diagnostics) as asyncio tasks: idempotent workflow IDs, Temporal-like retries, cancellation that stops the running engine and finalizes CANCELED, one scan at a time. Activities heartbeat only when running inside Temporal (`crp_worker/progress.py`).
- **Durability without Temporal:** all state is in PostgreSQL; on start the gateway resumes intakes in VALIDATING and scans in QUEUED/RUNNING (activities are idempotent; `prepare` returns the non-terminal engines of a RUNNING scan and `run_engine` re-runs an engine left RUNNING, exactly as a Temporal retry would).
- **Artifacts in PostgreSQL** (`CRP_ARTIFACT_BACKEND=postgres`, `PostgresArtifactStore`, migration `0004_artifact_objects`: key, SHA-256, size, bytes with CHECK constraints; same store contract tests as the filesystem store) because the free instance has no disk. Objects are limited to 32 MB; uploads to 25 MB (expanded 200 MB, text files 2 MB).
- **Memory budget:** analyzers run one at a time; PMD heap 192 MB, ESLint heap 256 MB, Opengrep `-j 1`, database pool 3. Explicit `CRP_*` variables override every lite default.
- **Offline Trivy DB baked into the image** (`/app/.local/engines/trivy-cache`), so Trivy works immediately after every start or wake. `<data>/trivy-cache` is a symlink to the newest complete copy (baked or a refreshed generation, chosen at startup); the daily refresh downloads a new generation and swaps the symlink atomically (directories inside image layers cannot be renamed on overlay filesystems). In lite mode the swap and deletion of the old copy happen under the analyzer's DB lock (same process); in standard mode the replaced copy is deleted one check interval later. Scans report the DB date and staleness.

Measurements (development Mac, arm64, real engines, `security-mixed` fixture): idle ~130 MB RSS; peak ~319 MB RSS during a scan (all six engines); ~20 s CPU for start-up + 2 intakes + 2 scans. On 0.1 CPU that CPU time becomes minutes of wall time. CI proves the Linux image under the Render limits: the `container` job reruns the smoke test with `deploy/docker-compose.lite.yml` (lite profile, `mem_limit 512m`, no swap, `cpus 0.1`), requires every engine including Trivy to succeed, and fails on any restart or out-of-memory kill; it prints the container's `memory.peak`.

Alternatives considered: Railway (memory/disk/credit limits above); Render free with the standard profile (a Temporal server + worker + API do not fit 512 MB, and SQLite history would vanish with the ephemeral disk); Temporal Cloud (external account and cost); an external object store for artifacts (another account and credentials; PostgreSQL suffices at trial scale); downloading the Trivy DB at every start (a 1.3 GB extract on 0.1 CPU after every wake).

Consequences:

- Same API, UI and analysis results as the standard profile; the difference is capacity and speed: scans are slow on 0.1 CPU; one scan runs at a time; uploads are limited to 25 MB.
- Free-tier limits remain: first request after 15 idle minutes waits about a minute; a restart during a scan resumes it (no findings lost); the free database holds 1 GB (uploads and scan artifacts included) and Render deletes it 30 days after creation unless upgraded — export anything worth keeping.
- The image is ~1.3 GB larger. Render caches build layers, so the baked DB is refreshed at runtime (daily) rather than per build.
- Without Temporal there is no workflow history UI; progress and failures are in scan events and logs.

Evidence: `tools/devtools/tests/test_hosted_smoke.py` (standard and lite through the real entrypoint, all engines incl. Trivy from the baked copy; lite scan resumes after `SIGKILL` mid-engine), `tools/devtools/tests/test_trivy_db.py`, `tools/devtools/tests/test_hosted_env.py`, `packages/core/tests/test_artifact_store.py` (PostgreSQL store contract), `.github/workflows/ci.yml` container job (lite under 512 MB / 0.1 CPU); docs/DEPLOYMENT.md.
