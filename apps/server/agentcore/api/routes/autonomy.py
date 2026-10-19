"""Last-picked boundary — silent account fallback for new conversations."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agentcore.api.dependencies import AuthUser, get_user_repo
from agentcore.core.types import DEFAULT_PERMISSION_AXES, WorkspaceBoundary
from agentcore.db.repositories import UserRepository

router = APIRouter(prefix="/users/me/autonomy", tags=["autonomy"])


class AutonomyView(BaseModel):
    policy: WorkspaceBoundary = DEFAULT_PERMISSION_AXES


class AutonomyUpdate(BaseModel):
    policy: WorkspaceBoundary = Field(
        ...,
        description="New-session default boundary: read | folder | computer | full",
    )


@router.get("", response_model=AutonomyView)
async def get_autonomy(
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> AutonomyView:
    row = await users.get_by_id(user.user_id)
    raw = (row.autonomy_policy if row else None) or DEFAULT_PERMISSION_AXES.value
    try:
        return AutonomyView(policy=WorkspaceBoundary(raw))
    except ValueError:
        return AutonomyView(policy=DEFAULT_PERMISSION_AXES)


@router.put("", response_model=AutonomyView)
async def put_autonomy(
    body: AutonomyUpdate,
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> AutonomyView:
    await users.set_autonomy_policy(user.user_id, body.policy.value)
    return AutonomyView(policy=body.policy)
