# syntax=docker/dockerfile:1.7
# Single-user hosted backend: API + Temporal worker + Temporal dev server + analyzers.
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

# --- Temporal CLI (dev server), SHA-256 from the release checksums file -------------------------
FROM ${PYTHON_IMAGE} AS temporal
ARG TEMPORAL_VERSION=1.9.1
ARG TEMPORAL_SHA256=09a0326a51db84d02735e53542b9ebd8c4758daf47482a9ab0abce15844e60d5
RUN python - <<'PY'
import hashlib, io, os, tarfile, urllib.request
version = os.environ["TEMPORAL_VERSION"]
url = f"https://github.com/temporalio/cli/releases/download/v{version}/temporal_cli_{version}_linux_amd64.tar.gz"
data = urllib.request.urlopen(url, timeout=300).read()
digest = hashlib.sha256(data).hexdigest()
if digest != os.environ["TEMPORAL_SHA256"]:
    raise SystemExit(f"temporal checksum mismatch: {digest}")
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    member = archive.getmember("temporal")
    with open("/temporal", "wb") as out:
        out.write(archive.extractfile(member).read())
os.chmod("/temporal", 0o755)
PY

# --- Runtime -----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH \
    CRP_DATA_DIR=/data
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

# Analyzers: PMD, Opengrep, Trivy binaries (SHA-256 pinned). The Trivy vulnerability DB is data
# and is downloaded at runtime onto the persistent disk, then refreshed daily.
RUN crp-dev engines --skip-trivy-db \
 && chown -R crp:crp /app/.local

COPY deploy/entrypoint.sh /usr/local/bin/crp-entrypoint
RUN chmod 0755 /usr/local/bin/crp-entrypoint
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/crp-entrypoint"]
