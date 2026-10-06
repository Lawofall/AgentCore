"""Tool switches: account default, and the list stored on one conversation."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agentcore.api.dependencies import AuthUser, get_conversation_repo, get_user_repo
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import ConversationRepository, UserRepository
from agentcore.tools.switchboard import (
    SWITCHES,
    normalize_disabled_tools,
    switch_member_names,
)

router = APIRouter(tags=["tool-switches"])


class ToolSwitchRow(BaseModel):
    id: str
    label: str
    summary: str
    doc_tool: str
    off: bool
    note: str | None = None
    tools: list[str] = Field(
        default_factory=list,
        description=(
            "Model-facing names this switch removes, sorted. "
            "Companions that leave with it are included."
        ),
    )


class ToolSwitchboardView(BaseModel):
    disabled: list[str]
    switches: list[ToolSwitchRow]


class ToolSwitchUpdate(BaseModel):
    disabled: list[str] = Field(default_factory=list)


def _view(disabled: object) -> ToolSwitchboardView:
    ids = set(normalize_disabled_tools(disabled))
    return ToolSwitchboardView(
        disabled=list(normalize_disabled_tools(disabled)),
        switches=[
            ToolSwitchRow(
                id=switch.id,
                label=switch.label,
                summary=switch.summary,
                doc_tool=switch.doc_tool,
                off=switch.id in ids,
                note=switch.note,
                tools=list(switch_member_names(switch.id)),
            )
            for switch in SWITCHES
        ],
    )


@router.get("/users/me/tool-switches", response_model=ToolSwitchboardView)
async def get_account_tool_switches(
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> ToolSwitchboardView:
    from agentcore.tools.switchboard import disabled_tools_for_user

    raw = await disabled_tools_for_user(users._session, user.user_id)
    return _view(raw)


@router.put("/users/me/tool-switches", response_model=ToolSwitchboardView)
async def put_account_tool_switches(
    body: ToolSwitchUpdate,
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> ToolSwitchboardView:
    from agentcore.core.errors import ValidationError
    from agentcore.llm.model_profiles import LlmModelProfileService

    disabled = list(normalize_disabled_tools(body.disabled))
    aid = await LlmModelProfileService(users._session).snapshot_default_profile_id(
        user.user_id
    )
    if not aid:
        raise ValidationError("还没有可写入的装配")
    await users.set_disabled_tools(user.user_id, disabled)
    return _view(disabled)


@router.get(
    "/conversations/{conversation_id}/tool-switches",
    response_model=ToolSwitchboardView,
)
async def get_conversation_tool_switches(
    conversation_id: str,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
) -> ToolSwitchboardView:
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
    raw = list(surface.disabled_tools) if surface is not None else []
    return _view(raw)


@router.put(
    "/conversations/{conversation_id}/tool-switches",
    response_model=ToolSwitchboardView,
)
async def put_conversation_tool_switches(
    conversation_id: str,
    body: ToolSwitchUpdate,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
) -> ToolSwitchboardView:
    """Takes effect the next time a turn entry builds the tool table."""
    disabled = list(normalize_disabled_tools(body.disabled))
    updated = await repo.set_disabled_tools(
        conversation_id, user_id=user.user_id, disabled=disabled
    )
    if updated is None:
        raise NotFoundError("对话不存在")
    return _view(disabled)
