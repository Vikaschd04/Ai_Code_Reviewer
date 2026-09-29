"""Sample project: a small, deliberately flawed online store for trying the product.

The sample goes through the normal intake path (archive in the artifact store, validation and
freezing by the worker), so its snapshot and scan are real. Its fake credential is generated
here, never stored in the repository, like the test fixtures' secrets.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import random
import string
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from importlib.resources.abc import Traversable

from crp_api.auth.principal import Principal
from crp_api.container import AppContainer
from crp_api.services import demo
from crp_api.services import projects as project_service
from crp_core.artifacts import ArtifactKey
from crp_core.db.models import Intake, Project, Source
from crp_core.db.session import transaction
from crp_core.domain.states import IntakeState, ProjectOrigin
from crp_core.workflows.gateway import WorkflowUnavailableError

SAMPLE_NAME = "Sample: Online store"
SAMPLE_SLUG = "sample-online-store"
SAMPLE_ARCHIVE_NAME = "online-store-sample.zip"
SAMPLE_DESCRIPTION = (
    "A small Java and TypeScript web shop with deliberate problems: security flaws, bugs, "
    "vulnerable dependencies and a leaked (fake) access token. Use it to try a full review."
)
_FIXED_TIME = (2026, 1, 1, 0, 0, 0)
_MAX_SLUG_ATTEMPTS = 50


@dataclass(frozen=True, slots=True)
class CreatedSample:
    project: Project
    intake: Intake
    start_error: str | None


def _files(folder: Traversable, prefix: str = "") -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        path = f"{prefix}{entry.name}"
        if entry.is_dir():
            found.extend(_files(entry, f"{path}/"))
        elif entry.name != "__pycache__":
            found.append((path, entry.read_bytes()))
    return found


def _fake_token() -> str:
    rng = random.Random(20260930)  # noqa: S311 - deterministic fake data, not cryptography
    alphabet = string.ascii_letters + string.digits
    return "ghp_" + "".join(rng.choice(alphabet) for _ in range(36))


def sample_archive() -> bytes:
    """Deterministic ZIP of the sample project (same bytes on every call)."""
    files = _files(resources.files("crp_api").joinpath("samples", "online_store"))
    files.append(
        (
            "web/src/config.js",
            (
                "// Fake credential generated for the refactorX sample project (never valid).\n"
                f'export const githubToken = "{_fake_token()}";\n'
            ).encode(),
        )
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files):
            info = zipfile.ZipInfo(f"online-store/{path}", date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


async def create_sample_project(
    container: AppContainer, principal: Principal, workspace_id: uuid.UUID
) -> CreatedSample:
    """Create the sample project and start freezing its snapshot (like upload + finalize)."""
    settings = container.settings
    async with transaction(container.session_factory) as session:
        if principal.is_demo and principal.role_in(workspace_id) is not None:
            await demo.enforce_project_quota(session, workspace_id, settings)
        project: Project | None = None
        for attempt in range(1, _MAX_SLUG_ATTEMPTS + 1):
            try:
                project = await project_service.create_project(
                    session,
                    principal,
                    workspace_id=workspace_id,
                    name=SAMPLE_NAME if attempt == 1 else f"{SAMPLE_NAME} ({attempt})",
                    slug=SAMPLE_SLUG if attempt == 1 else f"{SAMPLE_SLUG}-{attempt}",
                    description=SAMPLE_DESCRIPTION,
                    origin=ProjectOrigin.SYNTHETIC_FIXTURE,
                )
                break
            except project_service.ProjectSlugConflictError:
                continue
        if project is None:
            raise project_service.ProjectSlugConflictError(SAMPLE_SLUG)
        source = Source(
            workspace_id=workspace_id,
            project_id=project.id,
            mode="zip_upload",
            display_name=SAMPLE_ARCHIVE_NAME,
        )
        session.add(source)
        await session.flush()
        intake = Intake(
            workspace_id=workspace_id,
            project_id=project.id,
            source_id=source.id,
            mode="zip_upload",
            state=IntakeState.CREATED.value,
            created_by=principal.user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.intake_expiry_seconds),
        )
        session.add(intake)
        await session.flush()
        intake_id, project_id = intake.id, project.id

    data = sample_archive()
    key = ArtifactKey(f"intakes/{intake_id.hex}/archive.zip")
    await asyncio.to_thread(container.artifacts.put_bytes, key, data, overwrite=True)

    async with transaction(container.session_factory) as session:
        stored = await session.get(Intake, intake_id, with_for_update=True)
        created = await session.get(Project, project_id)
        if stored is None or created is None:
            raise LookupError("sample project disappeared while it was being created")
        stored.archive_key = str(key)
        stored.archive_sha256 = hashlib.sha256(data).hexdigest()
        stored.archive_bytes = len(data)
        stored.state = IntakeState.VALIDATING.value
        stored.workflow_id = f"crp-intake-{stored.id.hex}"
        await session.flush()
        await session.refresh(stored)
        await session.refresh(created)
    start_error: str | None = None
    try:
        await container.workflows.start_intake(intake_id)
    except WorkflowUnavailableError as exc:  # the intake stays VALIDATING; finalize retries
        start_error = str(exc)
    return CreatedSample(project=created, intake=stored, start_error=start_error)
