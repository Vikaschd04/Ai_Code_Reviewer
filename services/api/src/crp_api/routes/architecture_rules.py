"""Intended architecture as code (P10 slice 2; ADR 0019).

A project's architecture rules are append-only versions: every save records the author, a note
and the canonical document, and the newest version applies to the next review (engine
``architecture``). Rules are accepted as JSON or YAML (parsed as data: no anchors, aliases or
tags) and exported as YAML. A read-only check evaluates rules, saved or not, against an upload's
current dependency map without creating findings.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from crp_analysis.architecture.rules import (
    RulesError,
    RuleSet,
    evaluate,
    from_document,
    parse_yaml,
    to_yaml,
)
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    ArchitectureLayerParts,
    ArchitectureRulesCheckRequest,
    ArchitectureRulesCheckResponse,
    ArchitectureRulesDocument,
    ArchitectureRulesResponse,
    ArchitectureRulesUpdate,
    ArchitectureRuleVersionSummary,
    ArchitectureViolationResponse,
)
from crp_api.services import architecture
from crp_api.services.scope import get_scoped
from crp_core.db.models import ArchitectureRuleVersion, GraphBuild, Project, Snapshot, User
from crp_core.db.session import transaction
from crp_core.domain.states import GraphBuildState, MembershipRole

router = APIRouter(tags=["architecture"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
HISTORY_LIMIT = 50
VIOLATION_LIMIT = 200
PART_LIMIT = 50


async def _history(
    session: Any, project_id: uuid.UUID
) -> list[tuple[ArchitectureRuleVersion, str | None]]:
    rows = await session.execute(
        select(ArchitectureRuleVersion, User.display_name)
        .outerjoin(User, User.id == ArchitectureRuleVersion.created_by)
        .where(ArchitectureRuleVersion.project_id == project_id)
        .order_by(ArchitectureRuleVersion.version.desc())
        .limit(HISTORY_LIMIT)
    )
    return [(version, name) for version, name in rows.all()]


async def _version(
    session: Any, project_id: uuid.UUID, number: int | None
) -> tuple[ArchitectureRuleVersion, str | None] | None:
    query = (
        select(ArchitectureRuleVersion, User.display_name)
        .outerjoin(User, User.id == ArchitectureRuleVersion.created_by)
        .where(ArchitectureRuleVersion.project_id == project_id)
    )
    if number is None:
        query = query.order_by(ArchitectureRuleVersion.version.desc()).limit(1)
    else:
        query = query.where(ArchitectureRuleVersion.version == number)
    row = (await session.execute(query)).first()
    return (row[0], row[1]) if row else None


def _count(document: dict[str, object], key: str) -> int:
    value = document.get(key)
    return len(value) if isinstance(value, list) else 0


def _summary(version: ArchitectureRuleVersion, name: str | None) -> ArchitectureRuleVersionSummary:
    return ArchitectureRuleVersionSummary(
        version=version.version,
        sha256=version.sha256,
        source="yaml" if version.source == "yaml" else "editor",
        note=version.note,
        created_at=version.created_at,
        created_by=name,
        layers=_count(version.document, "layers"),
        forbid=_count(version.document, "forbid"),
        allow=_count(version.document, "allow"),
    )


async def _response(
    session: Any, project: Project, number: int | None, can_edit: bool
) -> ArchitectureRulesResponse:
    history = await _history(session, project.id)
    shown = await _version(session, project.id, number)
    if number is not None and shown is None:
        raise ApiError(404, "rules_version_not_found", "This rules version does not exist")
    current = history[0][0].version if history else 0
    if shown is None:
        return ArchitectureRulesResponse(
            project_id=project.id,
            version=0,
            current_version=current,
            sha256=None,
            document=None,
            rule_ids=[],
            note=None,
            created_at=None,
            created_by=None,
            can_edit=can_edit,
            history=[],
        )
    version, name = shown
    rules = from_document(version.document)
    return ArchitectureRulesResponse(
        project_id=project.id,
        version=version.version,
        current_version=current,
        sha256=version.sha256,
        document=ArchitectureRulesDocument.model_validate(version.document),
        rule_ids=rules.rule_ids(),
        note=version.note,
        created_at=version.created_at,
        created_by=name,
        can_edit=can_edit,
        history=[_summary(v, n) for v, n in history],
    )


def _parse(document: ArchitectureRulesDocument | None, text: str | None) -> tuple[RuleSet, str]:
    try:
        if text is not None and document is None:
            return parse_yaml(text), "yaml"
        if document is not None and text is None:
            return from_document(document.model_dump(by_alias=True, exclude_none=True)), "editor"
    except RulesError as exc:
        raise ApiError(
            422,
            "invalid_rules",
            "The architecture rules are not valid",
            {"problems": exc.problems},
        ) from None
    raise ApiError(422, "invalid_rules", "Give the rules either as a document or as YAML")


@router.get(
    "/projects/{project_id}/architecture-rules",
    response_model=ArchitectureRulesResponse,
    responses=_ERRORS,
)
async def get_architecture_rules(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    version: int | None = Query(
        default=None, ge=1, description="A saved version (default: newest)"
    ),
) -> ArchitectureRulesResponse:
    """The project's architecture rules (newest or a given version) and their history."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        return await _response(
            session,
            project,
            version,
            principal.has_role(project.workspace_id, MembershipRole.MEMBER),
        )


