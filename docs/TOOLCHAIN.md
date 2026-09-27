# Toolchain registry

Status: selected and verified in P00–P01 on 26 September 2026. Exact transitive versions live in `uv.lock` and `pnpm-lock.yaml`; this table lists direct choices. Re-verify versions, licenses and advisories before upgrading.

## Tested host

| Item | Value |
|---|---|
| OS | macOS 26.5.2 (Darwin 25.5.0), Apple Silicon arm64, 8 CPU, 8 GB RAM |
| Shell | zsh; GNU Make 3.81 |
| Other targets | **Linux and Windows are untested.** No parity is claimed. |
| Containers | Not used in P00 (Colima installed but not running; no Compose plugin). See ADR 0005. |

## Runtimes and services

| Area | Choice | Version | License | Evidence |
|---|---|---|---|---|
| Python | CPython (python.org build) | 3.14.3 (`requires-python >=3.14,<3.15`) | PSF | `make doctor` |
| Python packaging | uv | 0.12.19 (Homebrew); build backend `uv_build >=0.12.19,<0.13` | MIT/Apache-2.0 | `make bootstrap`, `make package` |
| Node.js | Node | 22.22.0 (`engines >=22.12.0`) | MIT | `make doctor` |
| JS packaging | pnpm | 11.20.0 (`packageManager` pinned) | MIT | `make bootstrap` |
| Database | PostgreSQL | 18.6 (Homebrew `postgresql@18`) | PostgreSQL | migration tests, `make doctor` |
| Workflow | Temporal CLI dev server | CLI 1.9.1, Server 1.32.0, UI 2.54.1 | MIT | workflow smoke test, `make doctor` |
| E2E browser | Google Chrome (installed) via Playwright `channel: "chrome"` | system Chrome | proprietary (not redistributed) | `make test-e2e` |

## Python direct dependencies (exact pins)

| Package | Version | License | Used by |
|---|---|---|---|
| fastapi | 0.141.1 | MIT | API (Starlette 1.7.0, BSD-3-Clause) |
| uvicorn | 0.54.0 | BSD-3-Clause | API server |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | MIT | contracts, settings |
| sqlalchemy[asyncio] | 2.1.1 (greenlet 3.5.6, MIT AND PSF-2.0) | MIT | persistence |
| alembic | 1.20.0 | MIT | migrations |
| psycopg[binary] | 3.3.6 | **LGPL-3.0-only** | PostgreSQL driver |
| temporalio | 1.33.0 | MIT | workflow client/worker |
| httpx | 0.28.1 | BSD-3-Clause | runner, doctor, tests |
| ruff | 0.16.9 | MIT | format + lint |
| mypy | 2.3.1 (strict, pydantic plugin) | MIT | type checks |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 | MIT / Apache-2.0 | tests |

License note: psycopg is LGPL-3.0 and `psycopg-binary` bundles libpq. It is used unmodified as a dynamically imported library. **Before distributing container images or binaries, obtain a license review** and ship the required notices (tracked in KNOWN_ISSUES K-P00-03).

## Web direct dependencies (exact pins)

| Package | Version | License | Notes |
|---|---|---|---|
| react / react-dom | 19.3.0 | MIT | |
| openapi-fetch | 0.17.0 | MIT | typed client over generated contract |
| openapi-typescript | 7.13.0 | MIT | generates `packages/contracts/src/v1.d.ts` |
| typescript | 5.9.3 | Apache-2.0 | TS 7.0 not used: typescript-eslint 8.70.1 supports `<6.1`, openapi-typescript needs `^5` |
| vite / @vitejs/plugin-react | 8.3.1 / 6.1.1 | MIT | dev server + proxy + build |
| eslint / @eslint/js / typescript-eslint / eslint-plugin-react-hooks | 10.11.0 / 10.0.1 / 8.70.1 / 7.1.1 | MIT | `strictTypeChecked` config |
| prettier | 3.9.9 | MIT | format check |
| vitest | 5.0.1 | MIT | 5.0.2 was <24 h old and held back by pnpm 11's minimum-release-age guard; 5.0.1 chosen instead of exempting it |
| jsdom | 29.1.1 | MIT | jsdom 30 requires Node ≥22.22.2 |
| @testing-library/react / dom / jest-dom / user-event | 16.3.3 / 10.4.2 / 7.0.1 / 14.6.7 | MIT | |
| @playwright/test | 1.63.0 | Apache-2.0 | E2E |
| @types/node | 22.20.4 | MIT | matches Node 22 runtime |

## Analysis engines and parsers (P01, P02)

