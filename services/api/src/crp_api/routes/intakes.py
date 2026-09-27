"""Source intake: create, stream upload, runner manifest, idempotent finalize, cancel, status.

Upload bytes stream to a private temporary file with the compressed-size limit enforced on actual
bytes (Content-Length is advisory), then move into the artifact store. Validation/extraction runs
in the worker, never in the request loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from crp_analysis import policy
from crp_analysis.client_manifest import ClientManifest
from crp_api.auth.dependencies import Container, CurrentPrincipal, OptionalPrincipal
from crp_api.auth.principal import Principal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    IntakeCreate,
    IntakeLimits,
    IntakePage,
    IntakePolicyResponse,
    IntakeResponse,
    UploadTicketResponse,
)
from crp_api.services.scope import get_scoped
from crp_core.artifacts import ArtifactKey
from crp_core.config import Settings
from crp_core.db.models import Intake, Project, Source
from crp_core.db.session import transaction
from crp_core.domain.states import IntakeState, MembershipRole
from crp_core.workflows.gateway import WorkflowUnavailableError

router = APIRouter(tags=["intake"], responses={401: {"model": ErrorResponse}})

_ACCEPTED_TYPES = frozenset(
    {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
)
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


def _limits(settings: Settings) -> IntakeLimits:
    return IntakeLimits(
        max_upload_bytes=settings.intake_max_upload_bytes,
        max_expanded_bytes=settings.intake_max_expanded_bytes,
        max_entries=settings.intake_max_entries,
        max_text_file_bytes=settings.intake_max_text_file_bytes,
        max_compression_ratio=settings.intake_max_compression_ratio,
    )


def _response(intake: Intake, settings: Settings) -> IntakeResponse:
    return IntakeResponse(
        id=intake.id,
        project_id=intake.project_id,
        source_id=intake.source_id,
        mode=intake.mode,
        state=intake.state,
        archive_sha256=intake.archive_sha256,
        archive_bytes=intake.archive_bytes,
        snapshot_id=intake.snapshot_id,
        error_code=intake.error_code,
        error_message=intake.error_message,
        error_details=intake.error_details,
        created_at=intake.created_at,
        finalized_at=intake.finalized_at,
        expires_at=intake.expires_at,
        limits=_limits(settings),
    )


async def _intake(
    session: Any, principal: Principal, intake_id: uuid.UUID, *, for_update: bool = False
) -> Intake:
    return await get_scoped(
        session,
        principal,
        Intake,
        intake_id,
        not_found="intake_not_found",
        required=MembershipRole.MEMBER,
        for_update=for_update,
    )


def _conflict(intake: Intake, action: str) -> ApiError:
    return ApiError(
        409, "invalid_intake_state", f"Cannot {action} an intake in state {intake.state}"
    )


@router.get("/intake-policy", response_model=IntakePolicyResponse)
async def intake_policy(principal: CurrentPrincipal, container: Container) -> IntakePolicyResponse:
    """Scope policy and quotas. The local runner applies the same policy before uploading."""
    document = policy.policy_document()
    return IntakePolicyResponse(
        version=str(document["version"]),
        excluded_directories=policy.EXCLUDED_DIRECTORIES,
        secret_patterns=list(policy.SECRET_PATTERNS),
        secret_allowlist=sorted(policy.SECRET_ALLOWLIST),
        generated_patterns=list(policy.GENERATED_PATTERNS),
        limits=_limits(container.settings),
        notes=[
            "ZIP bytes reach the server before exclusions apply; excluded entries are recorded "
            "but never stored or analyzed. Remove secrets before zipping, or use the local runner, "
            "which skips excluded paths on your machine.",
            "Only regular files are accepted: symlinks, devices, traversal and colliding paths "
            "reject the whole archive.",
        ],
    )


@router.post(
    "/projects/{project_id}/intakes",
    status_code=201,
    response_model=IntakeResponse,
    responses=_ERRORS,
)
async def create_intake(
    project_id: uuid.UUID, body: IntakeCreate, principal: CurrentPrincipal, container: Container
) -> IntakeResponse:
    settings = container.settings
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
        )
        source = Source(
            workspace_id=project.workspace_id,
            project_id=project.id,
            mode=body.mode,
            display_name=body.display_name,
        )
        session.add(source)
        await session.flush()
        intake = Intake(
            workspace_id=project.workspace_id,
            project_id=project.id,
            source_id=source.id,
            mode=body.mode,
            state=IntakeState.CREATED.value,
            created_by=principal.user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.intake_expiry_seconds),
        )
        session.add(intake)
        await session.flush()
        await session.refresh(intake)
        response = _response(intake, settings)
    await expire_abandoned(container)
    return response


@router.get("/projects/{project_id}/intakes", response_model=IntakePage, responses=_ERRORS)
async def list_intakes(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> IntakePage:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        rows = (
            await session.execute(
                select(Intake)
                .where(Intake.project_id == project.id)
                .order_by(Intake.created_at.desc())
                .limit(50)
            )
        ).scalars()
        return IntakePage(items=[_response(row, container.settings) for row in rows])


@router.get("/intakes/{intake_id}", response_model=IntakeResponse, responses=_ERRORS)
async def get_intake(
    intake_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> IntakeResponse:
    async with transaction(container.session_factory) as session:
        intake = await get_scoped(
            session, principal, Intake, intake_id, not_found="intake_not_found"
        )
        return _response(intake, container.settings)


def _file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            yield chunk


@router.post(
    "/intakes/{intake_id}/upload-ticket",
    response_model=UploadTicketResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
)
async def create_upload_ticket(
    intake_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> UploadTicketResponse:
    """Issue a 15-minute ticket that authorizes uploading this intake's archive only.

    Hosted deployments upload directly to the API host (bypassing the web host's proxy limits),
    so the ticket replaces the same-site session cookie for that single request.
    """
    async with transaction(container.session_factory) as session:
        intake = await _intake(session, principal, intake_id)
        if IntakeState(intake.state) not in {IntakeState.CREATED, IntakeState.UPLOADING}:
            raise _conflict(intake, "upload to")
    ticket, expires_at = container.identity.issue_upload_ticket(str(intake_id))
    base = (container.settings.public_api_url or "").rstrip("/")
    return UploadTicketResponse(
        upload_url=f"{base}/v1/intakes/{intake_id}/content?ticket={ticket}",
        expires_at=expires_at,
        max_bytes=container.settings.intake_max_upload_bytes,
    )


async def _upload_target(
    session: Any,
    container: Any,
    principal: Principal | None,
    ticket: str | None,
    intake_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Intake:
    """Resolve the intake through the caller's grants or through a valid upload ticket."""
    if principal is not None:
        return await _intake(session, principal, intake_id, for_update=for_update)
    if ticket is None or not container.identity.verify_upload_ticket(ticket, str(intake_id)):
        raise ApiError(
            401,
            "authentication_required",
            "Valid credentials or a valid upload ticket are required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    intake: Intake | None = await session.get(Intake, intake_id, with_for_update=for_update)
    if intake is None:
        raise ApiError(404, "intake_not_found", "Intake not found")
    return intake


@router.put(
    "/intakes/{intake_id}/content",
    response_model=IntakeResponse,
    responses={**_ERRORS, 413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_content(
    intake_id: uuid.UUID,
    request: Request,
    principal: OptionalPrincipal,
    container: Container,
    ticket: Annotated[str | None, Query(max_length=1024)] = None,
) -> IntakeResponse:
    settings = container.settings
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in _ACCEPTED_TYPES:
        raise ApiError(415, "unsupported_media_type", "Upload a ZIP archive (application/zip)")
    limit = settings.intake_max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(
            413, "upload_too_large", f"The upload exceeds {limit} bytes", {"limit": limit}
        )
    async with transaction(container.session_factory) as session:
        intake = await _upload_target(session, container, principal, ticket, intake_id)
        if IntakeState(intake.state) not in {IntakeState.CREATED, IntakeState.UPLOADING}:
            raise _conflict(intake, "upload to")

    staging = settings.work_root / "uploads"
    await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True, mode=0o700)
    temp = staging / f"{intake_id.hex}-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        async with await anyio.open_file(temp, "xb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise ApiError(
                        413,
                        "upload_too_large",
                        f"The upload exceeds {limit} bytes",
                        {"limit": limit},
                    )
                digest.update(chunk)
                await handle.write(chunk)
        if size == 0:
            raise ApiError(400, "empty_upload", "The upload was empty")
        key = ArtifactKey(f"intakes/{intake_id.hex}/archive.zip")
        await asyncio.to_thread(
            container.artifacts.put_stream, key, _file_chunks(temp), overwrite=True
        )
    except ClientDisconnect as exc:
        raise ApiError(
            400, "upload_aborted", "The client disconnected before the upload completed"
        ) from exc
    finally:
        await asyncio.to_thread(temp.unlink, missing_ok=True)

    async with transaction(container.session_factory) as session:
        intake = await _upload_target(
            session, container, principal, ticket, intake_id, for_update=True
        )
        if IntakeState(intake.state) not in {IntakeState.CREATED, IntakeState.UPLOADING}:
            await asyncio.to_thread(container.artifacts.delete, key)
            raise _conflict(intake, "upload to")
        intake.state = IntakeState.UPLOADING.value
        intake.archive_key = str(key)
        intake.archive_sha256 = digest.hexdigest()
        intake.archive_bytes = size
        await session.flush()
        await session.refresh(intake)
        return _response(intake, settings)


@router.put(
    "/intakes/{intake_id}/client-manifest",
    response_model=IntakeResponse,
    responses={**_ERRORS, 413: {"model": ErrorResponse}},
)
async def upload_client_manifest(
    intake_id: uuid.UUID, request: Request, principal: CurrentPrincipal, container: Container
) -> IntakeResponse:
    """Local-runner manifest (declared hashes and local exclusions). Verified, never trusted."""
    limit = container.settings.intake_client_manifest_max_bytes
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise ApiError(413, "manifest_too_large", "The runner manifest is too large")
    try:
        manifest = ClientManifest.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise ApiError(
            400,
            "invalid_client_manifest",
            "The runner manifest is not valid",
            {"errors": exc.error_count()},
        ) from exc
    async with transaction(container.session_factory) as session:
        intake = await _intake(session, principal, intake_id, for_update=True)
        if intake.mode != "local_runner":
            raise ApiError(
                409, "invalid_intake_mode", "Only local-runner intakes accept a client manifest"
            )
        if IntakeState(intake.state) not in {IntakeState.CREATED, IntakeState.UPLOADING}:
            raise _conflict(intake, "attach a manifest to")
        key = ArtifactKey(f"intakes/{intake_id.hex}/client-manifest.json")
        await asyncio.to_thread(
            container.artifacts.put_bytes, key, manifest.model_dump_json().encode(), overwrite=True
        )
        intake.client_manifest_key = str(key)
        await session.flush()
        await session.refresh(intake)
        return _response(intake, container.settings)


@router.post(
    "/intakes/{intake_id}/finalize",
    status_code=202,
    response_model=IntakeResponse,
    responses={**_ERRORS, 503: {"model": ErrorResponse}},
)
async def finalize_intake(
    intake_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> IntakeResponse:
    """Idempotent: repeated calls return the current state and never start a second validation."""
    async with transaction(container.session_factory) as session:
        intake = await _intake(session, principal, intake_id, for_update=True)
        state = IntakeState(intake.state)
        if state is IntakeState.CREATED:
            raise ApiError(409, "no_content", "Upload the archive before finalizing")
        if state is IntakeState.CANCELED:
            raise _conflict(intake, "finalize")
        if state is IntakeState.UPLOADING:
            if intake.mode == "local_runner" and intake.client_manifest_key is None:
                raise ApiError(
                    409,
                    "client_manifest_required",
                    "Local-runner intakes need their manifest first",
                )
            intake.state = IntakeState.VALIDATING.value
            intake.workflow_id = f"crp-intake-{intake.id.hex}"
        needs_start = IntakeState(intake.state) is IntakeState.VALIDATING
        await session.flush()
        await session.refresh(intake)
        response = _response(intake, container.settings)
    if needs_start:
        try:
            await container.workflows.start_intake(intake_id)
        except WorkflowUnavailableError as exc:
            raise ApiError(
                503, "workflow_unavailable", f"{exc}; retry finalize (it is idempotent)"
            ) from exc
    return response


@router.post("/intakes/{intake_id}/cancel", response_model=IntakeResponse, responses=_ERRORS)
async def cancel_intake(
    intake_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> IntakeResponse:
    async with transaction(container.session_factory) as session:
        intake = await _intake(session, principal, intake_id, for_update=True)
        state = IntakeState(intake.state)
        if state is IntakeState.CANCELED:
            return _response(intake, container.settings)
        if state.is_terminal:
            raise _conflict(intake, "cancel")
        intake.state = IntakeState.CANCELED.value
        intake.finalized_at = datetime.now(UTC)
        keys = [k for k in (intake.archive_key, intake.client_manifest_key) if k]
        await session.flush()
        await session.refresh(intake)
        response = _response(intake, container.settings)
    for key in keys:
        await asyncio.to_thread(container.artifacts.delete, ArtifactKey(key))
    return response


async def expire_abandoned(container: Any) -> int:
    """Cancel CREATED/UPLOADING intakes past their expiry and delete their temporary bytes."""
    now = datetime.now(UTC)
    async with transaction(container.session_factory) as session:
        rows = list(
            (
                await session.execute(
                    select(Intake)
                    .where(Intake.state.in_(["CREATED", "UPLOADING"]), Intake.expires_at < now)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        keys = []
        for row in rows:
            row.state = IntakeState.CANCELED.value
            row.error_code = "expired"
            row.error_message = "The intake expired before it was finalized"
            row.finalized_at = now
            keys += [k for k in (row.archive_key, row.client_manifest_key) if k]
    for key in keys:
        await asyncio.to_thread(container.artifacts.delete, ArtifactKey(key))
    return len(rows)
