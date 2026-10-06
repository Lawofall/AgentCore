"""Envelope projections stored on an assembly.

The account routes read and write the starred assembly. A conversation route
writes the assembly that conversation points at.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agentcore.api.dependencies import AuthUser, get_conversation_repo, get_user_repo
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.db.repositories import ConversationRepository, UserRepository
from agentcore.runtime.context.envelope_switches import (
    PROJECTIONS,
    normalize_omitted_projections,
)

router = APIRouter(tags=["envelope-switches"])


class EnvelopeSwitchRow(BaseModel):
    id: str
    label: str
    summary: str
    off: bool


class EnvelopeSwitchboardView(BaseModel):
    omitted: list[str]
    switches: list[EnvelopeSwitchRow]


class EnvelopeSwitchUpdate(BaseModel):
    omitted: list[str] = Field(default_factory=list)


def _view(raw: object) -> EnvelopeSwitchboardView:
    omitted = list(normalize_omitted_projections(raw))
    off = set(omitted)
    return EnvelopeSwitchboardView(
        omitted=omitted,
        switches=[
            EnvelopeSwitchRow(
                id=row.id,
                label=row.label,
                summary=row.summary,
                off=row.id in off,
            )
            for row in PROJECTIONS
        ],
    )


async def _starred_omissions(session: object, user_id: str) -> list:
    from agentcore.db.models import LlmModelProfile

    user = await UserRepository(session).get_by_id(user_id)  # type: ignore[arg-type]
    aid = getattr(user, "default_assembly_id", None) if user else None
    if not aid:
        return []
    row = await session.get(LlmModelProfile, aid)  # type: ignore[attr-defined]
    if row is None or row.user_id != user_id:
        return []
    from agentcore.assembly.recipes import surface_from_row

    return list(surface_from_row(row).omitted_projections)


@router.get("/users/me/envelope-switches", response_model=EnvelopeSwitchboardView)
async def get_account_envelope_switches(
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> EnvelopeSwitchboardView:
    return _view(await _starred_omissions(users._session, user.user_id))


@router.put("/users/me/envelope-switches", response_model=EnvelopeSwitchboardView)
async def put_account_envelope_switches(
    body: EnvelopeSwitchUpdate,
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> EnvelopeSwitchboardView:
    from agentcore.llm.model_profiles import LlmModelProfileService

    omitted = list(normalize_omitted_projections(body.omitted))
    aid = await LlmModelProfileService(users._session).snapshot_default_profile_id(
        user.user_id
    )
    if not aid:
        raise ValidationError("还没有可写入的装配")
    await users.set_omitted_projections(user.user_id, omitted)
    return _view(omitted)


@router.get(
    "/conversations/{conversation_id}/envelope-switches",
    response_model=EnvelopeSwitchboardView,
)
async def get_conversation_envelope_switches(
    conversation_id: str,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
) -> EnvelopeSwitchboardView:
    conv = await repo.get_by_id(conversation_id, user_id=user.user_id)
    if conv is None:
        raise NotFoundError("对话不存在")
    from agentcore.db.models import LlmModelProfile

    row = (
        await repo._session.get(LlmModelProfile, conv.assembly_id)
        if conv.assembly_id
        else None
    )
    from agentcore.assembly.recipes import surface_for

    owned = row if row is not None and row.user_id == conv.user_id else None
    surface = surface_for(conv.assembly_id, owned)
    raw = list(surface.omitted_projections) if surface is not None else []
    return _view(raw)


@router.put(
    "/conversations/{conversation_id}/envelope-switches",
    response_model=EnvelopeSwitchboardView,
)
async def put_conversation_envelope_switches(
    conversation_id: str,
    body: EnvelopeSwitchUpdate,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
) -> EnvelopeSwitchboardView:
    """Takes effect the next time a turn entry renders the envelope."""
    omitted = list(normalize_omitted_projections(body.omitted))
    updated = await repo.set_omitted_projections(
        conversation_id, user_id=user.user_id, omitted=omitted
    )
    if updated is None:
        raise NotFoundError("对话不存在")
    return _view(omitted)
