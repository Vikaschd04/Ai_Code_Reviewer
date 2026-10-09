"""Validated fixes (P05; ADR 0014): options, proposals, edits, validation runs and exports.

A proposal is prepared from a deterministic recipe for one finding and bound to the finding's
upload (snapshot), the file's base hash and the patch hash. It is applied only to copies. Editing
a proposal resets its validation. Patches download as unified diffs that apply to exactly the
upload they were made for; moving a fix to a newer upload creates a new proposal that must be
validated again.
"""

from __future__ import annotations

import asyncio
import posixpath
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.fixes import policy, recipes
from crp_analysis.fixes.export import build_fix_export
from crp_analysis.fixes.patching import (
    Edit,
    PatchError,
    apply_edits,
    changed_lines,
    relocate,
    sha256_text,
    unified_diff,
)
from crp_analysis.manifest import blob_key
from crp_analysis.sources import publication
from crp_analysis.sources.github import (
    GitHubAccessError,
    GitHubConflictError,
    GitHubError,
    RepoAccess,
)
from crp_api import __version__
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    FixCreate,
    FixEditResponse,
    FixEditsUpdate,
    FixFindingSummary,
    FixOption,
    FixOptions,
    FixProposalPage,
    FixProposalResponse,
    FixPullRequestResponse,
    FixRebase,
    FixReject,
    FixStepResponse,
    FixValidationResponse,
)
from crp_api.services import git as git_service
from crp_api.services.scope import get_scoped
from crp_core.artifacts import ArtifactKey
from crp_core.db.models import (
    FileEntry,
    Finding,
    FixProposal,
    FixPullRequest,
    FixValidation,
    Project,
    Snapshot,
)
from crp_core.db.session import transaction
from crp_core.domain.states import (
    CaptureStatus,
    FileDisposition,
    FixKind,
    FixProposalState,
    FixValidationState,
    MembershipRole,
)
from crp_core.workflows.contracts import fix_validation_workflow_id
from crp_core.workflows.gateway import WorkflowUnavailableError