@router.put(
    "/projects/{project_id}/architecture-rules",
    response_model=ArchitectureRulesResponse,
    responses=_ERRORS,
)
async def save_architecture_rules(
    project_id: uuid.UUID,
    body: ArchitectureRulesUpdate,
    principal: CurrentPrincipal,
    container: Container,
) -> ArchitectureRulesResponse:
    """Save the rules as a new version (members). Identical rules add no version; a newer version
    than ``base_version`` is a conflict. The next review applies the newest version."""
    rules, source = _parse(body.document, body.yaml)
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
        latest = await _version(session, project.id, None)
        current = latest[0].version if latest else 0
        if body.base_version != current:
            raise ApiError(
                409,
                "version_conflict",
                "The rules changed since you loaded them; reload and try again",
                {"current_version": current},
            )
        document = rules.to_document()
        sha256 = rules.sha256()
        if latest is None or latest[0].sha256 != sha256:
            session.add(
                ArchitectureRuleVersion(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    version=current + 1,
                    document=document,
                    sha256=sha256,
                    source=source,
                    note=(body.note or "").strip() or None,
                    created_by=principal.user_id,
                )
            )
            await session.flush()
        return await _response(session, project, None, can_edit=True)


@router.get(
    "/projects/{project_id}/architecture-rules/export",
    response_class=PlainTextResponse,
    responses={**_ERRORS, 200: {"content": {"application/yaml": {}}}},
)
async def export_architecture_rules(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    version: int | None = Query(default=None, ge=1),
) -> PlainTextResponse:
    """The rules as YAML, ready to keep in the repository or import into another project."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        shown = await _version(session, project.id, version)
        if shown is None:
            raise ApiError(404, "rules_not_found", "No architecture rules have been saved")
        saved = shown[0]
        text = to_yaml(from_document(saved.document))
        number = saved.version
    return PlainTextResponse(
        text,
        media_type="application/yaml",
        headers={
            "Content-Disposition": f'attachment; filename="architecture-rules-v{number}.yaml"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/snapshots/{snapshot_id}/architecture-rules/check",
    response_model=ArchitectureRulesCheckResponse,
    responses=_ERRORS,
)
async def check_architecture_rules(
    snapshot_id: uuid.UUID,
    body: ArchitectureRulesCheckRequest,
    principal: CurrentPrincipal,
    container: Container,
) -> ArchitectureRulesCheckResponse:
    """Evaluate rules (the given ones, or the project's newest) on this upload's dependency map.
    Read-only: nothing is saved and no findings are created."""
    given = body.document is not None or body.yaml is not None
    rules_version: int | None = None
    if given:
        rules, _ = _parse(body.document, body.yaml)
    async with transaction(container.session_factory) as session:
        snapshot = await get_scoped(
            session, principal, Snapshot, snapshot_id, not_found="snapshot_not_found"
        )
        if not given:
            saved = await _version(session, snapshot.project_id, None)
            if saved is None:
                raise ApiError(404, "rules_not_found", "No architecture rules have been saved")
            rules, rules_version = from_document(saved[0].document), saved[0].version
        build = (
            await session.execute(
                select(GraphBuild).where(
                    GraphBuild.snapshot_id == snapshot.id, GraphBuild.is_current.is_(True)
                )
            )
        ).scalar_one_or_none()
        if build is None:
            raise ApiError(404, "graph_not_built", "No dependency map has been built yet")
        if build.state == GraphBuildState.FAILED.value:
            raise ApiError(409, "graph_build_failed", "The latest dependency map failed; rescan")
        model = await architecture.load_model(session, build)
        build_id = build.id
    result = await asyncio.to_thread(
        evaluate, rules, model.files, model.dependencies, today=datetime.now(UTC).date()
    )
    by_rule: dict[str, int] = {}
    for violation in result.violations:
        by_rule[violation.rule_id] = by_rule.get(violation.rule_id, 0) + 1
    parts_by_layer: dict[str, list[str]] = {item.name: [] for item in rules.layers}
    for part, layer in sorted(result.layer_of.items()):
        if layer is not None:
            parts_by_layer[layer].append(part)
    unassigned = result.unassigned
    return ArchitectureRulesCheckResponse(
        build_id=build_id,
        rules_sha256=rules.sha256(),
        rules_version=rules_version,
        layers=[
            ArchitectureLayerParts(name=name, parts=parts[:PART_LIMIT], part_count=len(parts))
            for name, parts in parts_by_layer.items()
        ],
        unassigned=unassigned[:PART_LIMIT],
        unassigned_count=len(unassigned),
        overlaps=dict(list(result.overlaps.items())[:PART_LIMIT]),
        violations=[
            ArchitectureViolationResponse.model_validate(
                {
                    "rule_id": v.rule_id,
                    "title": v.title,
                    "severity": v.severity,
                    "path": v.source_path,
                    "line": v.line,
                    "target": v.target,
                    "source_component": v.source_component,
                    "target_component": v.target_component,
                    "source_layer": v.source_layer,
                    "target_layer": v.target_layer,
                    "message": v.message,
                }
            )
            for v in result.violations[:VIOLATION_LIMIT]
        ],
        violation_count=len(result.violations),
        by_rule=by_rule,
        allowed_by_exception=result.allowed,
        expired_exceptions=[
            f"{a.source} → {a.target} (expired {a.until.isoformat()})"
            for a in result.expired
            if a.until is not None
        ],
        dependencies_checked=result.dependencies_checked,
        notes=result.unmatched,
    )
