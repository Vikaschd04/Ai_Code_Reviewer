"""NFR readiness (P12 slice 1; docs/NFR_ASSESSMENT.md).

Answers the built-in questionnaire for a project from the newest reviewed upload (declared
libraries and files), the project's tracked issues and the team's NFR profile. The profile is
saved as append-only versions (members); the assessment is computed on request and can be
exported as CSV or Markdown. Nothing found is "not checked yet", never "met"; compliance is
never certified.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, cast

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from crp_analysis.nfr.assessment import (
    STATUS_LABELS,
    Assessment,
    to_csv,
    to_markdown,
)
from crp_analysis.nfr.profile import TARGETS, ProfileError, from_document
from crp_analysis.nfr.questionnaire import load
from crp_analysis.nfr.signals import Evidence
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    NfrAnswerView,
    NfrAspectResult,
    NfrAssessmentResponse,
    NfrBasis,
    NfrEvidenceItem,
    NfrEvidenceLocation,
    NfrGaps,
    NfrIssueRef,
    NfrProfileDocument,
    NfrProfileUpdate,
    NfrProfileVersionSummary,
    NfrQuestionResult,
    NfrStatus,
    NfrTargetSpec,
)
from crp_api.services import nfr
from crp_api.services.scope import get_scoped
from crp_core.db.models import NfrProfileVersion, Project
from crp_core.db.session import transaction
from crp_core.domain.states import MembershipRole

router = APIRouter(tags=["nfr"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _evidence(items: list[Evidence]) -> list[NfrEvidenceItem]:
    return [
        NfrEvidenceItem(
            signal=e.signal,
            label=e.label,
            kind="context" if e.kind == "context" else "supports",
            count=e.count,
            locations=[NfrEvidenceLocation(path=p, line=n, detail=d) for p, n, d in e.locations],
        )
        for e in items
    ]


async def _compute(session: Any, project: Project) -> tuple[Assessment, Any, Any]:
    gathered = await nfr.gather(session, project.id)
    return gathered.assessment, gathered.basis, (gathered.versions, gathered.profile)


def _response(
    project: Project, result: Assessment, found: Any, saved: Any, can_edit: bool
) -> NfrAssessmentResponse:
    versions, profile = saved
    latest = versions[0] if versions else None
    return NfrAssessmentResponse(
        project_id=project.id,
        questionnaire_source=load().source,
        basis=NfrBasis(
            snapshot_id=found.snapshot_id, scan_id=found.scan_id, reviewed_at=found.reviewed_at
        )
        if found
        else None,
        counts=result.counts,
        status_labels=STATUS_LABELS,
        aspects=[
            NfrAspectResult(
                id=a.aspect.id,
                name=a.aspect.name,
                iso=a.aspect.iso,
                counts=a.counts,
                questions=[
                    NfrQuestionResult(
                        id=r.question.id,
                        text=r.question.text,
                        help=r.question.help,
                        status=cast(NfrStatus, r.status),
                        team_required=r.question.team_required,
                        profile_fields=list(r.question.profile_fields),
                        evidence=_evidence(r.evidence),
                        context=_evidence(r.context),
                        gaps=NfrGaps(
                            open=r.gaps.open,
                            accepted=r.gaps.accepted,
                            by_severity=r.gaps.by_severity,
                            top=[
                                NfrIssueRef(
                                    id=uuid.UUID(i.id),
                                    title=i.title,
                                    severity=i.severity,
                                    path=i.path,
                                    engine=i.engine,
                                    rule_id=i.rule_id,
                                )
                                for i in r.gaps.top
                            ],
                        ),
                        answer=NfrAnswerView(
                            text=r.answer.text,
                            not_applicable=r.answer.not_applicable,
                            reason=r.answer.reason,
                        )
                        if r.answer
                        else None,
                        values=cast(dict[str, float | int | str | list[str]], r.values),
                    )
                    for r in a.questions
                ],
            )
            for a in result.aspects
        ],
        profile_version=latest[0].version if latest else 0,
        profile=NfrProfileDocument.model_validate(profile.to_document()),
        profile_note=latest[0].note if latest else None,
        profile_saved_by=latest[1] if latest else None,
        profile_saved_at=latest[0].created_at if latest else None,
        can_edit=can_edit,
        history=[
            NfrProfileVersionSummary(
                version=v.version,
                sha256=v.sha256,
                note=v.note,
                created_at=v.created_at,
                created_by=name,
            )
            for v, name in versions
        ],
        targets=[
            NfrTargetSpec(
                name=t.name,
                label=t.label,
                kind=cast(Literal["int", "float", "text"], t.kind),
                unit=t.unit,
                low=t.low,
                high=t.high,
            )
            for t in TARGETS
        ],
    )


@router.get("/projects/{project_id}/nfr", response_model=NfrAssessmentResponse, responses=_ERRORS)
async def get_nfr_assessment(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> NfrAssessmentResponse:
    """NFR readiness: every question with evidence, open issues and the team's answers."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        result, found, saved = await _compute(session, project)
        return _response(
            project,
            result,
            found,
            saved,
            principal.has_role(project.workspace_id, MembershipRole.MEMBER),
        )


