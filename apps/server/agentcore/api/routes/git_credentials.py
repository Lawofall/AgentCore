"""Account-level Git credentials API (设置 · Git 凭据 · G3).

Cloud private-repo clone/push loads the encrypted PAT server-side. Tools never
accept password parameters. Local mode inherits OS credential helper / ``gh auth``
— this endpoint is the cloud product surface only.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import GitCredentialView, StatusResponse, UpsertGitCredentialRequest
from agentcore.workspace.git_credentials import (
    GitCredentialService,
)
from agentcore.workspace.git_credentials import (
    GitCredentialView as ServiceView,
)

router = APIRouter(prefix="/users/me/git-credentials", tags=["git-credentials"])


def get_git_credential_service(
    session: AsyncSession = Depends(get_db),
) -> GitCredentialService:
    return GitCredentialService(session)


def _to_response(view: ServiceView) -> GitCredentialView:
    return GitCredentialView(
        configured=view.configured,
        masked_token=view.masked_token,
        updated_at=view.updated_at,
    )


@router.get("", response_model=GitCredentialView)
async def get_git_credentials(
    user: AuthUser,
    service: GitCredentialService = Depends(get_git_credential_service),
):
    """Whether an account Git PAT is configured (+ masked tip)."""
    return _to_response(await service.get_view(user.user_id))


@router.put("", response_model=GitCredentialView)
async def upsert_git_credentials(
    body: UpsertGitCredentialRequest,
    user: AuthUser,
    service: GitCredentialService = Depends(get_git_credential_service),
):
    """Create or replace the account Git PAT (encrypted at rest)."""
    return _to_response(await service.upsert(user.user_id, token=body.token))


@router.delete("", response_model=StatusResponse)
async def delete_git_credentials(
    user: AuthUser,
    service: GitCredentialService = Depends(get_git_credential_service),
):
    """Clear the account Git PAT."""
    await service.delete(user.user_id)
    return StatusResponse()
