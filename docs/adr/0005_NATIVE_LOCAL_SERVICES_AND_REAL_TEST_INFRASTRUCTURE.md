# ADR 0005 — Native local PostgreSQL/Temporal and real ephemeral test infrastructure

Status: accepted and implemented, 26 September 2026 (P00). Owner: development agent for the repository maintainer.

Context and constraints: the development host (macOS 26.5 arm64) had Docker CLI with Colima installed but not running, no Compose plugin, and about 5 GB of free disk. P00 forbids replacing PostgreSQL/Temporal integration tests with mocks.

Decision:

- Use native binaries: PostgreSQL 18.6 (Homebrew `postgresql@18`) and the Temporal CLI 1.9.1 dev server (`temporal server start-dev`, Temporal Server 1.32.0, SQLite persistence).
- Development state is project-local and git-ignored: `.local/postgres` (cluster initialized with SCRAM auth, generated password, `listen_addresses=127.0.0.1`, unix sockets disabled, port 55432) and `.local/temporal/dev.sqlite` (gRPC 127.0.0.1:7233, UI 127.0.0.1:8233). Nothing uses Homebrew's global services.
- Tests start **real throwaway instances** on free loopback ports (`crp_devtools.testing.fixtures`): one PostgreSQL cluster and one Temporal dev server per pytest session; migrated databases are cloned per test from a template. Missing binaries make integration tests error, never skip.
- `make test-e2e` starts a separate throwaway PostgreSQL/Temporal/API/worker plus the production web build and runs Playwright with the installed Google Chrome.
- All processes are launched from fixed executables with argument arrays; no shell strings.

Alternatives considered: Docker Compose (not runnable on this host without starting a VM and installing a plugin; no disk headroom); SQLite in place of PostgreSQL (violates the gate); Temporal's in-memory time-skipping test server only (does not prove the dev server/worker process path).

Consequences: contributors need PostgreSQL 18 and the Temporal CLI on PATH or via `CRP_PG_BIN_DIR`/`CRP_TEMPORAL_CLI`. The Temporal dev server is not a production deployment. Container images and Compose definitions remain a later task (they must be pinned by digest when added) and are not claimed now. Linux/Windows are untested.

Evidence: `make test` (123 Python tests, including real PostgreSQL migrations and a real Temporal workflow), `make test-e2e` (5 Playwright tests), `make dev` + `make doctor`; see docs/validation/P00_REPORT.md.

Affected contracts/phases/tests: INSTALLATION.md, TOOLCHAIN.md, all integration suites; P07 must qualify hosted Temporal/PostgreSQL deployments separately.
