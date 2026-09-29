# Single-user hosted application in one container: web UI + API + Temporal worker + Temporal dev
# server + analyzers (CRP_PROFILE=lite: one process without Temporal, for free tiers; ADR 0010).
# Build context = repository root. See docs/DEPLOYMENT.md. Base images are pinned by digest;
# analyzer binaries are pinned by SHA-256 (tools/devtools/src/crp_devtools/engines.py).

ARG PYTHON_IMAGE=python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
ARG NODE_IMAGE=node:22-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

# --- ESLint runner: self-contained node_modules from the frozen pnpm lockfile -------------------
FROM ${NODE_IMAGE} AS eslint-runner
ENV CI=true
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@11.20.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY engines/eslint-runner/ engines/eslint-runner/
RUN pnpm install --frozen-lockfile --filter @crp/eslint-runner \
 && pnpm --filter @crp/eslint-runner deploy --prod --legacy /out/eslint-runner

# --- Web UI: static production build served by the API (one origin for UI and API) ------------
FROM ${NODE_IMAGE} AS web
ENV CI=true
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@11.20.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY engines/eslint-runner/package.json engines/eslint-runner/package.json
RUN pnpm install --frozen-lockfile --filter "@crp/web..."
COPY packages/contracts/ packages/contracts/
COPY apps/web/ apps/web/
RUN pnpm --filter @crp/web build

# --- Temporal CLI (dev server), SHA-256 from the release checksums file -------------------------
FROM ${PYTHON_IMAGE} AS temporal
COPY deploy/fetch_temporal.py /fetch_temporal.py
RUN python /fetch_temporal.py 1.9.1 \
    09a0326a51db84d02735e53542b9ebd8c4758daf47482a9ab0abce15844e60d5 /temporal

# --- Runtime -----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH \
    CRP_DATA_DIR=/data \
    CRP_WEB_STATIC_DIR=/app/web-dist
RUN mkdir -p /usr/share/man/man1 \
 && apt-get update \
 && apt-get install -y --no-install-recommends openjdk-21-jre-headless libstdc++6 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin crp
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=temporal /temporal /usr/local/bin/temporal
COPY --from=eslint-runner /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
# Third-party dependencies first (cached layer), then the workspace sources.
COPY pyproject.toml uv.lock .python-version AGENTS.md ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/analysis/pyproject.toml packages/analysis/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml
COPY tools/devtools/pyproject.toml tools/devtools/pyproject.toml
COPY tools/local-runner/pyproject.toml tools/local-runner/pyproject.toml
RUN uv sync --locked --no-dev --all-packages --no-install-workspace
COPY packages/core/src packages/core/src
COPY packages/analysis/src packages/analysis/src
COPY services/api/src services/api/src
COPY services/worker/src services/worker/src
COPY tools/devtools/src tools/devtools/src
COPY tools/local-runner/src tools/local-runner/src
RUN uv sync --locked --no-dev --all-packages
COPY --from=eslint-runner /out/eslint-runner engines/eslint-runner
COPY --from=web /src/apps/web/dist web-dist

# Analyzers: PMD, Opengrep, Trivy binaries (SHA-256 pinned) plus the offline Trivy vulnerability
# DB (data, ~1.3 GB). Baking the DB in makes Trivy work right after every start, also on hosts
# without a persistent disk (Render free); crp-dev hosted refreshes it daily in the background
# (crp_devtools/trivy_db.py).
RUN crp-dev engines \
 && chown -R crp:crp /app/.local

COPY deploy/entrypoint.sh /usr/local/bin/crp-entrypoint
RUN chmod 0755 /usr/local/bin/crp-entrypoint
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/crp-entrypoint"]