| Tool | Version | License | Source / pin | Notes |
|---|---|---|---|---|
| PMD | 7.27.0 (released 2026-08-28) | BSD-style (PMD license) | `pmd-dist-7.27.0-bin.zip`, SHA-256 `4ae396ffaf2b0d3ef0b73a10b2925e77066f73d57a4ce9078c60e7302bcddec9`, installed by `make engines` | Requires Java 8+ (tested with Java 17.0.12). Trusted ruleset `crp-pmd-java-v1` (35 built-in rules) |
| ESLint | 10.11.0 | MIT | `engines/eslint-runner` (pnpm lock) | With typescript-eslint 8.70.1 (MIT), globals 17.12.0 (MIT); trusted config `crp-eslint-v1` (29 rules) |
| py-tree-sitter | 0.26.0 | MIT | PyPI (uv lock) | Grammars: tree-sitter-java 0.23.5, tree-sitter-javascript 0.25.0, tree-sitter-typescript 0.23.2 (all MIT) |

| Opengrep | 1.30.0 | LGPL-2.1 (separate unmodified executable) | `opengrep_osx_arm64`, SHA-256 `0f5bc3dec09d995c61331a4017b856ede508f90d95b018d95f1dc6166be89fdd`, Sigstore signature + certificate | Owned rules `crp-opengrep-v1` (10 rules); `XDG_CACHE_HOME` in the engine dir (P02) |
| Trivy | 0.69.3 | Apache-2.0 | `trivy_0.69.3_macOS-ARM64.tar.gz`, SHA-256 `a2f2179afd4f8bb265ca3c7aefb56a666bc4a9a411663bc0f22c3549fbc643a5`, Sigstore bundle | Verified-safe release after GHSA-69fq-xp46-6x23; offline DB in `.local/engines/trivy-cache` (data sources keep their own terms) (P02) |
| jsonschema | 4.26.0 (dev) | MIT | PyPI (uv lock) | Validates exports against the OASIS SARIF 2.1.0 schema and the platform schema in tests (P02) |
| cosign | v3.1.3 (optional host tool) | Apache-2.0 | Homebrew | Used by `crp-dev engines --verify-signatures`; not a runtime dependency |

Adoption records: ADR 0006, ADR 0007 and docs/ENGINE_ADOPTION.md. PMD 7.28.0 was one day old and was not adopted.

## Not yet selected (later phases)

| Area | Planned | Phase |
|---|---|---|
| Duplication | CPD (PMD) | P02 |
| Worker isolation | Pinned analyzer images/processes with resource limits | P01+ |
| Object storage | S3-compatible backend behind `ArtifactStore` | later |

## Upgrade procedure

Change the pin in the relevant `pyproject.toml`/`package.json`, run `uv lock` / `pnpm install`, then `make check`, `make test`, `make test-e2e`. Record new versions and license changes here. Keep pnpm's minimum-release-age protection enabled; do not add exemptions for freshly published packages without a reason recorded here.

## Hosted deployment and CI (ADR 0009)

| Item | Pin | Notes |
|---|---|---|
| Base image | `python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d` | runtime; Debian `openjdk-21-jre-headless` for PMD |
| Node image | `node:22-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4` | ESLint runner build (`pnpm deploy --prod --legacy`) and the `node` binary |
| uv image | `ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424` | locked `uv sync --no-dev` |
| Temporal CLI (Linux amd64) | 1.9.1, SHA-256 `09a0326a51db84d02735e53542b9ebd8c4758daf47482a9ab0abce15844e60d5` | dev server with SQLite on `/data` |
| Opengrep (Linux x86_64) | `opengrep_manylinux_x86`, SHA-256 `35779bdd72e92129c8df2a77f0c55e8c08356801ea92591ef32108d6b28d564c` | cosign-verified 2026-09-27 |
| Trivy (Linux x86_64) | `trivy_0.69.3_Linux-64bit.tar.gz`, SHA-256 `1816b632dfe529869c740c0913e36bd1629cb7688bd5634f4a858c1d57c88b75` | cosign-verified 2026-09-27; matches `trivy_0.69.3_checksums.txt` |
| GitHub Actions | checkout v7.0.1, setup-uv v10.2.0, setup-node v7.0.0, pnpm/action-setup v6.1.0, setup-buildx v4.4.1, build-push v7.4.0 — all pinned to commit SHAs in `.github/workflows/ci.yml` | CI Postgres service image `postgres:18` |
| Vercel build | `npx pnpm@11.20.0` install/build from `vercel.json` | static output `apps/web/dist` |
