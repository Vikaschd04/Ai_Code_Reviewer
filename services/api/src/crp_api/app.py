"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from crp_api import __version__
from crp_api.auth.local_token import LocalTokenProvider
from crp_api.auth.throttle import FailedAttemptThrottle
from crp_api.container import AppContainer
from crp_api.errors import install_error_handlers
from crp_api.middleware import (
    HostAllowlistMiddleware,
    LoopbackOnlyMiddleware,
    RequestContextMiddleware,
)
from crp_api.routes import (
    auth,
    diagnostics,
    graph,
    health,
    intakes,
    issues,
    projects,
    reports,
    scans,
    snapshots,
)
from crp_api.webapp import WebAppFiles
from crp_core.artifacts import ArtifactStore, create_artifact_store
from crp_core.config import AuthMode, Settings
from crp_core.db.session import create_engine_from_settings, create_session_factory
from crp_core.local_secrets import read_secret_file
from crp_core.workflows.gateway import WorkflowGateway
from crp_core.workflows.temporal import TemporalWorkflowGateway

API_PREFIX = "/v1"


def create_app(
    settings: Settings,
    *,
    workflow_gateway: WorkflowGateway | None = None,
    artifact_store: ArtifactStore | None = None,
) -> FastAPI:
    """Build the API. Optional collaborators allow tests to simulate dependency failures."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.local_token_file is None:  # guaranteed by Settings validation in local mode
            raise RuntimeError("local token file is not configured")
        identity = LocalTokenProvider(
            read_secret_file(settings.local_token_file),
            settings.session_ttl_seconds,
            demo_enabled=settings.demo_enabled,
        )
        if settings.hosted and identity.token_length < 32:
            raise RuntimeError("hosted mode requires an access token of at least 32 characters")
        engine = create_engine_from_settings(settings)
        workflows = workflow_gateway or TemporalWorkflowGateway(settings)
        app.state.container = AppContainer(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            workflows=workflows,
            artifacts=artifact_store or create_artifact_store(settings),
            identity=identity,
            login_throttle=FailedAttemptThrottle(),
        )
        # In-process gateways (lite profile) resume unfinished work once the API is ready.
        start = getattr(workflows, "start", None)
        if callable(start):
            await start()
        try:
            yield
        finally:
            await workflows.close()
            await engine.dispose()

    app = FastAPI(
        title="refactorX API",
        version=__version__,
        description=(
            "refactorX control-plane API: authentication (access token or optional demo "
            "account), readiness, projects and sample projects, ZIP/local-runner intake, frozen "
            "snapshots, scans (PMD, ESLint, Opengrep, Trivy, structure, graph) with coverage, "
            "findings, issues, comparison and exports. Source-only analysis; no AI provider is "
            "used."
        ),
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    install_error_handlers(app)
    for router in (
        health.router,
        auth.router,
        projects.router,
        intakes.router,
        snapshots.router,
        scans.router,
        issues.router,
        reports.router,
        graph.router,
        diagnostics.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    if settings.web_static_dir is not None:
        app.mount("/", WebAppFiles(settings.web_static_dir), name="web")
    if settings.hosted:
        # Direct archive uploads come from the web origin to this API's own host (upload
        # tickets, no cookies); everything else is same-origin through the web host's proxy.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_web_origins),
            allow_origin_regex=settings.allowed_web_origin_regex,
            allow_methods=["PUT"],
            allow_headers=["content-type"],
            allow_credentials=False,
            max_age=600,
        )
        app.add_middleware(HostAllowlistMiddleware, hosts=settings.public_hosts)
    elif settings.auth_mode is AuthMode.LOCAL_TOKEN:
        app.add_middleware(LoopbackOnlyMiddleware)
    app.add_middleware(RequestContextMiddleware)  # outermost: request IDs on every response
    return app
