"""Cross-domain cleanup for a 注销 (deleted) account.

Shared by the two deletion paths — self-service (``DELETE /v1/auth/me``) and admin
(``DELETE /v1/admin/users/{id}``). The auth-domain mutation (soft-delete + anonymize
the user row, revoke sessions) is the *caller's*, because it differs by initiator:
self proves intent with a password; admin via the role gate + no-self-delete guard.
What is **identical** between the two is reclaiming everything the account owns
*outside* the auth domain — so it lives here once, keeping the two destructive paths
from drifting apart (one place to add a resource when the data model grows).
"""

from agentcore.db.repositories import (
    ConversationRepository,
    ConversationShareRepository,
    LlmModelProfileRepository,
    UserLlmProviderRepository,
)
from agentcore.folders.service import FolderDeskService
from agentcore.storage.assets import AssetStorage
from agentcore.workspace.git_credentials import delete_git_credentials_for_user


async def cleanup_account_resources(
    user_id: str,
    *,
    avatar_key: str | None,
    conversations: ConversationRepository,
    shares: ConversationShareRepository,
    llm_providers: UserLlmProviderRepository,
    assets: AssetStorage,
    folder_desk: FolderDeskService | None = None,
    llm_profiles: LlmModelProfileRepository | None = None,
) -> None:
    """Reclaim everything a 注销 account owns outside the auth domain.

    Soft-deletes the user's conversations (the retention sweeper later reclaims their
    workspaces), revokes every public share link the user created (no shared snapshot
    outlives the account), drops all BYOK providers + model profiles + Git PAT,
    removes the avatar object, and cascades collaboration-desk membership
    (owner folders stay for retention; member → drop membership rows + pending invites).
    ``avatar_key`` must be captured by the caller *before* the user row is anonymized
    (soft-delete nulls it). Each step is independently idempotent, so re-running on
    an already-注销 account is harmless. The append-only cost ledger (不变量①) is
    intentionally untouched.
    """
    await conversations.soft_delete_all_for_user(user_id)
    await shares.revoke_all_for_user(user_id)
    await llm_providers.delete_all_for_user(user_id)
    if llm_profiles is not None:
        await llm_profiles.delete_all_for_user(user_id)
    else:
        # Callers that haven't been updated yet — still reclaim via a fresh session repo
        # only when the shared session is on the conversations repo.
        from agentcore.db.repositories.llm_profiles import LlmModelProfileRepository as _Repo

        await _Repo(conversations._session).delete_all_for_user(user_id)
    await delete_git_credentials_for_user(conversations._session, user_id)
    if avatar_key:
        await assets.delete(avatar_key)
    if folder_desk is not None:
        await folder_desk.cleanup_for_deleted_user(user_id)
    await conversations.delete_preferences_for_user(user_id)