router = APIRouter(tags=["fixes"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}
ACTIVE_VALIDATION = {FixValidationState.QUEUED.value, FixValidationState.RUNNING.value}


# -- helpers ------------------------------------------------------------------------------------


async def _text(container: Any, entry: FileEntry) -> str:
    if entry.blob_sha256 is None or entry.disposition != FileDisposition.ANALYZABLE.value:
        raise ApiError(409, "content_not_stored", "The file's content is not stored")
    data = await asyncio.to_thread(
        container.artifacts.read_bytes,
        ArtifactKey(blob_key(entry.blob_sha256)),
        max_bytes=container.settings.intake_max_text_file_bytes,
    )
    return str(data.decode("utf-8", errors="replace"))


async def _project_file_reader(
    session: AsyncSession, container: Any, snapshot_id: uuid.UUID, near: str
) -> recipes.ReadFile:
    """Read callback for recipes: the ``sfdx-project.json`` closest above ``near``."""
    candidates = (
        await session.scalars(
            select(FileEntry).where(
                FileEntry.snapshot_id == snapshot_id,
                FileEntry.path.like("%sfdx-project.json"),
                FileEntry.disposition == FileDisposition.ANALYZABLE.value,
            )
        )
    ).all()
    best: FileEntry | None = None
    for entry in candidates:
        directory = posixpath.dirname(entry.path)
        if (not directory or near.startswith(directory + "/")) and (
            best is None or len(directory) > len(posixpath.dirname(best.path))
        ):
            best = entry
    project_text = await _text(container, best) if best is not None else None

    def read(name: str) -> str | None:
        return project_text if name == "sfdx-project.json" else None

    return read


async def _context(
    session: AsyncSession, container: Any, finding: Finding
) -> tuple[FileEntry, str, recipes.FindingInfo]:
    entry = await session.get(FileEntry, finding.file_entry_id)
    if entry is None:
        raise ApiError(404, "file_not_found", "The finding's file is not in this upload")
    info = recipes.FindingInfo(
        finding.engine,
        finding.rule_id,
        entry.path,
        finding.start_line,
        finding.end_line,
        finding.details,
    )
    return entry, await _text(container, entry), info


def _validation(row: FixValidation, proposal: FixProposal) -> FixValidationResponse:
    return FixValidationResponse(
        id=row.id,
        proposal_id=row.proposal_id,
        state=row.state,
        patch_sha256=row.patch_sha256,
        result_sha256=row.result_sha256,
        current=row.patch_sha256 == proposal.patch_sha256,
        steps=[FixStepResponse.model_validate(step) for step in row.steps or []],
        summary=row.summary,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cancel_requested_at=row.cancel_requested_at,
    )


def _labels(
    proposal: FixProposal, latest: FixValidation | None, snapshot: Snapshot | None = None
) -> list[str]:
    if snapshot is not None and snapshot.git_commit and snapshot.git_ref:
        first = (
            f"Applies to commit {snapshot.git_commit[:7]} of {snapshot.git_ref} only; a newer "
            "commit needs the fix moved and checked again."
        )
    else:
        first = (
            f"Applies to this upload only (snapshot {str(proposal.snapshot_id)[:8]}); a newer "
            "upload needs the fix moved and checked again."
        )
    labels = [first]
    current = latest is not None and latest.patch_sha256 == proposal.patch_sha256
    if proposal.state == FixProposalState.REJECTED:
        labels.append("Rejected.")
    elif current and latest is not None and latest.state == FixValidationState.PASSED:
        labels.append("Source checks passed for this exact patch.")
        labels.append("Not compiled, built or tested: no isolated runner or platform environment.")
    elif current and latest is not None and latest.state == FixValidationState.FAILED:
        labels.append("Validation failed for this exact patch.")
    else:
        labels.append("Not validated yet for this exact patch.")
    return labels


async def _response(session: AsyncSession, proposal: FixProposal) -> FixProposalResponse:
    finding = await session.get(Finding, proposal.finding_id)
    latest = await session.scalar(
        select(FixValidation)
        .where(FixValidation.proposal_id == proposal.id)
        .order_by(FixValidation.created_at.desc())
        .limit(1)
    )
    return FixProposalResponse(
        id=proposal.id,
        project_id=proposal.project_id,
        snapshot_id=proposal.snapshot_id,
        scan_id=proposal.scan_id,
        finding_id=proposal.finding_id,
        kind=proposal.kind,
        recipe_id=proposal.recipe_id,
        title=proposal.title,
        explanation=proposal.explanation,
        behaviour_note=proposal.behaviour_note,
        state=proposal.state,
        path=proposal.path,
        base_sha256=proposal.base_sha256,
        result_sha256=proposal.result_sha256,
        patch_sha256=proposal.patch_sha256,
        patch=proposal.patch,
        edits=[FixEditResponse.model_validate(e) for e in proposal.edits],
        changed_lines=proposal.changed_lines,
        edited=proposal.edited,
        validations_used=proposal.validations_used,
        max_validations=proposal.max_validations,
        rejected_reason=proposal.rejected_reason,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        version=proposal.version,
        finding=FixFindingSummary(
            id=finding.id,
            title=finding.title,
            engine=finding.engine,
            rule_id=finding.rule_id,
            severity=finding.severity,
            start_line=finding.start_line,
        )
        if finding is not None
        else None,
        latest_validation=_validation(latest, proposal) if latest is not None else None,
        labels=_labels(proposal, latest, await session.get(Snapshot, proposal.snapshot_id)),
        **(await _pull_request_state(session, proposal, latest)),
    )


def _pr_response(row: FixPullRequest) -> FixPullRequestResponse:
    return FixPullRequestResponse(
        number=row.number,
        url=row.url,
        repository=row.repository,
        branch=row.branch,
        base_ref=row.base_ref,
        base_sha=row.base_sha,
        commit_sha=row.commit_sha,
        created_at=row.created_at,
    )


@dataclass(frozen=True, slots=True)
class _PrTarget:
    access: RepoAccess
    branch: str
    base_sha: str
    tree_sha: str
    mode: str


async def _pull_request_target(
    session: AsyncSession, proposal: FixProposal, latest: FixValidation | None
) -> tuple[_PrTarget | None, str | None]:
    """Where a pull request for this fix would go, or why it cannot be opened (plain text)."""
    snapshot = await session.get(Snapshot, proposal.snapshot_id)
    if snapshot is None:
        return None, None
    target, reason = await git_service.publish_target(session, proposal.project_id, snapshot)
    if target is None:
        return None, reason
    passed = (
        latest is not None
        and latest.state == FixValidationState.PASSED.value
        and latest.patch_sha256 == proposal.patch_sha256
        and proposal.state == FixProposalState.VALIDATED.value
    )
    if not passed:
        return (
            None,
            "Run the checks first: only fixes whose checks passed can become pull requests.",
        )
    mode = "100755" if proposal.path in target.executable else "100644"
    return (
        _PrTarget(target.access, target.branch, target.base_sha, target.tree_sha, mode),
        None,
    )


async def _pull_request_state(
    session: AsyncSession, proposal: FixProposal, latest: FixValidation | None
) -> dict[str, Any]:
    existing = await session.scalar(
        select(FixPullRequest).where(FixPullRequest.proposal_id == proposal.id)
    )
    if existing is not None:
        return {"pull_request": _pr_response(existing), "pull_request_available": False}
    target, reason = await _pull_request_target(session, proposal, latest)
    return {"pull_request_available": target is not None, "pull_request_reason": reason}


def _md(text: object) -> str:
    return publication.md(text)


def _pull_request_text(
    proposal: FixProposal, finding: Finding | None, latest: FixValidation, short_sha: str
) -> tuple[str, str, str]:
    title = f"refactorX: {proposal.title}"[:250]
    where = f"`{proposal.path}`" if "`" not in proposal.path else _md(proposal.path)
    lines = ["Opened from refactorX by a reviewer for a finding in this repository.", ""]
    if finding is not None:
        line = f" line {finding.start_line}" if finding.start_line else ""
        lines.append(
            f"**Finding:** {_md(finding.title)} ({_md(finding.engine)} {_md(finding.rule_id)}) "
            f"in {where}{line}"
        )
    lines.append(f"**Fix:** {_md(proposal.title)}: {_md(proposal.explanation)}")
    if proposal.behaviour_note:
        lines.append(f"**What to watch:** {_md(proposal.behaviour_note)}")
    lines += ["", f"Checks refactorX ran on a copy of commit {short_sha}:"]
    marks = {"passed": "Passed", "failed": "Failed", "not_run": "Not run"}
    for step in latest.steps or []:
        state = marks.get(str(step.get("state")), str(step.get("state")))
        lines.append(f"- {state}: {_md(step.get('label'))}. {_md(step.get('detail'))}")
    lines += [
        "",
        "refactorX did not compile, build or test this change. Review it and let your own CI "
        "run before merging.",
    ]
    message = (
        f"{proposal.title}\n\nFixes {finding.title if finding else 'a finding'} in "
        f"{proposal.path}.\nPrepared and checked by refactorX at source level; not built or "
        "tested."
    )
    return title, "\n".join(lines), message


def _patch_fields(path: str, base: str, edits: list[Edit]) -> dict[str, Any]:
    try:
        after = apply_edits(base, edits)
    except PatchError as exc:
        raise ApiError(409, "patch_conflict", str(exc), {"reason": exc.code}) from exc
    violations = policy.check(path, base, after, frozenset({path}))
    if violations:
        raise ApiError(
            422,
            "fix_not_allowed",
            violations[0].message,
            {"violations": [v.code for v in violations]},
        )
    patch = unified_diff(path, base, after)
    if not patch:
        raise ApiError(422, "no_change", "The fix does not change the file")
    return {
        "edits": [e.to_json() for e in edits],
        "result_sha256": sha256_text(after),
        "patch": patch,
        "patch_sha256": sha256_text(patch),
        "changed_lines": changed_lines(base, after),
    }


# -- options and proposals ------------------------------------------------------------------------


@router.get("/findings/{finding_id}/fix-options", response_model=FixOptions, responses=_ERRORS)
async def fix_options(
    finding_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> FixOptions:
    """Fixes that can be prepared for this finding (deterministic recipes), with reasons."""
    async with transaction(container.session_factory) as session:
        finding = await get_scoped(
            session, principal, Finding, finding_id, not_found="finding_not_found"
        )
        entry, text, info = await _context(session, container, finding)
        choices = recipes.options(info)
        read = (
            await _project_file_reader(session, container, finding.snapshot_id, entry.path)
            if "salesforce:api-version" in choices
            else (lambda _name: None)
        )
    result: list[FixOption] = []
    for recipe_id in choices:
        try:
            proposal = recipes.propose(recipe_id, info, text, read)
            result.append(
                FixOption(recipe_id=recipe_id, title=proposal.title, available=True, reason=None)
            )
        except recipes.NoFix as exc:
            result.append(
                FixOption(recipe_id=recipe_id, title=recipe_id, available=False, reason=str(exc))
            )
    return FixOptions(finding_id=finding_id, options=result)


@router.post(
    "/findings/{finding_id}/fix-proposals",
    status_code=201,
    response_model=FixProposalResponse,
    responses={**_ERRORS, 422: {"model": ErrorResponse}},
)
async def create_fix_proposal(
    finding_id: uuid.UUID, body: FixCreate, principal: CurrentPrincipal, container: Container
) -> FixProposalResponse:
    """Prepare a fix for the finding with a deterministic recipe (applied to a copy only)."""
    async with transaction(container.session_factory) as session:
        finding = await get_scoped(
            session,
            principal,
            Finding,
            finding_id,
            not_found="finding_not_found",
            required=MembershipRole.MEMBER,
        )
        entry, text, info = await _context(session, container, finding)
        if body.recipe_id not in recipes.options(info):
            raise ApiError(422, "unknown_recipe", "This fix does not apply to the finding's rule")
        read = await _project_file_reader(session, container, finding.snapshot_id, entry.path)
        try:
            proposed = recipes.propose(body.recipe_id, info, text, read)
        except recipes.NoFix as exc:
            raise ApiError(409, "no_fix", str(exc)) from exc
        fields = _patch_fields(entry.path, text, list(proposed.edits))
        existing = await session.scalar(
            select(FixProposal).where(
                FixProposal.finding_id == finding.id,
                FixProposal.recipe_id == proposed.recipe_id,
                FixProposal.patch_sha256 == fields["patch_sha256"],
                FixProposal.state != FixProposalState.REJECTED.value,
            )
        )
        if existing is not None:
            return await _response(session, existing)
        proposal = FixProposal(
            workspace_id=finding.workspace_id,
            project_id=finding.project_id,
            snapshot_id=finding.snapshot_id,
            scan_id=finding.scan_id,
            finding_id=finding.id,
            kind=FixKind.RECIPE.value,
            recipe_id=proposed.recipe_id,
            title=proposed.title,
            explanation=proposed.explanation,
            behaviour_note=proposed.behaviour_note,
            state=FixProposalState.PROPOSED.value,
            path=entry.path,
            target_line=finding.start_line,
            allowed_paths=[entry.path],
            base_sha256=sha256_text(text),
            created_by=principal.user_id,
            **fields,
        )
        session.add(proposal)
        await session.flush()
        await session.refresh(proposal)
        return await _response(session, proposal)


@router.get(
    "/projects/{project_id}/fix-proposals", response_model=FixProposalPage, responses=_ERRORS
)
async def list_fix_proposals(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    finding_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> FixProposalPage:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        query = select(FixProposal).where(FixProposal.project_id == project.id)
        if finding_id is not None:
            query = query.where(FixProposal.finding_id == finding_id)
        rows = (
            await session.scalars(query.order_by(FixProposal.created_at.desc()).limit(limit))
        ).all()
        return FixProposalPage(items=[await _response(session, row) for row in rows])


@router.get("/fix-proposals/{proposal_id}", response_model=FixProposalResponse, responses=_ERRORS)
async def get_fix_proposal(
    proposal_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> FixProposalResponse:
    async with transaction(container.session_factory) as session:
        proposal = await get_scoped(
            session, principal, FixProposal, proposal_id, not_found="fix_not_found"
        )
        return await _response(session, proposal)


async def _editable(session: AsyncSession, principal: Any, proposal_id: uuid.UUID) -> FixProposal:
    proposal = await get_scoped(
        session,
        principal,
        FixProposal,
        proposal_id,
        not_found="fix_not_found",
        required=MembershipRole.MEMBER,
        for_update=True,
    )
    if proposal.state == FixProposalState.REJECTED.value:
        raise ApiError(409, "fix_rejected", "This fix was rejected")
    return proposal


@router.put(
    "/fix-proposals/{proposal_id}/edits",
    response_model=FixProposalResponse,
    responses={**_ERRORS, 422: {"model": ErrorResponse}},
)
async def edit_fix_proposal(
    proposal_id: uuid.UUID, body: FixEditsUpdate, principal: CurrentPrincipal, container: Container
) -> FixProposalResponse:
    """Change the replacement lines of the fix. The lines it replaces and the file stay fixed;
    the policy still applies and validation starts over."""
    async with transaction(container.session_factory) as session:
        proposal = await _editable(session, principal, proposal_id)
        if body.version != proposal.version:
            raise ApiError(409, "version_conflict", "The fix changed meanwhile; reload it")
        edits = [Edit.from_json(e) for e in proposal.edits]
        by_start = {e.start_line: e for e in edits}
        for update in body.edits:
            if update.start_line not in by_start:
                raise ApiError(422, "unknown_edit", "No change starts on that line")
            current = by_start[update.start_line]
            by_start[update.start_line] = Edit(
                current.path,
                current.start_line,
                current.end_line,
                current.original,
                tuple(update.replacement),
            )
        entry = await session.scalar(
            select(FileEntry).where(
                FileEntry.snapshot_id == proposal.snapshot_id, FileEntry.path == proposal.path
            )
        )
        if entry is None:
            raise ApiError(404, "file_not_found", "The fix's file is not in this upload")
        base = await _text(container, entry)
        fields = _patch_fields(proposal.path, base, list(by_start.values()))
        for key, value in fields.items():
            setattr(proposal, key, value)
        proposal.edited = True
        proposal.state = FixProposalState.PROPOSED.value
        await session.flush()
        await session.refresh(proposal)
        return await _response(session, proposal)


@router.post(
    "/fix-proposals/{proposal_id}/reject", response_model=FixProposalResponse, responses=_ERRORS
)
async def reject_fix_proposal(
    proposal_id: uuid.UUID, body: FixReject, principal: CurrentPrincipal, container: Container
) -> FixProposalResponse:
    async with transaction(container.session_factory) as session:
        proposal = await _editable(session, principal, proposal_id)
        proposal.state = FixProposalState.REJECTED.value
        proposal.rejected_reason = body.reason
        await session.flush()
        await session.refresh(proposal)
        return await _response(session, proposal)


# -- validation -----------------------------------------------------------------------------------


@router.post(
    "/fix-proposals/{proposal_id}/validations",
    status_code=202,
    response_model=FixValidationResponse,
    responses={**_ERRORS, 503: {"model": ErrorResponse}},
)
async def validate_fix_proposal(
    proposal_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> FixValidationResponse:
    """Run the validation ladder for the fix's current patch (copies only; no code executed)."""
    async with transaction(container.session_factory) as session:
        proposal = await _editable(session, principal, proposal_id)
        active = await session.scalar(
            select(FixValidation.id).where(
                FixValidation.proposal_id == proposal.id,
                FixValidation.state.in_(ACTIVE_VALIDATION),
            )
        )
        if active is not None:
            raise ApiError(409, "validation_running", "A validation of this fix is running")
        if proposal.validations_used >= proposal.max_validations:
            raise ApiError(
                409,
                "validation_budget_exhausted",
                f"This fix was validated {proposal.max_validations} times; prepare a new one",
            )
        validation = FixValidation(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            workspace_id=proposal.workspace_id,
            state=FixValidationState.QUEUED.value,
            patch_sha256=proposal.patch_sha256,
            result_sha256=proposal.result_sha256,
            requested_by=principal.user_id,
        )
        validation.workflow_id = fix_validation_workflow_id(validation.id)
        session.add(validation)
        proposal.validations_used += 1
        proposal.state = FixProposalState.VALIDATING.value
        await session.flush()
        await session.refresh(validation)
        response = _validation(validation, proposal)
    try:
        await container.workflows.start_fix_validation(validation.id)
    except WorkflowUnavailableError as exc:
        async with transaction(container.session_factory) as session:
            stored = await session.get(FixValidation, validation.id, with_for_update=True)
            owner = await session.get(FixProposal, proposal_id, with_for_update=True)
            if stored is not None and stored.state == FixValidationState.QUEUED.value:
                stored.state = FixValidationState.FAILED.value
                stored.error_code = "workflow_unavailable"
                stored.error_message = "The validation service is not running."
                stored.finished_at = datetime.now(UTC)
            if owner is not None and owner.state == FixProposalState.VALIDATING.value:
                owner.state = FixProposalState.PROPOSED.value
                owner.validations_used = max(0, owner.validations_used - 1)
        raise ApiError(503, "workflow_unavailable", f"{exc}; try again shortly") from exc
    return response


@router.post(
    "/fix-validations/{validation_id}/cancel",
    response_model=FixValidationResponse,
    responses={**_ERRORS, 503: {"model": ErrorResponse}},
)
async def cancel_fix_validation(
    validation_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> FixValidationResponse:
    async with transaction(container.session_factory) as session:
        validation = await get_scoped(
            session,
            principal,
            FixValidation,
            validation_id,
            not_found="fix_validation_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        terminal = FixValidationState(validation.state).is_terminal
        if not terminal and validation.cancel_requested_at is None:
            validation.cancel_requested_at = datetime.now(UTC)
        proposal = await session.get(FixProposal, validation.proposal_id)
        if proposal is None:
            raise ApiError(404, "fix_not_found", "The fix no longer exists")
        await session.flush()
        await session.refresh(validation)
        response = _validation(validation, proposal)
    if not terminal:
        try:
            await container.workflows.cancel_fix_validation(validation_id)
        except WorkflowUnavailableError as exc:
            raise ApiError(503, "workflow_unavailable", str(exc)) from exc
    return response


# -- exports and moving to another upload ---------------------------------------------------------


def _filename(proposal: FixProposal, suffix: str) -> str:
    return f"refactorx-fix-{proposal.id.hex[:12]}.{suffix}"


async def _export_parts(
    session: AsyncSession, principal: Any, proposal_id: uuid.UUID
) -> tuple[FixProposal, FixProposalResponse, Project | None, Snapshot | None]:
    proposal = await get_scoped(
        session, principal, FixProposal, proposal_id, not_found="fix_not_found"
    )
    response = await _response(session, proposal)
    return (
        proposal,
        response,
        await session.get(Project, proposal.project_id),
        await session.get(Snapshot, proposal.snapshot_id),
    )


@router.get(
    "/fix-proposals/{proposal_id}/patch",
    response_class=PlainTextResponse,
    responses={**_ERRORS, 200: {"content": {"text/x-diff": {}}}},
)
async def download_fix_patch(
    proposal_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> PlainTextResponse:
    """The patch as a unified diff for ``git apply -p1`` / ``patch -p1`` on exactly this upload."""
    async with transaction(container.session_factory) as session:
        proposal, response, _, snapshot = await _export_parts(session, principal, proposal_id)
    validation = response.latest_validation
    status = (
        f"{validation.state}{'' if validation.current else ' (an earlier version of this patch)'}"
        if validation
        else "not validated"
    )
    finding = response.finding
    header = [
        f"# refactorX fix {proposal.id}: {proposal.title}",
        f"# Finding: {finding.title} ({finding.engine} {finding.rule_id})" if finding else "#",
        f"# Upload (snapshot) {proposal.snapshot_id}, manifest sha256 "
        f"{snapshot.manifest_sha256 if snapshot else 'unknown'}",
        f"# File {proposal.path}: base sha256 {proposal.base_sha256}, "
        f"result sha256 {proposal.result_sha256}",
        f"# Patch sha256 {proposal.patch_sha256}; validation: {status}",
        "# Not compiled, built or tested by refactorX. Apply only to this exact upload: "
        "git apply -p1 <file>",
        "",
    ]
    return PlainTextResponse(
        "\n".join(header) + proposal.patch,
        media_type="text/x-diff",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(proposal, "diff")}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/fix-proposals/{proposal_id}/summary", responses=_ERRORS)
async def download_fix_summary(
    proposal_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> JSONResponse:
    """Change summary with validation evidence and known risks (``crp-fix-export/v1``)."""
    async with transaction(container.session_factory) as session:
        proposal, response, project, snapshot = await _export_parts(session, principal, proposal_id)
    document = build_fix_export(
        response.model_dump(mode="json"),
        project={"id": str(proposal.project_id), "name": project.name if project else ""},
        snapshot={
            "id": str(proposal.snapshot_id),
            "manifest_sha256": snapshot.manifest_sha256 if snapshot else None,
        },
        tool_version=__version__,
        generated_at=datetime.now(UTC).isoformat(),
    )
    return JSONResponse(
        document,
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(proposal, "json")}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/fix-proposals/{proposal_id}/pull-request",
    status_code=201,
    response_model=FixPullRequestResponse,
    responses={**_ERRORS, 503: {"model": ErrorResponse}},
)
async def open_pull_request(
    proposal_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> FixPullRequestResponse:
    """Open a pull request with this validated fix on the reviewed branch (never merged).

    The branch must still point at the reviewed commit; otherwise 409 ``stale_patch``: review
    the newer commit, move the fix there and check it again.
    """
    client = container.github
    if client is None:
        raise ApiError(503, "github_not_configured", "GitHub is not set up on this server")
    async with transaction(container.session_factory) as session:
        proposal = await _editable(session, principal, proposal_id)  # locked: one PR at a time
        existing = await session.scalar(
            select(FixPullRequest).where(FixPullRequest.proposal_id == proposal.id)
        )
        if existing is not None:
            return _pr_response(existing)
        latest = await session.scalar(
            select(FixValidation)
            .where(FixValidation.proposal_id == proposal.id)
            .order_by(FixValidation.created_at.desc())
            .limit(1)
        )
        target, reason = await _pull_request_target(session, proposal, latest)
        if target is None or latest is None:
            raise ApiError(409, "pull_request_unavailable", reason or "Not a GitHub commit")
        entry = (
            await session.execute(
                select(FileEntry).where(
                    FileEntry.snapshot_id == proposal.snapshot_id, FileEntry.path == proposal.path
                )
            )
        ).scalar_one_or_none()
        if entry is None:
            raise ApiError(404, "file_not_found", "The fix's file is not in this upload")
        base = await _text(container, entry)
        after = apply_edits(base, [Edit.from_json(e) for e in proposal.edits])
        if (
            sha256_text(after) != proposal.result_sha256
            or sha256_text(base) != proposal.base_sha256
        ):
            raise ApiError(409, "patch_conflict", "The stored file does not match the fix")
        finding = await session.get(Finding, proposal.finding_id)
        title, body, message = _pull_request_text(proposal, finding, latest, target.base_sha[:7])
        try:
            head = await client.branch_head(target.access, target.branch)
            if head != target.base_sha:
                raise ApiError(
                    409,
                    "stale_patch",
                    f"{target.branch} moved on since the reviewed commit; review the newer "
                    "commit, move the fix there and check it again",
                    {"reviewed": target.base_sha, "current": head},
                )
            commit = await client.create_fix_commit(
                target.access,
                parent_sha=target.base_sha,
                base_tree=target.tree_sha or await client.commit_tree(target.access, head),
                path=proposal.path,
                mode=target.mode,
                content=after.encode("utf-8"),
                message=message,
            )
            branch = f"refactorx/fix-{proposal.id.hex[:12]}"
            for attempt in range(1, 4):
                try:
                    await client.create_branch(target.access, branch, commit)
                    break
                except GitHubConflictError:
                    if attempt == 3:
                        raise
                    branch = f"refactorx/fix-{proposal.id.hex[:12]}-{attempt + 1}"
            number, url = await client.create_pull_request(
                target.access, head=branch, base=target.branch, title=title, body=body
            )
        except GitHubAccessError as exc:
            raise ApiError(409, exc.code, exc.message) from exc
        except GitHubConflictError as exc:
            raise ApiError(409, "github_rejected", exc.message) from exc
        except GitHubError as exc:
            raise ApiError(503, "github_unavailable", exc.message) from exc
        row = FixPullRequest(
            proposal_id=proposal.id,
            workspace_id=proposal.workspace_id,
            project_id=proposal.project_id,
            repository=target.access.full_name,
            branch=branch,
            base_ref=target.branch,
            base_sha=target.base_sha,
            commit_sha=commit,
            number=number,
            url=url[:500],
            created_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return _pr_response(row)


@router.post(
    "/fix-proposals/{proposal_id}/rebase",
    status_code=201,
    response_model=FixProposalResponse,
    responses={**_ERRORS, 422: {"model": ErrorResponse}},
)
async def rebase_fix_proposal(
    proposal_id: uuid.UUID, body: FixRebase, principal: CurrentPrincipal, container: Container
) -> FixProposalResponse:
    """Move the fix to another upload of the same project as a new, unvalidated proposal.

    Works only when the changed lines are still present unchanged (conflict otherwise)."""
    async with transaction(container.session_factory) as session:
        source = await get_scoped(
            session,
            principal,
            FixProposal,
            proposal_id,
            not_found="fix_not_found",
            required=MembershipRole.MEMBER,
        )
        target = await session.get(Snapshot, body.snapshot_id)
        if target is None or target.project_id != source.project_id:
            raise ApiError(404, "snapshot_not_found", "Upload not found in this project")
        if target.id == source.snapshot_id:
            raise ApiError(422, "same_snapshot", "The fix already belongs to this upload")
        if target.capture_status != CaptureStatus.FROZEN.value:
            raise ApiError(409, "snapshot_not_ready", "This upload is not ready yet")
        entry = await session.scalar(
            select(FileEntry).where(
                FileEntry.snapshot_id == target.id, FileEntry.path == source.path
            )
        )
        if entry is None or entry.disposition != FileDisposition.ANALYZABLE.value:
            raise ApiError(409, "patch_conflict", "The file is not in the other upload")
        text = await _text(container, entry)
        edits = [Edit.from_json(e) for e in source.edits]
        if sha256_text(text) != source.base_sha256:
            try:
                edits = [relocate(text, edit) for edit in edits]
            except PatchError as exc:
                raise ApiError(409, "patch_conflict", str(exc)) from exc
        fields = _patch_fields(source.path, text, edits)
        shift = edits[0].start_line - Edit.from_json(source.edits[0]).start_line
        proposal = FixProposal(
            workspace_id=source.workspace_id,
            project_id=source.project_id,
            snapshot_id=target.id,
            scan_id=source.scan_id,
            finding_id=source.finding_id,
            kind=source.kind,
            recipe_id=source.recipe_id,
            title=source.title,
            explanation=source.explanation,
            behaviour_note=source.behaviour_note,
            state=FixProposalState.PROPOSED.value,
            path=source.path,
            target_line=source.target_line + shift if source.target_line else None,
            rebased_from=source.id,
            allowed_paths=list(source.allowed_paths),
            base_sha256=sha256_text(text),
            edited=source.edited,
            created_by=principal.user_id,
            **fields,
        )
        session.add(proposal)
        await session.flush()
        await session.refresh(proposal)
        return await _response(session, proposal)