@router.put(
    "/projects/{project_id}/nfr/profile",
    response_model=NfrAssessmentResponse,
    responses=_ERRORS,
)
async def save_nfr_profile(
    project_id: uuid.UUID,
    body: NfrProfileUpdate,
    principal: CurrentPrincipal,
    container: Container,
) -> NfrAssessmentResponse:
    """Save the team's targets and attested answers as a new profile version (members)."""
    try:
        profile = from_document(body.document.model_dump(by_alias=True, exclude_none=True))
    except ProfileError as exc:
        raise ApiError(
            422, "invalid_profile", "The NFR profile is not valid", {"problems": exc.problems}
        ) from None
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        versions = await nfr.history(session, project.id, limit=1)
        current = versions[0][0].version if versions else 0
        if body.base_version != current:
            raise ApiError(
                409,
                "version_conflict",
                "The profile changed since you loaded it; reload and try again",
                {"current_version": current},
            )
        sha256 = profile.sha256()
        if not versions or versions[0][0].sha256 != sha256:
            session.add(
                NfrProfileVersion(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    version=current + 1,
                    document=profile.to_document(),
                    sha256=sha256,
                    note=(body.note or "").strip() or None,
                    created_by=principal.user_id,
                )
            )
            await session.flush()
        result, found, saved = await _compute(session, project)
        return _response(project, result, found, saved, can_edit=True)


@router.get(
    "/projects/{project_id}/nfr/export",
    response_class=PlainTextResponse,
    responses={**_ERRORS, 200: {"content": {"text/csv": {}, "text/markdown": {}}}},
)
async def export_nfr_assessment(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    format: Literal["csv", "md"] = Query(default="csv"),
) -> PlainTextResponse:
    """The questionnaire with every answer, as CSV (spreadsheets) or a Markdown report."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        result, found, saved = await _compute(session, project)
        name = project.name
        version = saved[0][0][0].version if saved[0] else 0
    if format == "csv":
        body, media, extension = to_csv(result), "text/csv", "csv"
    else:
        basis = (
            f"Evidence from the review of upload {found.snapshot_id} ({found.reviewed_at:%d %B %Y})"
            if found and found.reviewed_at
            else "No reviewed upload yet: there is no code evidence."
        )
        basis += f"; NFR profile version {version}." if version else "; no NFR profile saved."
        body = to_markdown(result, project=name, source=load().source, basis=basis)
        media, extension = "text/markdown", "md"
    return PlainTextResponse(
        body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="nfr-readiness.{extension}"',
            "Cache-Control": "no-store",
        },
    )
