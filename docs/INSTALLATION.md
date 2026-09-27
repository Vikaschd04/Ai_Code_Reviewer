# Installation and developer commands

Status: implemented in P00–P01 and verified on macOS 26.5 (arm64) on 26 September 2026. Linux and Windows are **untested**. Evidence: docs/validation/P00_REPORT.md, P01_REPORT.md.

## Prerequisites

| Tool | Tested version | macOS install |
|---|---|---|
| Python | 3.14.x | python.org installer or `uv python install 3.14` |
| uv | 0.12.19 | `brew install uv` |
| Node.js | 22.22 (≥22.12) | `brew install node@22` |
| pnpm | 11.20.0 | `brew install pnpm` or `corepack enable` |
| PostgreSQL server binaries | 18.6 | `brew install postgresql@18` (keg-only; found automatically at `/opt/homebrew/opt/postgresql@18/bin`, or set `CRP_PG_BIN_DIR`) |
| Temporal CLI | 1.9.1 | `brew install temporal` (or set `CRP_TEMPORAL_CLI`) |
| Java runtime | 17+ (tested 17.0.12) | required by PMD; any JDK/JRE on PATH |
| Google Chrome | any current | only for `make test-e2e` (or use Playwright's Chromium, see below) |

No Docker, cloud account, model key or paid service is required. The repository path may contain spaces.

Disk space: allow about 2.5 GB for `.local/engines` (Trivy DB ~1.3 GB, Opengrep ~290 MB, Trivy ~150 MB, PMD) plus test/E2E temporary data.

## Commands (all verified)

| Contract | Command | What it does |
|---|---|---|
| bootstrap | `make bootstrap` | `uv sync --all-packages --locked`, `pnpm install --frozen-lockfile`, `crp-dev engines` (PMD download + SHA-256 check), generates `.local/secrets/{local-api-token,postgres-password}` (0600), initializes the dev PostgreSQL cluster in `.local/postgres`, writes `.local/dev.env`, runs doctor |
| dev | `make dev` | Starts PostgreSQL + Temporal (if not running), migrates, then API, worker and Vite in the foreground with prefixed logs. Ctrl-C stops the app processes and any infrastructure this command started |
| engines | `make engines` | Downloads PMD 7.27.0, Opengrep 1.30.0 and Trivy 0.69.3 from their official releases, verifies pinned SHA-256s, installs to `.local/engines/`, unpacks Opengrep once, downloads the Trivy vulnerability DB (~1.3 GB; the only network step — scans are offline) and checks the ESLint runner. `uv run crp-dev engines --verify-signatures` additionally verifies Sigstore signatures (needs `cosign`); `--skip-trivy-db` skips the DB (Trivy then reports UNAVAILABLE). Re-run to refresh a stale DB |
| infra | `make infra-up` / `make infra-down` / `make infra-status` | Start/stop/show only PostgreSQL and the Temporal dev server |
| doctor | `make doctor` | Tool versions, secret-file safety, DB schema revision, Temporal namespace, worker pollers, API readiness, analyzer status. Exit 1 on any failure; services that are merely stopped are warnings |
| migrate | `make migrate` | `infra-up`, then Alembic upgrade to head and idempotent local-identity provisioning |
| seed-fixtures | `make seed-fixtures` | Creates one project labelled **Synthetic fixture** (no source, scans or findings). Idempotent |
| check | `make check` | ruff format/lint, mypy strict, OpenAPI + TypeScript contract drift, tsc, ESLint, Prettier |
| test | `make test` (`SCOPE=all\|python\|unit\|integration\|web`) | pytest (real ephemeral PostgreSQL/Temporal for integration tests; JUnit report in `.local/test-reports/`) and vitest |
| benchmark | `make benchmark` | Isolated stack; generates the synthetic medium fixture (1,010 files), runs a cold and a warm scan, samples worker memory and times read APIs → `.local/benchmarks/p02-*.json` |
| test-e2e | `make test-e2e` | Starts a throwaway PostgreSQL/Temporal/API/worker + production web build on free ports and runs Playwright (Chrome). Logs are copied to `.local/logs/e2e-*` on failure |
| package | `make package` | Wheels/sdists for all Python packages in `dist/python`, web bundle in `dist/web`, SHA-256 `dist/MANIFEST.json` |
| contracts | `make contracts` | Regenerates `packages/contracts/openapi.json` and `src/v1.d.ts` after API model changes |
| context-map | `make context-map` | Bounded repository map → `.local/context/map-<digest>.json` |
| context-pack | `make context-pack TASK=P01-01 PATHS="services/api docs/SOURCE_INTAKE.md"` | Task-scoped packet → `.local/context/packs/<task>/<key>.md` (cached by content) |
| token | `make token`; `uv run crp-dev token --show` | Where the sign-in token is; print it |

Direct CLIs: `uv run crp-dev --help`, `uv run crp-api`, `uv run crp-worker`, `uv run crp-runner ping --token-file .local/secrets/local-api-token`, `uv run crp-runner capture <folder> --project-id <id> --token-file .local/secrets/local-api-token [--dry-run] [--yes] [--scan]`.

## First run

```sh
make bootstrap
make dev            # leave running
# new terminal:
uv run crp-dev token --show     # paste into the sign-in form
open http://127.0.0.1:5173
make doctor         # expect 0 failing
```

## Service URLs (loopback only)

| Service | URL |
|---|---|
| Web UI | http://127.0.0.1:5173 (proxies `/v1` to the API) |
| API + OpenAPI UI | http://127.0.0.1:8710/v1/docs, contract at `/v1/openapi.json` |
| Temporal gRPC / UI | 127.0.0.1:7233 / http://127.0.0.1:8233 |
| PostgreSQL | 127.0.0.1:55432, database `crp`, user `crp` |

Ports can be overridden with `CRP_DEV_PG_PORT`, `CRP_DEV_TEMPORAL_PORT`, `CRP_DEV_TEMPORAL_UI_PORT`, `CRP_DEV_API_PORT`, `CRP_DEV_WEB_PORT`.

## Configuration

Services read `CRP_*` environment variables, validated at startup (`crp_core/config.py`). `.env.example` documents every variable without secrets. `crp-dev` builds the values from `.local/` and writes `.local/dev.env` (0600). To run a service manually:

```sh
set -a; . .local/dev.env; set +a
uv run crp-api        # or: uv run crp-worker
```

Key rules enforced at startup (exit code 2 with a message that never echoes input values):

- `CRP_AUTH_MODE=local_token` (the only mode) requires a loopback `CRP_API_HOST` and loopback `CRP_ALLOWED_WEB_ORIGINS`.
- `CRP_LOCAL_TOKEN_FILE` must be an owner-only regular file (not a symlink).
- `CRP_ARTIFACT_ROOT` must be absolute and outside `CRP_TRUSTED_DEV_ROOT` (the repo). Default: `~/.local/share/code-review-platform/artifacts` (or `$XDG_DATA_HOME/...`).
- `CRP_DATABASE_URL` must use `postgresql+psycopg://`.
- `CRP_ENVIRONMENT` accepts only `local` or `test`.

Intake quotas (`CRP_INTAKE_*`), engine locations/limits (`CRP_PMD_HOME`, `CRP_ESLINT_RUNNER_DIR`, `CRP_NODE_EXECUTABLE`, `CRP_WORK_ROOT`, `CRP_ENGINE_TIMEOUT_SECONDS`, `CRP_ENGINE_MAX_OUTPUT_BYTES`, `CRP_PMD_JAVA_HEAP`) are documented in `.env.example`; `crp-dev` sets the engine paths automatically. `CRP_WORK_ROOT` must be outside the repository. Provider/model settings and egress policy arrive with P03.

## Local state and reset

Everything generated lives in `.local/` (ignored): `secrets/`, `postgres/`, `temporal/`, `logs/`, `run/`, `test-reports/`, `context/`. Artifacts live outside the repo (see above). To reset the dev database: `make infra-down`, delete `.local/postgres`, `rm .local/temporal/dev.sqlite`, then `make bootstrap && make migrate`. Rotating the token (`rm .local/secrets/local-api-token && make bootstrap`) invalidates all browser sessions.

## Using Playwright's Chromium instead of Chrome

```sh
pnpm --filter @crp/web exec playwright install chromium
CRP_E2E_CHANNEL= make test-e2e
```

## Local runner

`crp-runner ping` verifies API liveness and authenticated readiness. `crp-runner capture` snapshots one explicitly named folder and **uploads its source** to the platform:

```sh
uv run crp-runner capture ~/code/billing --project-id <project-uuid> \
  --token-file .local/secrets/local-api-token --dry-run      # preview, sends nothing
uv run crp-runner capture ~/code/billing --project-id <project-uuid> \
  --token-file .local/secrets/local-api-token --scan         # confirm with "yes"; then scans
```

It downloads the server's scope policy (`GET /v1/intake-policy`), skips excluded paths on your machine (secrets such as `.env`/keys, `.git`, `node_modules`, build output, minified bundles), records symlinks/special/unreadable entries without following them, never writes to the folder or runs its scripts, retries if files change during capture, writes a deterministic archive to the system temp directory (outside the folder) and deletes it afterwards. The server recomputes every hash and rejects a mismatching manifest. Tokens are read from a file (owner-only), never from arguments. The project UUID is shown on the project's **Add source** tab.

## Troubleshooting (verified cases)

| Symptom | Cause / fix |
|---|---|
| `initdb: error: file ".../share/postgresql@18/postgres.bki" does not exist` | Homebrew post-install for `postgresql@18` did not complete (the formula needs a newer Homebrew). Run `brew update && brew postinstall postgresql@18`. |
| doctor `FAIL postgresql ... not found` | Install PostgreSQL 18 or set `CRP_PG_BIN_DIR` to its `bin` directory. |
| `crp-api: refusing to start: ... Refusing to start: local-token authentication may only bind to a loopback address` | `CRP_API_HOST` is not loopback. Hosted exposure is not supported before P07. |
| `secret file local-api-token is accessible by other users` | `chmod 600 .local/secrets/local-api-token` |
| Readiness `database: failed schema_out_of_date` | Run `make migrate`. |
| Readiness `database: unavailable database_unreachable` / API returns `503 database_unavailable` | PostgreSQL is not running: `make infra-up`. |
| Readiness `workflow_worker: unavailable no_active_worker` | Start the worker (`make dev`). A stopped worker may still appear ready for up to `CRP_WORKER_POLLER_FRESH_SECONDS` (90 s). |
| Readiness `workflow_service: unavailable` | Temporal dev server not running: `make infra-up`. |
| `port 7233 is already in use` | Another Temporal server is running (e.g. `brew services`); stop it or set `CRP_DEV_TEMPORAL_PORT`. |
| Sign-in shows `origin_rejected` | Open the UI at an origin in `CRP_ALLOWED_WEB_ORIGINS` (default `http://127.0.0.1:5173` or `http://localhost:5173`). |
| Sign-in returns 429 | 10 failed attempts within 60 s; wait for `Retry-After`. |
| doctor `FAIL analyzer pmd` | Run `make engines` (needs network to github.com once). |
| doctor `FAIL analyzer eslint` | Run `pnpm install` (installs `engines/eslint-runner`). |
| Intake `REJECTED` with `path_traversal`, `symlink_entry`, `path_collision`, `compression_ratio`, `expanded_too_large`, `too_many_entries`, `encrypted_entry`, `corrupt_archive`, `unsupported_archive` | The archive violates the intake contract; the UI and `GET /v1/intakes/{id}` list the offending entries (escaped). Re-create the archive from the project folder or use `crp-runner capture`. |
| Upload returns 413 `upload_too_large` | The archive exceeds `CRP_INTAKE_MAX_UPLOAD_BYTES` (default 100 MiB). |
| Runner intake `REJECTED` `client_manifest_mismatch` / `policy_version_mismatch` | Files changed between capture and upload, or the runner is older than the server policy; rerun the capture with the current runner. |
| Scan `PARTIAL` | At least one engine could not analyze every eligible file (parse errors, crash, unavailable). The scan page lists limitations; the Coverage tab lists each affected file and reason. |
| Engine `UNAVAILABLE` | PMD or ESLint not installed/configured for the worker; see the two doctor rows above. |
| pnpm adds `minimumReleaseAgeExclude` entries | A pinned package is younger than pnpm's minimum release age; pin an older, matured release instead of exempting it (see TOOLCHAIN.md). |
| Not yet implemented | no AI key, rate limit, platform SDK/org unavailable — these arrive with P03+ features. |
