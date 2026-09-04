"""FolderDeskService — invite lifecycle on folder_id."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentcore.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    QuotaExceededError,
    RateLimitedError,
    ValidationError,
)
from agentcore.core.logging import get_logger
from agentcore.core.rate_limit import FixedWindowRateLimiter
from agentcore.db.models import Folder, FolderMember, User
from agentcore.db.repositories.folder_members import FolderMemberRepository
from agentcore.db.repositories.folders import FolderRepository
from agentcore.db.repositories.users import (
    UserBlockRepository,
    UserDirectoryRepository,
    UserRepository,
)
from agentcore.folders.desk import DeskRole, resolve_desk_access
from agentcore.folders.limits import (
    DEFAULT_INVITE_RATE_MAX,
    DEFAULT_INVITE_RATE_WINDOW_SECONDS,
    DEFAULT_MAX_MEMBERS_PER_FOLDER,
)
from agentcore.messaging.events import ChatEventPublisher, NullChatEventPublisher

logger = get_logger(__name__)

_INVITE_ROLES: frozenset[str] = frozenset({"editor", "viewer"})
FolderMemberState = Literal["accepted", "pending"]


@dataclass(frozen=True)
class FolderDeskView:
    id: str
    name: str
    owner_user_id: str
    my_role: DeskRole
    my_state: FolderMemberState
    rel_path: str | None
    local_root_id: str | None
    local_subpath: str | None
    created_at: object | None = None
    updated_at: object | None = None


@dataclass(frozen=True)
class FolderMemberView:
    user_id: str
    role: DeskRole
    state: FolderMemberState
    invited_by: str | None
    joined_at: object | None
    display_name: str | None = None
    username: str | None = None


class FolderDeskService:
    """Cloud-folder collaboration desk: invites, roles, pending, block, cleanup."""

    def __init__(
        self,
        *,
        folders: FolderRepository,
        members: FolderMemberRepository,
        users: UserRepository,
        blocks: UserBlockRepository,
        directory: UserDirectoryRepository,
        events: ChatEventPublisher | None = None,
        max_members_per_folder: int = DEFAULT_MAX_MEMBERS_PER_FOLDER,
        invite_rate_max: int = DEFAULT_INVITE_RATE_MAX,
        invite_rate_window_seconds: int = DEFAULT_INVITE_RATE_WINDOW_SECONDS,
        invite_limiter: FixedWindowRateLimiter | None = None,
    ) -> None:
        self._folders = folders
        self._members = members
        self._users = users
        self._blocks = blocks
        self._directory = directory
        self._events: ChatEventPublisher = events or NullChatEventPublisher()
        self._max_members = max_members_per_folder
        self._invite_limiter = invite_limiter or FixedWindowRateLimiter(
            max_requests=invite_rate_max,
            window_seconds=invite_rate_window_seconds,
        )

    async def invite(
        self,
        *,
        folder_id: str,
        actor_id: str,
        target_user_id: str,
        role: DeskRole,
    ) -> FolderMemberView:
        if role not in _INVITE_ROLES:
            raise ValidationError("邀请角色只能是 editor 或 viewer")
        if target_user_id == actor_id:
            raise ValidationError("不能邀请自己")
        folder, actor_role = await self._require_accepted_member(folder_id, actor_id)
        if actor_role != "owner":
            raise AuthorizationError("仅所有者可邀请成员")
        if folder.local_root_id:
            raise ValidationError("本机文件夹不能邀请成员")
        if not folder.rel_path:
            raise ValidationError("仅云文件夹可邀请成员")

        if not self._invite_limiter.allow(actor_id):
            raise RateLimitedError("邀请过于频繁，请稍后再试", retry_after=60)

        count = 1 + await self._members.count_members(folder_id)
        if count >= self._max_members:
            raise QuotaExceededError(
                f"成员数已达上限（{self._max_members}）",
                dimension="folder_members",
            )

        target = await self._users.get_by_id(target_user_id)
        if target is None or getattr(target, "status", "active") != "active":
            raise NotFoundError("用户不存在")

        settings = await self._directory.get(target_user_id)
        if settings is not None and not settings.discoverable:
            raise NotFoundError("用户不存在")

        if await self._blocks.is_blocked_between(actor_id, target_user_id):
            raise ValidationError("无法邀请该用户")

        if target_user_id == folder.user_id:
            raise ConflictError("该用户已是成员")

        existing = await self._members.get_member(folder_id, target_user_id)
        if existing is not None:
            if existing.state == "accepted":
                raise ConflictError("该用户已是成员")
            raise ConflictError("邀请已发送，等待对方处理")

        member = await self._members.add_member(
            folder_id=folder_id,
            user_id=target_user_id,
            role=role,
            state="pending",
            invited_by=actor_id,
        )
        await self._events.publish(
            [target_user_id],
            {
                "type": "folder_invite",
                "folder_id": folder_id,
                "folder_name": folder.name,
                "from_user_id": actor_id,
                "role": role,
            },
        )
        await self._fanout(
            folder_id,
            {
                "type": "folder_changed",
                "folder_id": folder_id,
                "action": "member_invited",
                "actor": {"user_id": actor_id, "via": "user"},
                "detail": {"target_user_id": target_user_id, "role": role},
            },
        )
        return await self._member_view(member, target)

    async def accept_invite(self, *, folder_id: str, user_id: str) -> FolderDeskView:
        folder = await self._folders.get_by_id_unscoped(folder_id)
        member = await self._members.get_member(folder_id, user_id)
        if folder is None or member is None or member.state != "pending":
            raise NotFoundError("邀请不存在")
        await self._members.set_member_state(folder_id, user_id, state="accepted")
        await self._fanout(
            folder_id,
            {
                "type": "folder_changed",
                "folder_id": folder_id,
                "action": "member_accepted",
                "actor": {"user_id": user_id, "via": "user"},
            },
        )
        return await self.get_desk(folder_id=folder_id, user_id=user_id)

    async def reject_invite(self, *, folder_id: str, user_id: str) -> None:
        member = await self._members.get_member(folder_id, user_id)
        if member is None or member.state != "pending":
            raise NotFoundError("邀请不存在")
        await self._members.remove_member(folder_id, user_id)
        await self._fanout(
            folder_id,
            {
                "type": "folder_changed",
                "folder_id": folder_id,
                "action": "member_rejected",
                "actor": {"user_id": user_id, "via": "user"},
            },
        )

    async def list_pending_invites(self, *, user_id: str) -> list[FolderDeskView]:
        rows = await self._members.list_pending_for_user(user_id)
        return [self._view(folder, member.role, "pending") for folder, member in rows]

    async def list_shared_with_me(self, *, user_id: str) -> list[FolderDeskView]:
        rows = await self._members.list_for_user(user_id, state="accepted")
        return [self._view(folder, member.role, "accepted") for folder, member in rows]

    async def get_desk(self, *, folder_id: str, user_id: str) -> FolderDeskView:
        folder, role = await self._require_accepted_member(folder_id, user_id)
        return self._view(folder, role, "accepted")

    async def list_members(self, *, folder_id: str, user_id: str) -> list[FolderMemberView]:
        folder, _ = await self._require_accepted_member(folder_id, user_id)
        owner = await self._users.get_by_id(folder.user_id)
        invited = list(await self._members.list_members(folder_id))
        users = await self._users.get_by_ids([m.user_id for m in invited])
        owner_view = FolderMemberView(
            user_id=folder.user_id,
            role="owner",
            state="accepted",
            invited_by=None,
            joined_at=folder.created_at,
            display_name=getattr(owner, "display_name", None) if owner else None,
            username=getattr(owner, "username", None) if owner else None,
        )
        return [owner_view] + [await self._member_view(m, users.get(m.user_id)) for m in invited]

    async def change_role(
        self,
        *,
        folder_id: str,
        actor_id: str,
        target_user_id: str,
        role: DeskRole,
    ) -> FolderMemberView:
        if role not in _INVITE_ROLES:
            raise ValidationError("角色只能改为 editor 或 viewer")
        folder, actor_role = await self._require_accepted_member(folder_id, actor_id)
        if actor_role != "owner":
            raise AuthorizationError("仅所有者可改角色")
        if target_user_id == folder.user_id:
            raise ValidationError("不能变更所有者角色")
        target = await self._members.get_member(folder_id, target_user_id)
        if target is None or target.state != "accepted":
            raise NotFoundError("成员不存在")
        await self._members.set_member_role(folder_id, target_user_id, role=role)
        await self._fanout(
            folder_id,
            {
                "type": "folder_changed",
                "folder_id": folder_id,
                "action": "member_role_changed",
                "actor": {"user_id": actor_id, "via": "user"},
                "detail": {"target_user_id": target_user_id, "role": role},
            },
        )
        user = await self._users.get_by_id(target_user_id)
        refreshed = await self._members.get_member(folder_id, target_user_id)
        assert refreshed is not None
        return await self._member_view(refreshed, user)

    async def remove_member(
        self, *, folder_id: str, actor_id: str, target_user_id: str
    ) -> None:
        folder, actor_role = await self._require_accepted_member(folder_id, actor_id)
        if actor_role != "owner":
            raise AuthorizationError("仅所有者可移除成员")
        if target_user_id == actor_id or target_user_id == folder.user_id:
            raise ValidationError("所有者不能移除自己")
        target = await self._members.get_member(folder_id, target_user_id)
        if target is None:
            raise NotFoundError("成员不存在")
        await self._members.remove_member(folder_id, target_user_id)
        await self._fanout(
            folder_id,
            {
                "type": "folder_changed",
                "folder_id": folder_id,
                "action": "member_removed",
                "actor": {"user_id": actor_id, "via": "user"},
                "detail": {"target_user_id": target_user_id},
            },
        )

    async def leave(self, *, folder_id: str, user_id: str) -> None:
        folder, role = await self._require_accepted_member(folder_id, user_id)
        if role == "owner":
            raise ValidationError("所有者不能退出")
        await self._members.remove_member(folder_id, user_id)
        await self._fanout(
            folder.id,
            {
                "type": "folder_changed",
                "folder_id": folder.id,
                "action": "member_left",
                "actor": {"user_id": user_id, "via": "user"},
            },
        )

    async def on_users_blocked(self, user_a: str, user_b: str) -> int:
        n = await self._members.delete_pending_between(user_a, user_b)
        if n:
            logger.info(
                "folder_desk.pending_cleared_on_block",
                user_a=user_a,
                user_b=user_b,
                count=n,
            )
        return n

    async def cleanup_for_deleted_user(self, user_id: str) -> None:
        owned = await self._folders.list_owned_ids(user_id, include_deleted=True)
        dropped = await self._members.delete_memberships_for_folders(owned)
        await self._members.delete_all_memberships_for_user(user_id)
        logger.info(
            "folder_desk.cleanup_account",
            user=user_id,
            owned_memberships_cleared=dropped,
        )

    async def _require_accepted_member(
        self, folder_id: str, user_id: str
    ) -> tuple[Folder, DeskRole]:
        access = await resolve_desk_access(
            self._folders._session, folder_id=folder_id, user_id=user_id
        )
        if access is None:
            raise NotFoundError("文件夹不存在")
        return access.folder, access.role

    def _view(
        self, folder: Folder, role: str, state: FolderMemberState
    ) -> FolderDeskView:
        desk_role: DeskRole = "viewer"
        if role == "owner":
            desk_role = "owner"
        elif role == "editor":
            desk_role = "editor"
        elif role == "viewer":
            desk_role = "viewer"
        return FolderDeskView(
            id=folder.id,
            name=folder.name,
            owner_user_id=folder.user_id,
            my_role=desk_role,
            my_state=state,
            rel_path=folder.rel_path,
            local_root_id=folder.local_root_id,
            local_subpath=folder.local_subpath,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )

    async def _member_view(
        self, member: FolderMember, user: User | None
    ) -> FolderMemberView:
        role: DeskRole = member.role  # type: ignore[assignment]
        state: FolderMemberState = member.state  # type: ignore[assignment]
        return FolderMemberView(
            user_id=member.user_id,
            role=role,
            state=state,
            invited_by=member.invited_by,
            joined_at=member.joined_at,
            display_name=getattr(user, "display_name", None) if user else None,
            username=getattr(user, "username", None) if user else None,
        )

    async def _fanout(self, folder_id: str, event: dict) -> None:
        folder = await self._folders.get_by_id_unscoped(folder_id)
        recipient_ids: list[str] = []
        if folder is not None:
            recipient_ids.append(folder.user_id)
        for m in await self._members.list_members(folder_id):
            if m.state == "accepted" and m.user_id not in recipient_ids:
                recipient_ids.append(m.user_id)
        if recipient_ids:
            await self._events.publish(recipient_ids, event)
