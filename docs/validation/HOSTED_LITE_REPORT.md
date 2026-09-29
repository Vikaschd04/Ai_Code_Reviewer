# Validation — lite profile and free Render deployment (ADR 0010)

Date: 30 September 2026. Scope: user request to deploy the complete application for free on Render (or Railway); not a phase gate.

## Environment

- Local: macOS 26.5.2 arm64 (Apple M2, 8 GB); Python 3.14.3; Node 22; PostgreSQL 18.6; Temporal CLI 1.9.1; PMD 7.27.0; ESLint 10.11.0; Opengrep 1.30.0; Trivy 0.69.3 with offline DB (local copy in `.local/engines/trivy-cache`).
- CI: GitHub Actions `ubuntu-latest`, Linux x86_64 image from `Dockerfile` (digest-pinned bases), Docker Compose with PostgreSQL 18.
- Render limits were taken from render.com/docs/free and /blueprint-spec on 30 September 2026 (free web service 0.1 CPU / 512 MB, sleeps after 15 idle minutes, ephemeral filesystem; free PostgreSQL 1 GB, 30-day expiry; `plan: free` valid for services and databases; PostgreSQL 18 supported).

## Commands and outcomes (local)

| Command | Outcome |
|---|---|
| `make check` | exit 0 (ruff format/check, mypy strict on 116 files, contract drift, web typecheck/lint/format) |
| `make test` | exit 0: 303 pytest passed (0 skipped; unit + integration with real PostgreSQL, Temporal and engines) + 14 vitest |
| `pytest tools/devtools/tests/test_hosted_smoke.py` | 5 passed: real `crp-dev hosted` entrypoint in standard and lite profile, all six engines SUCCEEDED including Trivy from the baked-copy symlink; CI smoke script with `SMOKE_REQUIRE_TRIVY=1` |
| `test_lite_profile_resumes_a_scan_after_the_process_is_killed` ×3 extra runs | 3/3 passed: process group SIGKILLed while an external analyzer was RUNNING, restarted on the same database, scan resumed to SUCCEEDED with findings; log shows "resumed unfinished work" |
| `pytest tools/devtools/tests/test_trivy_db.py` | 6 passed: baked copy active immediately, newest copy wins, legacy directory adopted, incomplete generations removed, baked copy never deleted, atomic swap under the lock, failed refresh keeps the current copy, only a stale copy is refreshed |
| `docker-compose -f deploy/docker-compose.yml -f deploy/docker-compose.lite.yml config` | override merges: `CRP_PROFILE=lite`, `mem_limit`/`memswap_limit` 512 MiB, `cpus` 0.1 |

Earlier measurement of the lite profile (same machine, `security-mixed` fixture, real engines): idle ~130 MB RSS, peak ~319 MB RSS during a scan, ~20 s CPU for start-up + 2 intakes + 2 scans.

## CI (Linux image under Render free limits)

The `container` job builds the image (Trivy DB baked in), runs the smoke test in the standard profile, then resets the data and reruns it in the lite profile with 512 MB memory, no swap and 0.1 CPU. The job requires Trivy SUCCEEDED and fails on any container restart or out-of-memory kill; it prints the cgroup `memory.peak`. Results:

- Run 36614706192 (commit a62c8e2): `quality` and `container` succeeded. Image build 2 min 17 s; standard smoke 22 s; lite step (reset, start at 0.1 CPU, full smoke with Trivy required, restart/OOM checks) 2 min 17 s. The step fails on any restart, OOM flag or cgroup `oom_kill`, so success means none occurred. The printed `memory.peak` is only in the job log (sign-in required), so the workflow now also publishes it as a check annotation.

## Remaining gaps

- The live Render service has not been created yet (owner action; docs/DEPLOYMENT.md). CI reproduces the memory/CPU limits but not Render's proxy, wake-up or network behaviour.
- The runtime Trivy DB refresh path was unit-tested with a fake download; a real download inside the Linux image under 512 MB was not exercised (CI disables auto-refresh for determinism).
- Free-tier limits (K-P09-05) and lite-profile limits (K-P09-06) in docs/memory/KNOWN_ISSUES.md.
