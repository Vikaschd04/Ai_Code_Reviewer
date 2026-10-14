"""A project's own AI model and API key (ADR 0026).

A workspace admin chooses a provider (Anthropic or UnoRouter) and a model and adds their own API
key. The key is tested with the provider first (no code is sent) and saved only when the test
passes, encrypted and bound to the project; it is never returned, logged or used for another
project. The project's AI runs then use it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter

from crp_analysis.ai import catalog
from crp_analysis.ai.config import default_connection, project_key_context
from crp_analysis.ai.keytest import KeyCheck, check_anthropic, check_openai_compatible
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.auth.principal import Principal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    AiCatalog,
    AiCatalogModel,
    AiCatalogProvider,
    ProjectAiModelResponse,
    ProjectAiModelUpdate,
)
from crp_api.services.ai_models import MAX_KEY_TESTS_PER_HOUR, project_model, recent_key_tests
from crp_api.services.scope import get_scoped
from crp_core.db.models import AiPolicyEvent, Project, ProjectAiModel, ProjectAiPolicy
from crp_core.db.session import transaction
from crp_core.domain.states import MembershipRole
from crp_core.secret_box import secret_box

router = APIRouter(tags=["ai"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
MIN_KEY_LENGTH, MAX_KEY_LENGTH = 8, 400


async def _models(container: Any, provider_id: str) -> tuple[list[catalog.CatalogModel], bool]:
    if provider_id == "unorouter":
        if not container.settings.ai_catalog_live:
            return list(catalog.UNOROUTER_FALLBACK), False
        return await catalog.live_unorouter(container.ai_transport)
    chosen = catalog.provider(provider_id)
    return (list(chosen.models) if chosen else []), False


@router.get("/ai/catalog", response_model=AiCatalog, responses=_ERRORS)
async def ai_catalog(principal: CurrentPrincipal, container: Container) -> AiCatalog:
    """Providers and models a project can use with its own API key."""
    providers: list[AiCatalogProvider] = []
    for provider_id in catalog.PROVIDER_IDS:
        chosen = catalog.provider(provider_id)
        if chosen is None:
            continue
        models, live = await _models(container, provider_id)
        providers.append(
            AiCatalogProvider(
                id=chosen.id,
                name=chosen.name,
                data_note=chosen.data_note,
                key_hint=chosen.key_hint,
                models=[
                    AiCatalogModel(
                        id=m.id,
                        label=m.label,
                        free=m.free,
                        note=m.note,
                        price_input_per_mtok_usd=m.price_input,
                        price_output_per_mtok_usd=m.price_output,
                    )
                    for m in models
                ],
                live=live,
            )
        )
    return AiCatalog(providers=providers, can_store_keys=secret_box(container.settings) is not None)


def _label(row: ProjectAiModel) -> str:
    found = catalog.find_model(row.provider, row.model)
    chosen = catalog.provider(row.provider)
    name = chosen.name if chosen else row.provider
    return f"{found.label if found else row.model} ({name})"


def _response(
    container: Any,
    project: Project,
    row: ProjectAiModel | None,
    policy: ProjectAiPolicy | None,
    principal: Principal,
) -> ProjectAiModelResponse:
    return ProjectAiModelResponse(
        project_id=project.id,
        configured=row is not None,
        provider=cast(Any, row.provider) if row else None,
        model=row.model if row else None,
        model_label=_label(row) if row else None,
        key_hint=row.key_hint if row else None,
        tested_at=row.tested_at if row else None,
        updated_at=row.updated_at if row else None,
        in_use=bool(row and policy and policy.connection == "project"),
        can_edit=principal.has_role(project.workspace_id, MembershipRole.ADMIN),
        can_store_keys=secret_box(container.settings) is not None,
    )


@router.get(
    "/projects/{project_id}/ai-model", response_model=ProjectAiModelResponse, responses=_ERRORS
)
async def get_project_ai_model(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ProjectAiModelResponse:
    """The project's own AI model (without the key)."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        row = await project_model(session, project.id)
        policy = await session.get(ProjectAiPolicy, project.id)
        return _response(container, project, row, policy, principal)


async def _check(container: Any, body: ProjectAiModelUpdate, key: str) -> KeyCheck:
    settings = container.settings
    if body.provider == "anthropic":
        return await check_anthropic(
            key, body.model, settings.ai_anthropic_base_url, container.ai_transport
        )
    return await check_openai_compatible(
        key, body.model, settings.ai_unorouter_base_url, "UnoRouter", container.ai_transport
    )


@router.put(
    "/projects/{project_id}/ai-model",
    response_model=ProjectAiModelResponse,
    responses={**_ERRORS, 429: {"model": ErrorResponse}},
)
async def save_project_ai_model(
    project_id: uuid.UUID,
    body: ProjectAiModelUpdate,
    principal: CurrentPrincipal,
    container: Container,
) -> ProjectAiModelResponse:
    """Test the API key with the provider, then save the model and the encrypted key and use
    them for the project's AI (workspace admins; AI must be switched on for the project)."""
    box = secret_box(container.settings)
    if box is None:
        raise ApiError(
            409,
            "keys_unavailable",
            "This server cannot store API keys yet. An administrator must set CRP_SECRETS_KEY.",
        )
    key = body.api_key.get_secret_value().strip()
    if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise ApiError(422, "invalid_key", "Paste the whole API key, without spaces.")
    models, _ = await _models(container, body.provider)
    model = next((m for m in models if m.id == body.model), None)
    if model is None:
        raise ApiError(422, "unknown_model", "Choose one of the models offered for this provider.")
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.ADMIN,
        )
        policy = await session.get(ProjectAiPolicy, project.id)
        if policy is None or not policy.enabled:
            raise ApiError(409, "ai_policy_disabled", "Switch on AI review for this project first.")
        if await recent_key_tests(session, project.id) >= MAX_KEY_TESTS_PER_HOUR:
            raise ApiError(
                429, "key_test_limit", "Too many key tests in the last hour; try again later."
            )
        session.add(  # every attempt counts towards the limit and is audited (never the key)
            AiPolicyEvent(
                project_id=project.id,
                actor_user_id=principal.user_id,
                changes={"key_test": [body.provider, body.model]},
            )
        )
    result = await _check(container, body, key)  # outside the transaction: a slow provider
    if not result.ok:
        raise ApiError(422, "key_test_failed", result.message)
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.ADMIN,
            for_update=True,
        )
        policy = await session.get(ProjectAiPolicy, project.id)
        if policy is None or not policy.enabled:
            raise ApiError(409, "ai_policy_disabled", "Switch on AI review for this project first.")
        row = await project_model(session, project.id)
        before = f"{row.provider}/{row.model} …{row.key_hint[-4:]}" if row else None
        now = datetime.now(UTC)
        values = {
            "provider": body.provider,
            "model": body.model,
            "key_ciphertext": box.encrypt(key, project_key_context(project.id, body.provider)),
            "key_hint": key[-4:],
            "tested_at": now,
            "updated_by": principal.user_id,
            "updated_at": now,
        }
        if row is None:
            row = ProjectAiModel(project_id=project.id, workspace_id=project.workspace_id, **values)
            session.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
        connection_before = policy.connection
        policy.connection = "project"
        policy.updated_by = principal.user_id
        session.add(
            AiPolicyEvent(
                project_id=project.id,
                actor_user_id=principal.user_id,
                changes={
                    "ai_model": [before, f"{body.provider}/{body.model} …{key[-4:]}"],
                    "connection": [connection_before, "project"],
                },
            )
        )
        await session.flush()
        await session.refresh(row)
        return _response(container, project, row, policy, principal)


@router.delete(
    "/projects/{project_id}/ai-model", response_model=ProjectAiModelResponse, responses=_ERRORS
)
async def delete_project_ai_model(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ProjectAiModelResponse:
    """Remove the project's own model and key (workspace admins)."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.ADMIN,
            for_update=True,
        )
        row = await project_model(session, project.id)
        policy = await session.get(ProjectAiPolicy, project.id)
        if row is not None:
            await session.delete(row)
            changes: dict[str, object] = {"ai_model": [f"{row.provider}/{row.model}", None]}
            if policy is not None and policy.connection == "project":
                fallback = default_connection(container.settings)
                changes["connection"] = ["project", fallback]
                policy.connection = fallback
                policy.updated_by = principal.user_id
            session.add(
                AiPolicyEvent(
                    project_id=project.id, actor_user_id=principal.user_id, changes=changes
                )
            )
            await session.flush()
        return _response(container, project, None, policy, principal)
