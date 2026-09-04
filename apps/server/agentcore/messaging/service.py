"""Messaging service: the 消息 page (找人 IM) business logic (消息IM.md).

Holds all IM policy so the HTTP layer stays thin and the repos do pure data
access:
- 任意搜人 visibility: exact-match search, minus self, blocked pairs, and users
  who opted out of discovery (``user_directory_settings.discoverable``);
- friend graph + request lifecycle (消息IM.md §九);
- who-can-DM gate: friends open freely; non-friends need peer ``who_can_dm=anyone``
  (message request / pending), while ``friends`` refuses strangers (403);
- send-message guards: must be a chat member (else 404, IDOR-safe), dm blocked
  pairs are refused, and a reply by the requested party clears their pending gate;
- list / unread / read-cursor / block / directory-settings management.

The service depends on repository instances (unit-testable with in-memory fakes)
and an optional realtime publisher (a seam — see ``events.py``) it calls to fan a
new message out to every member's live connections.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from agentcore.core.errors import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.logging import get_logger
from agentcore.db.models import Chat, ChatMember, ChatMessage, FriendRequest, User
from agentcore.db.repositories import (
    ChatRepository,
    FriendRepository,
    UserBlockRepository,
    UserDirectoryRepository,
    UserRepository,
)
from agentcore.messaging.events import ChatEventPublisher, NullChatEventPublisher
from agentcore.messaging.thumbnails import make_image_thumbnail
from agentcore.workspace.locate import build_chat_workspace
from agentcore.workspace.protocol import (
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
    WorkspaceIOError,
)

logger = get_logger(__name__)

_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 50
# Reply-quote body preview: keep short for bubble quote bars.
_REPLY_PREVIEW_MAX = 100
# Self-recall window (消息IM.md §8.1); platform admin group governance bypasses.
_RECALL_WINDOW = timedelta(minutes=2)
_RECALL_LIST_PREVIEW = "[已撤回]"
# Self-edit window (消息IM.md §8.1); attachments / non-text refused.
_EDIT_WINDOW = timedelta(minutes=15)
_OFFICIAL_DISPLAY_NAME = "官方号"
_ATTACHMENT_PREVIEW_LABELS = {
    "image": "[图片]",
    "file": "[文件]",
    "system_card": "[系统消息]",
}
# WeChat-style notice posted when a friend request is accepted (NULL-sender system_card).
_FRIEND_ACCEPTED_SYSTEM_TEXT = "我通过了你的朋友验证请求，现在我们可以开始聊天了"

FriendRelation = Literal[
    "self",
    "none",
    "outgoing_request",
    "incoming_request",
    "friends",
    "blocked",
]
FriendRequestAction = Literal["created", "accepted", "rejected", "cancelled"]
ChatChangedReason = Literal["created", "member_added", "activated"]


@dataclass(frozen=True)
class ChatView:
    """A chat plus the viewer's per-chat state and resolved dm peer — the domain
    shape the route maps to ``ChatSummary`` (schema conversion stays in the route).
    """

    chat: Chat
    member: ChatMember
    peer: User | None
    unread: int


@dataclass(frozen=True)
class DirectoryView:
    """A user's resolved discoverability + who-can-DM / who-can-friend (defaults)."""

    discoverable: bool
    who_can_dm: str
    who_can_friend: str


@dataclass(frozen=True)
class ProfileView:
    """资料卡 domain shape for ``GET /users/{id}/profile``."""

    user: User
    relation: FriendRelation
    request_id: str | None
    online: bool = False


@dataclass(frozen=True)
class FriendRequestBox:
    """Pending friend-request inbox (incoming + outgoing)."""

    incoming: Sequence[FriendRequest]
    outgoing: Sequence[FriendRequest]


@dataclass(frozen=True)
class MessagePage:
    """A page of chat messages with paging echoed back for the list response."""

    messages: Sequence[ChatMessage]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AttachmentUpload:
    """The result of storing an attachment: bytes written + the optional thumbnail.

    ``thumb_path`` is a workspace-relative path to a generated WebP preview for
    images (None for non-images / small images / a failed thumbnail), referenced
    by the message's ``StoredAttachment`` so the bubble can inline it cheaply.
    """

    size_bytes: int
    thumb_path: str | None


@dataclass(frozen=True)
class MemberView:
    """A group member for the roster: their user plus moderation-relevant flags.

    ``is_admin`` = platform ``users.role == admin`` (创始团队). ``group_role`` =
    ``chat_members.role`` (内测群管理员 = ``admin``). Clients badge both and gate
    kick/mute on either platform admin or group moderator.
    """

    user: User
    is_admin: bool
    group_role: str
    muted_by_admin: bool


class MessagingService:
    def __init__(
        self,
        *,
        users: UserRepository,
        chats: ChatRepository,
        blocks: UserBlockRepository,
        directory: UserDirectoryRepository,
        friends: FriendRepository | None = None,
        events: ChatEventPublisher | None = None,
        folder_members: Any | None = None,
    ) -> None:
        self._users = users
        self._chats = chats
        self._blocks = blocks
        self._directory = directory
        # Optional so older test fixtures without friends still construct; production
        # always injects FriendRepository via get_messaging_service.
        self._friends = friends
        self._events: ChatEventPublisher = events or NullChatEventPublisher()
        self._folder_members = folder_members

    def _require_friends(self) -> FriendRepository:
        if self._friends is None:
            raise RuntimeError("FriendRepository is required for friend operations")
        return self._friends

    # --- People search (任意搜人 + 护栏) ---

    async def search_users(self, *, requester_id: str, query: str, limit: int = 20) -> list[User]:
        """Exact-match people-search, filtered by visibility rules.

        Drops the requester themselves, any user in a block relationship with
        them (either direction), and anyone who turned discovery off. A missing
        directory row means discoverable (open search is the product default).
        """
        candidates = await self._users.search(query, limit=limit)
        visible: list[User] = []
        for user in candidates:
            if user.user_id == requester_id:
                continue
            if await self._blocks.is_blocked_between(requester_id, user.user_id):
                continue
            settings = await self._directory.get(user.user_id)
            if settings is not None and not settings.discoverable:
                continue
            visible.append(user)
        return visible

    # --- Chats ---

    async def start_dm(self, *, requester_id: str, peer_id: str) -> ChatView:
        """Open (or reuse) a 1:1 chat with another user.

        Reuses the existing dm if there is one (idempotent open). For a brand-new
        dm: refuses self-dm, unknown/disabled peers, blocked pairs. Friends open
        with both sides accepted; non-friends need peer ``who_can_dm=anyone``
        (peer starts pending) — ``friends`` refuses with 403.
        """
        if peer_id == requester_id:
            raise ValidationError("不能与自己发起会话")
        peer = await self._users.get_by_id(peer_id)
        if peer is None or peer.status != "active":
            raise NotFoundError("用户不存在")
        if await self._blocks.is_blocked_between(requester_id, peer_id):
            raise AuthorizationError("无法向该用户发送消息")

        are_friends = False
        if self._friends is not None:
            are_friends = await self._friends.are_friends(requester_id, peer_id)

        existing = await self._chats.get_dm(requester_id, peer_id)
        if existing is not None:
            if are_friends:
                await self._activate_pending_dm(existing.id, requester_id, peer_id)
            member = await self._chats.get_member(existing.id, requester_id)
            assert member is not None  # creator is always a member of their dm
            return ChatView(chat=existing, member=member, peer=peer, unread=0)

        if not are_friends:
            settings = await self._directory.get(peer_id)
            who = settings.who_can_dm if settings is not None else "anyone"
            if who == "friends":
                raise AuthorizationError("对方仅允许好友发起会话")

        peer_state = "accepted" if are_friends else "pending"
        chat = await self._chats.create_dm(
            creator_id=requester_id, peer_id=peer_id, peer_state=peer_state
        )
        # Peer learns of the new chat via thin firehose nudge (then pulls ChatView).
        await self._publish_chat_changed(chat.id, reason="created", user_ids=[peer_id])
        member = await self._chats.get_member(chat.id, requester_id)
        assert member is not None
        logger.debug("dm.opened", chat=chat.id, by=requester_id, peer=peer_id)
        return ChatView(chat=chat, member=member, peer=peer, unread=0)

    async def _activate_pending_dm(self, chat_id: str, *user_ids: str) -> bool:
        """Clear pending message-request gates; nudge both sides when any flipped.

        Returns whether at least one member transitioned ``pending → accepted``.
        """
        activated = False
        for uid in user_ids:
            member = await self._chats.get_member(chat_id, uid)
            if member is not None and member.state == "pending":
                await self._chats.accept_request(chat_id, uid)
                activated = True
        if activated:
            await self._publish_chat_changed(
                chat_id, reason="activated", user_ids=list(user_ids)
            )
        return activated

    async def join_auto_join_chats(self, *, user_id: str) -> None:
        """Enroll a user into every auto-join chat (内测群 + official broadcast).

        Called once at registration. Idempotent per chat — a user already in a
        chat is left untouched (so re-running never resets their state). Pinned on
        join so the chat surfaces at the top of a brand-new user's list.
        """
        chats = await self._chats.list_auto_join_chats()
        joined: list[str] = []
        for chat in chats:
            if await self._chats.get_member(chat.id, user_id) is not None:
                continue
            await self._chats.add_member(chat.id, user_id, pinned=True)
            await self._publish_chat_changed(
                chat.id, reason="member_added", user_ids=[user_id]
            )
            joined.append(chat.id)
        if joined:
            logger.info("chat.auto_join", user=user_id, chats=joined)

    async def ensure_official_membership(self, *, user_id: str) -> None:
        """Idempotent enrollment into the official broadcast chat.

        Leave is forbidden on the official chat, so login / list-chats may call
        this as a兜底 for accounts that missed registration auto-join (e.g.
        created before the official chat migration). Does **not** re-enroll
        leavers of the 内测群 (that chat remains registration-only). No-ops when
        the official chat row is absent (pre-migration).
        """
        chat = await self._chats.get_official_chat()
        if chat is None:
            return
        if await self._chats.get_member(chat.id, user_id) is not None:
            return
        await self._chats.add_member(chat.id, user_id, pinned=True)
        await self._publish_chat_changed(
            chat.id, reason="member_added", user_ids=[user_id]
        )
        logger.info("chat.auto_join", user=user_id, chats=[chat.id])

    async def list_members(self, *, chat_id: str, user_id: str) -> list[MemberView]:
        """The members of a chat (for the group roster + member panel).

        Non-members get 404 (IDOR-safe). Returns members in join order, each with
        platform-admin, group-role, and admin-mute flags so the route can build a
        ``ChatParticipant``. Members whose account no longer resolves are dropped.
        """
        if await self._chats.get_member(chat_id, user_id) is None:
            raise NotFoundError("会话不存在")
        members = sorted(await self._chats.list_members(chat_id), key=lambda m: m.joined_at)
        users = await self._users.get_by_ids([m.user_id for m in members])
        views: list[MemberView] = []
        for m in members:
            user = users.get(m.user_id)
            if user is None:
                continue
            role = getattr(m, "role", None) or "member"
            if role not in ("owner", "admin", "member"):
                role = "member"
            views.append(
                MemberView(
                    user=user,
                    is_admin=user.role == "admin",
                    group_role=role,
                    muted_by_admin=m.muted_by_admin,
                )
            )
        return views

    async def leave_chat(self, *, chat_id: str, user_id: str) -> None:
        """Leave a group chat (removes this user's membership).

        Non-members 404. Dms can't be "left" (they're a pair, not a room). The
        official broadcast chat also refuses leave (422) — membership is
        mandatory and list/login兜底 would re-enroll anyway. Auto-join for the
        内测群 fires only at registration, so leaving that group sticks.
        """
        member = await self._chats.get_member(chat_id, user_id)
        if member is None:
            raise NotFoundError("会话不存在")
        chat = await self._chats.get_chat(chat_id)
        if chat is not None and chat.type == "dm":
            raise ValidationError("单聊不支持退出")
        if chat is not None and chat.type == "official":
            raise ValidationError("官方号不支持退出")
        await self._chats.remove_member(chat_id, user_id)
        logger.info("chat.left", chat=chat_id, user=user_id)

    async def set_chat_flags(
        self,
        *,
        chat_id: str,
        user_id: str,
        muted: bool | None = None,
        pinned: bool | None = None,
    ) -> ChatView:
        """Update this user's per-chat flags (mute / pin) and return the row."""
        if await self._chats.get_member(chat_id, user_id) is None:
            raise NotFoundError("会话不存在")
        await self._chats.set_membership_flags(chat_id, user_id, muted=muted, pinned=pinned)
        return await self.chat_view(chat_id=chat_id, user_id=user_id)

    # --- Moderation (审核治理: 平台 admin 或群管理员) ---
    # Actor authority is enforced here (routes use AuthUser). Platform admin is
    # always a moderator; group-level ``chat_members.role in {owner,admin}`` also
    # qualifies. Appointment of 内测群管理员 is admin-console-only.

    async def kick_member(self, *, chat_id: str, actor_id: str, target_id: str) -> None:
        """Remove a member from a group (踢人) and post a system notice.

        404 unknown chat / target-not-a-member; 422 for a dm (a pair, not a room);
        403 when the actor is not a moderator, or the target is a platform admin /
        (for group mods) another group moderator. The kicked user is dropped, then
        a centered ``system_card`` is fanned out to the remaining members.
        """
        await self._require_moderatable_group(chat_id)
        await self._require_moderator(chat_id, actor_id)
        await self._assert_target_moderatable(chat_id, actor_id=actor_id, target_id=target_id)
        target = await self._users.get_by_id(target_id)
        await self._chats.remove_member(chat_id, target_id)
        name = target.display_name if target else "成员"
        await self._post_system_card(
            chat_id=chat_id,
            content=f"{name} 已被移出群聊",
            payload={"kind": "member_removed", "user_id": target_id},
        )
        logger.info("chat.kicked", chat=chat_id, by=actor_id, target=target_id)

    async def set_admin_mute(
        self, *, chat_id: str, actor_id: str, target_id: str, muted: bool
    ) -> None:
        """Mute / unmute a member (禁言): a muted member keeps reading but a
        send is refused (403, in :meth:`send_message`).

        Same gates as :meth:`kick_member`. No全群 broadcast — 禁言 is targeted, not
        announced (Stage 3 decision); the member learns of it when a send is
        refused, and the roster shows the state to moderators.
        """
        await self._require_moderatable_group(chat_id)
        await self._require_moderator(chat_id, actor_id)
        await self._assert_target_moderatable(chat_id, actor_id=actor_id, target_id=target_id)
        await self._chats.set_admin_mute(chat_id, target_id, muted_by_admin=muted)
        logger.info(
            "chat.admin_mute",
            chat=chat_id,
            by=actor_id,
            target=target_id,
            muted=muted,
        )

    async def post_announcement(self, *, chat_id: str, actor_id: str, content: str) -> ChatMessage:
        """Post a group announcement as a centered ``system_card``.

        Sent as the official/system account (NULL sender) so it renders as a
        notice rather than a normal bubble, and fanned out to every member. 404
        unknown chat; 422 for a dm; 403 if actor is not a moderator.
        """
        await self._require_moderatable_group(chat_id)
        await self._require_moderator(chat_id, actor_id)
        message = await self._post_system_card(
            chat_id=chat_id,
            content=content,
            payload={"kind": "announcement", "by": actor_id},
        )
        logger.info("chat.announced", chat=chat_id, by=actor_id)
        return message

    async def list_beta_group_moderators(self) -> tuple[str, str, list[User]]:
        """内测群 members with ``chat_members.role=admin`` (appointment roster).

        Returns ``(chat_id, title, users)``. Missing seed chat → 404.
        """
        from agentcore.db.repositories.chat import BETA_GROUP_ID, BETA_GROUP_TITLE

        chat = await self._chats.get_chat(BETA_GROUP_ID)
        if chat is None:
            raise NotFoundError("内测群不存在")
        members = await self._chats.list_members(BETA_GROUP_ID)
        mod_ids = [m.user_id for m in members if (getattr(m, "role", None) or "member") == "admin"]
        users_by_id = await self._users.get_by_ids(mod_ids)
        users = [users_by_id[uid] for uid in mod_ids if uid in users_by_id]
        title = chat.title or BETA_GROUP_TITLE
        return BETA_GROUP_ID, title, users

    async def set_beta_group_moderator(self, *, user_id: str, actor_id: str) -> User:
        """Appoint a 内测群管理员 (``chat_members.role=admin``). Admin-console only.

        Ensures membership (re-adds leavers). Idempotent if already admin.
        """
        from agentcore.db.repositories.chat import BETA_GROUP_ID

        chat = await self._chats.get_chat(BETA_GROUP_ID)
        if chat is None:
            raise NotFoundError("内测群不存在")
        user = await self._users.get_by_id(user_id)
        if user is None or getattr(user, "status", None) != "active":
            raise NotFoundError("用户不存在")
        member = await self._chats.get_member(BETA_GROUP_ID, user_id)
        if member is None:
            await self._chats.add_member(BETA_GROUP_ID, user_id, role="admin")
            await self._publish_chat_changed(
                BETA_GROUP_ID, reason="member_added", user_ids=[user_id]
            )
        elif (getattr(member, "role", None) or "member") != "admin":
            await self._chats.set_member_role(BETA_GROUP_ID, user_id, role="admin")
        logger.info("chat.beta_moderator_set", chat=BETA_GROUP_ID, by=actor_id, target=user_id)
        return user

    async def clear_beta_group_moderator(self, *, user_id: str, actor_id: str) -> None:
        """Revoke 内测群管理员 (role → ``member``). Leaves membership intact."""
        from agentcore.db.repositories.chat import BETA_GROUP_ID

        chat = await self._chats.get_chat(BETA_GROUP_ID)
        if chat is None:
            raise NotFoundError("内测群不存在")
        member = await self._chats.get_member(BETA_GROUP_ID, user_id)
        if member is None:
            raise NotFoundError("该用户不在内测群")
        if (getattr(member, "role", None) or "member") == "admin":
            await self._chats.set_member_role(BETA_GROUP_ID, user_id, role="member")
        logger.info(
            "chat.beta_moderator_cleared", chat=BETA_GROUP_ID, by=actor_id, target=user_id
        )

    async def publish_product_notice(
        self,
        *,
        notice_id: str,
        title: str,
        body: str,
        severity: str,
        surface: str,
        card_template: str = "service",
        summary: str | None = None,
        cover_url: str | None = None,
        cta_label: str | None = None,
        cta_url: str | None = None,
    ) -> ChatMessage | None:
        """Mirror a published product Notice into the official broadcast chat.

        Only ``surface ∈ {inbox, both, modal}`` writes an IM message (banner-only
        stays on the Notice surfaces). One shared ``system_card`` for all members —
        never per-user copies. Returns ``None`` when the surface skips IM.

        ``content`` stays ``title\\nbody`` for old clients; dual-template fields live
        in ``payload`` (``card_template`` always; ``summary`` / ``cover_url`` when set).
        """
        if surface not in ("inbox", "both", "modal"):
            return None
        chat = await self._chats.get_or_create_official_chat()
        payload: dict[str, Any] = {
            "kind": "product_notice",
            "notice_id": notice_id,
            "severity": severity,
            "card_template": card_template or "service",
        }
        if summary:
            payload["summary"] = summary
        if cover_url:
            payload["cover_url"] = cover_url
        if cta_label:
            payload["cta_label"] = cta_label
        if cta_url:
            payload["cta_url"] = cta_url
        content = f"{title}\n{body}".strip() if body else title
        message = await self._post_system_card(
            chat_id=chat.id,
            content=content,
            payload=payload,
        )
        logger.info(
            "chat.product_notice_published",
            chat=chat.id,
            notice_id=notice_id,
            surface=surface,
        )
        return message

    async def _require_moderatable_group(self, chat_id: str) -> Chat:
        """Resolve a chat that supports moderation (group/official, not a dm)."""
        chat = await self._chats.get_chat(chat_id)
        if chat is None:
            raise NotFoundError("会话不存在")
        if chat.type == "dm":
            raise ValidationError("单聊不支持该操作")
        return chat

    async def _actor_is_moderator(self, chat_id: str, actor_id: str) -> bool:
        """Platform admin, or group membership role in ``{owner, admin}``."""
        actor = await self._users.get_by_id(actor_id)
        if actor is not None and getattr(actor, "role", None) == "admin":
            return True
        member = await self._chats.get_member(chat_id, actor_id)
        if member is None:
            return False
        return (getattr(member, "role", None) or "member") in ("owner", "admin")

    async def _require_moderator(self, chat_id: str, actor_id: str) -> None:
        if not await self._actor_is_moderator(chat_id, actor_id):
            raise AuthorizationError("无权执行该操作")

    async def _assert_target_moderatable(
        self, chat_id: str, *, actor_id: str, target_id: str
    ) -> None:
        """Guard a kick/mute target: member, not self, not platform admin;
        group mods cannot act on other group mods (platform admin may).
        """
        if target_id == actor_id:
            raise ValidationError("不能对自己执行该操作")
        member = await self._chats.get_member(chat_id, target_id)
        if member is None:
            raise NotFoundError("该用户不在群内")
        target = await self._users.get_by_id(target_id)
        if target is not None and target.role == "admin":
            raise AuthorizationError("不能对管理员执行该操作")
        target_group_role = getattr(member, "role", None) or "member"
        if target_group_role in ("owner", "admin"):
            actor = await self._users.get_by_id(actor_id)
            if actor is None or getattr(actor, "role", None) != "admin":
                raise AuthorizationError("不能对群管理员执行该操作")

    async def _post_system_card(
        self, *, chat_id: str, content: str, payload: dict[str, Any] | None = None
    ) -> ChatMessage:
        """Append a ``system_card`` (NULL sender = official) and fan it out to the
        chat's current members. Shared by kick notices and announcements.
        """
        message = await self._chats.add_message(
            chat_id=chat_id,
            sender_user_id=None,
            content=content,
            sender_type="official",
            content_type="system_card",
            payload=payload,
        )
        members = await self._chats.list_members(chat_id)
        await self._events.publish([m.user_id for m in members], self._message_event(message))
        return message

    async def chat_view(self, *, chat_id: str, user_id: str) -> ChatView:
        """Build one chat's view (chat + this user's state + dm peer + unread).

        Single-chat counterpart to :meth:`list_chats` for endpoints that return
        one updated row (e.g. a flags patch). Non-members 404.
        """
        chat = await self._chats.get_chat(chat_id)
        member = await self._chats.get_member(chat_id, user_id)
        if chat is None or member is None:
            raise NotFoundError("会话不存在")
        peer: User | None = None
        if chat.type == "dm":
            peer_ids = await self._chats.peer_ids_for([chat_id], exclude_user_id=user_id)
            peer_id = peer_ids.get(chat_id)
            if peer_id:
                peer = (await self._users.get_by_ids([peer_id])).get(peer_id)
        unread = (await self._chats.unread_counts(user_id)).get(chat_id, 0)
        return ChatView(chat=chat, member=member, peer=peer, unread=unread)

    async def list_chats(self, *, user_id: str) -> list[ChatView]:
        """The user's chat list (pinned first, then recent), with unread counts and
        dm peers resolved in batch (no N+1).

        Ensures official-chat membership before listing (兜底 for accounts that
        missed registration auto-join after the official chat was introduced).
        """
        await self.ensure_official_membership(user_id=user_id)
        memberships = await self._chats.list_memberships(user_id)
        # Resolve "the other human" only for dms — a group/official chat has no
        # single peer (the client renders its title), and picking an arbitrary
        # member would leak that member as the row's identity.
        dm_ids = [chat.id for chat, _ in memberships if chat.type == "dm"]
        unread = await self._chats.unread_counts(user_id)
        peer_ids = await self._chats.peer_ids_for(dm_ids, exclude_user_id=user_id)
        peers = await self._users.get_by_ids(list(peer_ids.values()))

        views: list[ChatView] = []
        for chat, member in memberships:
            peer_id = peer_ids.get(chat.id)
            peer = peers.get(peer_id) if peer_id else None
            views.append(
                ChatView(
                    chat=chat,
                    member=member,
                    peer=peer,
                    unread=unread.get(chat.id, 0),
                )
            )
        return views

    # --- Messages ---

    async def send_message(
        self,
        *,
        chat_id: str,
        sender_id: str,
        content: str | None,
        content_type: str = "text",
        attachments: list | None = None,
        reply_to_message_id: str | None = None,
        mentions: list[dict[str, Any]] | None = None,
        client_msg_id: str | None = None,
    ) -> ChatMessage:
        """Send a message into a chat the user belongs to.

        Non-members get 404 (IDOR-safe — no existence leak). Users cannot send
        into the official broadcast chat (422 — read-only fan-in). In a dm, a
        block in either direction refuses the send. A reply by the party who was
        holding a pending message-request accepts it. The stored message is
        fanned out to every member's live connections (sender included, for
        multi-device). ``content`` may be empty for a 富消息 carrying only
        ``attachments``. Structured ``mentions`` are validated and frozen onto
        the row (accepted members only; ``@所有人`` = group + platform admin).
        """
        member = await self._chats.get_member(chat_id, sender_id)
        if member is None:
            raise NotFoundError("会话不存在")
        chat = await self._chats.get_chat(chat_id)
        if chat is None:
            raise NotFoundError("会话不存在")
        if chat.type == "official":
            raise ValidationError("官方号不支持发送消息")
        if member.muted_by_admin:
            raise AuthorizationError("你已被管理员禁言，暂时无法发言")

        if chat.type == "dm":
            peer_ids = await self._chats.peer_ids_for([chat_id], exclude_user_id=sender_id)
            peer_id = peer_ids.get(chat_id)
            if peer_id and await self._blocks.is_blocked_between(sender_id, peer_id):
                raise AuthorizationError("无法向该用户发送消息")

        reply_id, reply_snapshot = await self._resolve_reply_to(
            chat_id=chat_id, reply_to_message_id=reply_to_message_id
        )
        frozen_mentions = await self._resolve_mentions(
            chat=chat, sender_id=sender_id, mentions=mentions
        )

        message = await self._chats.add_message(
            chat_id=chat_id,
            sender_user_id=sender_id,
            content=content,
            content_type=content_type,
            attachments=attachments,
            reply_to_message_id=reply_id,
            reply_to=reply_snapshot,
            mentions=frozen_mentions,
            client_msg_id=client_msg_id,
        )

        if member.state == "pending":
            await self._chats.accept_request(chat_id, sender_id)

        members = await self._chats.list_members(chat_id)
        await self._events.publish([m.user_id for m in members], self._message_event(message))
        return message

    async def recall_message(
        self, *, chat_id: str, message_id: str, actor_id: str
    ) -> ChatMessage:
        """Soft-recall a message (消息IM.md §8 S3).

        Keeps the row (cursors / reply snapshots stay valid), clears body so list
        previews never re-expose withdrawn text, and fans ``chat_message_updated``
        (not ``chat_message``) so clients replace in place without bumping unread.

        Permissions:
        - sender within 2 minutes;
        - platform admin may recall any member message in a **group** (no window);
        - ``system_card`` / official-channel messages: platform admin only.
        Non-members get 404. Already-recalled is idempotent.
        """
        member = await self._chats.get_member(chat_id, actor_id)
        if member is None:
            raise NotFoundError("会话不存在")
        chat = await self._chats.get_chat(chat_id)
        if chat is None:
            raise NotFoundError("会话不存在")
        message = await self._chats.get_message(message_id)
        if message is None or message.chat_id != chat_id:
            raise NotFoundError("消息不存在")
        if message.recalled_at is not None:
            return message

        actor = await self._users.get_by_id(actor_id)
        is_platform_admin = actor is not None and getattr(actor, "role", None) == "admin"
        is_group_mod = False
        if not is_platform_admin and chat.type == "group":
            is_group_mod = await self._actor_is_moderator(chat_id, actor_id)
        can_govern = is_platform_admin or is_group_mod
        is_protected = (
            message.content_type == "system_card"
            or message.sender_type == "official"
            or chat.type == "official"
        )

        if is_protected:
            if not is_platform_admin:
                raise AuthorizationError("无权撤回该消息")
        elif can_govern and chat.type == "group":
            pass  # group governance — any member message, no window
        elif message.sender_user_id == actor_id:
            created = message.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            if created is None or (now - created) > _RECALL_WINDOW:
                raise AuthorizationError("已超过撤回时限")
        else:
            raise AuthorizationError("无权撤回该消息")

        recalled = await self._chats.recall_message(
            message_id=message_id,
            recalled_by_user_id=actor_id,
            list_preview=_RECALL_LIST_PREVIEW,
        )
        if recalled is None:
            raise NotFoundError("消息不存在")

        members = await self._chats.list_members(chat_id)
        await self._events.publish(
            [m.user_id for m in members], self._message_updated_event(recalled)
        )
        logger.info(
            "chat.message_recalled",
            chat=chat_id,
            message=message_id,
            by=actor_id,
        )
        return recalled

    async def edit_message(
        self, *, chat_id: str, message_id: str, actor_id: str, content: str
    ) -> ChatMessage:
        """Rewrite a plain-text message (消息IM.md §8 S4).

        Sender-only within 15 minutes. Refuses recalled rows, attachment /
        non-text messages, and ``system_card`` / official channels. Fans
        ``chat_message_updated`` (same shape as recall) so clients replace in
        place without bumping unread. Non-members get 404.
        """
        member = await self._chats.get_member(chat_id, actor_id)
        if member is None:
            raise NotFoundError("会话不存在")
        chat = await self._chats.get_chat(chat_id)
        if chat is None:
            raise NotFoundError("会话不存在")
        message = await self._chats.get_message(message_id)
        if message is None or message.chat_id != chat_id:
            raise NotFoundError("消息不存在")

        text = (content or "").strip()
        if not text:
            raise ValidationError("消息内容不能为空")

        if message.recalled_at is not None:
            raise AuthorizationError("已撤回的消息不可编辑")

        is_protected = (
            message.content_type == "system_card"
            or message.sender_type == "official"
            or chat.type == "official"
        )
        if is_protected:
            raise AuthorizationError("无权编辑该消息")

        if message.sender_user_id != actor_id:
            raise AuthorizationError("无权编辑该消息")

        if message.content_type != "text" or (message.attachments or []):
            raise AuthorizationError("附件消息不支持编辑")

        created = message.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if created is None or (now - created) > _EDIT_WINDOW:
            raise AuthorizationError("已超过编辑时限")

        edited = await self._chats.edit_message(
            message_id=message_id,
            content=text,
            list_preview=text,
        )
        if edited is None:
            raise NotFoundError("消息不存在")

        members = await self._chats.list_members(chat_id)
        await self._events.publish(
            [m.user_id for m in members], self._message_updated_event(edited)
        )
        logger.info(
            "chat.message_edited",
            chat=chat_id,
            message=message_id,
            by=actor_id,
        )
        return edited

    async def _resolve_reply_to(
        self, *, chat_id: str, reply_to_message_id: str | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Validate ``reply_to_message_id`` and freeze a lightweight quote snapshot.

        Target must exist and share ``chat_id`` (else 422). Snapshot is written onto
        the new message so a later recall of the target still leaves a readable quote.
        """
        if not reply_to_message_id:
            return None, None
        target = await self._chats.get_message(reply_to_message_id)
        if target is None or target.chat_id != chat_id:
            raise ValidationError("回复的消息不存在或不属于当前会话")
        display_name = _OFFICIAL_DISPLAY_NAME
        if target.sender_user_id:
            user = await self._users.get_by_id(target.sender_user_id)
            display_name = (
                (user.display_name if user and user.display_name else None)
                or (user.username if user else None)
                or "用户"
            )
        return reply_to_message_id, {
            "sender_user_id": target.sender_user_id,
            "sender_display_name": display_name,
            "body_preview": self._body_preview(target),
        }

    async def _resolve_mentions(
        self,
        *,
        chat: Chat,
        sender_id: str,
        mentions: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Validate and freeze structured mentions (deduped; source of truth).

        ``kind=user``: ``user_id`` must be an *accepted* member of this chat (422).
        ``kind=everyone``: group only (422 on dm); caller must be platform admin or
        group moderator else 403.
        """
        if not mentions:
            return []

        members = await self._chats.list_members(chat.id)
        accepted_ids = {m.user_id for m in members if m.state == "accepted"}

        frozen: list[dict[str, Any]] = []
        seen_users: set[str] = set()
        saw_everyone = False

        for raw in mentions:
            kind = raw.get("kind") if isinstance(raw, dict) else None
            if kind == "user":
                user_id = raw.get("user_id")
                if not isinstance(user_id, str) or not user_id:
                    raise ValidationError("@提及的用户无效")
                if user_id in seen_users:
                    continue
                if user_id not in accepted_ids:
                    raise ValidationError("@提及的用户不是本会话成员")
                seen_users.add(user_id)
                frozen.append({"kind": "user", "user_id": user_id})
            elif kind == "everyone":
                if saw_everyone:
                    continue
                if chat.type != "group":
                    raise ValidationError("单聊不支持@所有人")
                if not await self._actor_is_moderator(chat.id, sender_id):
                    raise AuthorizationError("仅管理员可@所有人")
                saw_everyone = True
                frozen.append({"kind": "everyone"})
            else:
                raise ValidationError("@提及类型无效")

        return frozen

    @staticmethod
    def _body_preview(message: ChatMessage) -> str:
        """Truncate body text, or fall back to an attachment-type label."""
        if getattr(message, "recalled_at", None) is not None:
            return "[已撤回]"
        text = (message.content or "").strip()
        if text:
            compact = " ".join(text.split())
            return compact[:_REPLY_PREVIEW_MAX]
        label = _ATTACHMENT_PREVIEW_LABELS.get(message.content_type)
        if label:
            return label
        attachments = message.attachments or []
        if attachments:
            # Prefer image label when any attachment carries a thumbnail.
            if any(
                isinstance(a, dict) and a.get("thumb_path") for a in attachments
            ):
                return "[图片]"
            return "[文件]"
        return ""

    # --- Attachments (富消息: 图/文件，复用工作区存储) ---
    # A chat owns a shared ``ServerWorkspace`` (build_chat_workspace) under
    # ``workspaces/im/<chat_id>/``. Upload then send is two steps: PUT the bytes
    # here, then reference the returned path in a send_message attachment. Both
    # gate membership first (non-member 404), and the chat-scoped backend means a
    # member can only reach this chat's files — never another chat's (no IDOR).

    async def upload_attachment(
        self, *, chat_id: str, user_id: str, path: str, data: bytes
    ) -> AttachmentUpload:
        """Store an attachment's bytes in the chat's workspace; return its metadata.

        Members only (404 otherwise). ``path`` is workspace-relative; one escaping
        the chat space is refused (422). Size limits are enforced at the route
        before the body is read.

        For an image, a bounded WebP thumbnail is generated and stored alongside
        (``<path>.thumb.webp``) so the thread can show cheap inline previews; its
        path rides back in ``thumb_path``. Thumbnailing is best-effort and off the
        event loop (CPU-bound) — a failure leaves ``thumb_path`` None and the
        original is served inline.
        """
        if await self._chats.get_member(chat_id, user_id) is None:
            raise NotFoundError("会话不存在")
        chat = await self._chats.get_chat(chat_id)
        if chat is not None and chat.type == "official":
            raise ValidationError("官方号不支持发送消息")
        backend = build_chat_workspace(chat_id)
        try:
            size_bytes = await backend.write_bytes(path, data)
        except OutsideWorkspace as e:
            raise ValidationError("路径非法：超出会话附件范围") from e
        except WorkspaceIOError as e:
            raise ValidationError(f"附件写入失败：{e}") from e

        thumb_path: str | None = None
        thumbnail = await asyncio.to_thread(make_image_thumbnail, data)
        if thumbnail is not None:
            candidate = f"{path}.thumb.webp"
            try:
                await backend.write_bytes(candidate, thumbnail)
                thumb_path = candidate
            except WorkspaceError as e:
                # The original is already stored and serviceable; a missing
                # thumbnail just means the client inlines the full image.
                logger.warning("chat.thumbnail_store_failed", chat=chat_id, error=str(e))
        return AttachmentUpload(size_bytes=size_bytes, thumb_path=thumb_path)

    async def download_attachment(self, *, chat_id: str, user_id: str, path: str) -> bytes:
        """Return an attachment's raw bytes (members only; 404 otherwise).

        Scoped to this chat's workspace, so a member can fetch only files that
        belong to this chat. 404 for a missing path; 422 for an illegal path.
        """
        if await self._chats.get_member(chat_id, user_id) is None:
            raise NotFoundError("会话不存在")
        backend = build_chat_workspace(chat_id)
        try:
            return await backend.read_bytes(path)
        except OutsideWorkspace as e:
            raise ValidationError("路径非法：超出会话附件范围") from e
        except (PathNotFound, NotAFile) as e:
            raise NotFoundError("附件不存在") from e

    async def list_messages(
        self,
        *,
        chat_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> MessagePage:
        """A page of a chat's messages (oldest first). Non-members get 404."""
        if await self._chats.get_member(chat_id, user_id) is None:
            raise NotFoundError("会话不存在")
        page = max(1, page)
        page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
        offset = (page - 1) * page_size
        messages, total = await self._chats.list_messages(chat_id, limit=page_size, offset=offset)
        return MessagePage(messages=messages, total=total, page=page, page_size=page_size)

    async def mark_read(self, *, chat_id: str, user_id: str, last_read_message_id: str) -> None:
        """Advance the user's read cursor (drives unread counts). Non-members 404."""
        if await self._chats.get_member(chat_id, user_id) is None:
            raise NotFoundError("会话不存在")
        await self._chats.mark_read(chat_id, user_id, last_read_message_id=last_read_message_id)

    # --- Blocking (任意搜人 护栏) ---

    async def block_user(self, *, user_id: str, target_id: str) -> None:
        if target_id == user_id:
            raise ValidationError("不能拉黑自己")
        target = await self._users.get_by_id(target_id)
        if target is None:
            raise NotFoundError("用户不存在")
        await self._blocks.block(user_id, target_id)
        # Cascade: drop friendship + cancel pending friend requests (§九).
        if self._friends is not None:
            await self._friends.remove_friendship(user_id, target_id)
            cancelled = await self._friends.cancel_pending_between(user_id, target_id)
            for req in cancelled:
                await self._publish_friend_request(req, action="cancelled")
        if self._folder_members is not None:
            n = await self._folder_members.delete_pending_between(user_id, target_id)
            if n:
                logger.info(
                    "folder_desk.pending_cleared_on_block",
                    user_a=user_id,
                    user_b=target_id,
                    count=n,
                )
        logger.info("dm.user_blocked", user=user_id, target=target_id)

    async def unblock_user(self, *, user_id: str, target_id: str) -> None:
        await self._blocks.unblock(user_id, target_id)

    async def list_blocked(self, *, user_id: str) -> list[User]:
        blocked_ids = await self._blocks.list_blocked(user_id)
        users = await self._users.get_by_ids(blocked_ids)
        return [users[uid] for uid in blocked_ids if uid in users]

    # --- Friends (消息IM.md §九) ---

    async def get_profile(self, *, viewer_id: str, target_id: str) -> ProfileView:
        """资料卡: relation + request id; non-visible targets → 404 (no leak)."""
        friends = self._require_friends()
        if target_id == viewer_id:
            me = await self._users.get_by_id(viewer_id)
            if me is None or me.status != "active":
                raise NotFoundError("用户不存在")
            return ProfileView(user=me, relation="self", request_id=None)

        target = await self._users.get_by_id(target_id)
        if target is None or target.status != "active":
            raise NotFoundError("用户不存在")

        blocked = await self._blocks.is_blocked_between(viewer_id, target_id)
        are_friends = await friends.are_friends(viewer_id, target_id)
        pending = await friends.get_pending_between(viewer_id, target_id)

        if not blocked and not are_friends and pending is None:
            settings = await self._directory.get(target_id)
            discoverable = settings is None or settings.discoverable
            if not discoverable and not await self._chats.share_group(viewer_id, target_id):
                raise NotFoundError("用户不存在")

        request_id: str | None = None
        if blocked:
            relation: FriendRelation = "blocked"
        elif are_friends:
            relation = "friends"
        elif pending is not None:
            if pending.from_user_id == viewer_id:
                relation = "outgoing_request"
            else:
                relation = "incoming_request"
            request_id = pending.id
        else:
            relation = "none"
        return ProfileView(user=target, relation=relation, request_id=request_id)

    async def list_friends(self, *, user_id: str) -> list[User]:
        friends = self._require_friends()
        friend_ids = await friends.list_friend_ids(user_id)
        users = await self._users.get_by_ids(list(friend_ids))
        # Stable order by display_name then username.
        ordered = [users[uid] for uid in friend_ids if uid in users]
        ordered.sort(key=lambda u: ((u.display_name or "").lower(), u.username.lower()))
        return ordered

    async def list_friend_requests(self, *, user_id: str) -> FriendRequestBox:
        friends = self._require_friends()
        incoming = list(await friends.list_pending_incoming(user_id))
        outgoing = list(await friends.list_pending_outgoing(user_id))
        # Self-heal: drop pending rows that already have a friendship (should not
        # happen after accept, but clears stuck「等待对方处理」if status lagged).
        kept_in: list[FriendRequest] = []
        for req in incoming:
            if await friends.are_friends(user_id, req.from_user_id):
                await friends.set_request_status(req.id, "accepted")
            else:
                kept_in.append(req)
        kept_out: list[FriendRequest] = []
        for req in outgoing:
            if await friends.are_friends(user_id, req.to_user_id):
                await friends.set_request_status(req.id, "accepted")
            else:
                kept_out.append(req)
        return FriendRequestBox(incoming=kept_in, outgoing=kept_out)

    async def get_user(self, user_id: str) -> User | None:
        """Load a user by id without profile visibility gates (inbox peer chip)."""
        user = await self._users.get_by_id(user_id)
        if user is None or user.status != "active":
            return None
        return user

    async def send_friend_request(
        self,
        *,
        from_user_id: str,
        to_user_id: str,
        message: str | None = None,
    ) -> FriendRequest:
        friends = self._require_friends()
        if to_user_id == from_user_id:
            raise ValidationError("不能加自己为好友")
        target = await self._users.get_by_id(to_user_id)
        if target is None or target.status != "active":
            raise NotFoundError("用户不存在")
        if await self._blocks.is_blocked_between(from_user_id, to_user_id):
            raise AuthorizationError("无法向该用户发起好友申请")
        if await friends.are_friends(from_user_id, to_user_id):
            raise ValidationError("你们已经是好友")

        existing = await friends.get_pending_between(from_user_id, to_user_id)
        if existing is not None:
            if existing.from_user_id == from_user_id:
                raise ValidationError("已向对方发起好友申请")
            raise ValidationError("对方已向你发起好友申请，请先处理")

        settings = await self._directory.get(to_user_id)
        who = settings.who_can_friend if settings is not None else "anyone"
        if who == "nobody":
            raise AuthorizationError("对方不接受好友申请")
        if who == "group_members" and not await self._chats.share_group(
            from_user_id, to_user_id
        ):
            raise AuthorizationError("仅共同群成员可发起好友申请")

        msg = (message or "").strip() or None
        if msg is not None and len(msg) > 200:
            raise ValidationError("验证语过长")

        req = await friends.create_request(
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            message=msg,
        )
        await self._publish_friend_request(req, action="created")
        logger.info("friend.request_created", from_user=from_user_id, to_user=to_user_id)
        return req

    async def accept_friend_request(
        self, *, user_id: str, request_id: str
    ) -> FriendRequest:
        friends = self._require_friends()
        req = await friends.get_request(request_id)
        if req is None or req.status != "pending":
            raise NotFoundError("好友申请不存在")
        if req.to_user_id != user_id:
            raise NotFoundError("好友申请不存在")
        if await self._blocks.is_blocked_between(req.from_user_id, req.to_user_id):
            raise AuthorizationError("无法接受该好友申请")

        updated = await friends.set_request_status(request_id, "accepted")
        assert updated is not None
        await friends.add_friendship(req.from_user_id, req.to_user_id)

        # WeChat-style: ensure a mutual DM exists, then post the verification notice.
        dm = await self._chats.get_dm(req.from_user_id, req.to_user_id)
        if dm is None:
            dm = await self._chats.create_dm(
                creator_id=user_id,
                peer_id=req.from_user_id,
                peer_state="accepted",
            )
            await self._publish_chat_changed(
                dm.id, reason="created", user_ids=[req.from_user_id]
            )
        else:
            await self._activate_pending_dm(dm.id, req.from_user_id, req.to_user_id)

        await self._post_system_card(
            chat_id=dm.id,
            content=_FRIEND_ACCEPTED_SYSTEM_TEXT,
            payload={"kind": "friend_accepted"},
        )

        await self._publish_friend_request(updated, action="accepted")
        logger.info(
            "friend.request_accepted",
            request=request_id,
            from_user=req.from_user_id,
            to_user=req.to_user_id,
        )
        return updated

    async def reject_friend_request(
        self, *, user_id: str, request_id: str
    ) -> FriendRequest:
        friends = self._require_friends()
        req = await friends.get_request(request_id)
        if req is None or req.status != "pending":
            raise NotFoundError("好友申请不存在")
        if req.to_user_id != user_id:
            raise NotFoundError("好友申请不存在")
        updated = await friends.set_request_status(request_id, "rejected")
        assert updated is not None
        await self._publish_friend_request(updated, action="rejected")
        logger.info("friend.request_rejected", request=request_id, by=user_id)
        return updated

    async def cancel_friend_request(
        self, *, user_id: str, request_id: str
    ) -> FriendRequest:
        friends = self._require_friends()
        req = await friends.get_request(request_id)
        if req is None or req.status != "pending":
            raise NotFoundError("好友申请不存在")
        if req.from_user_id != user_id:
            raise NotFoundError("好友申请不存在")
        updated = await friends.set_request_status(request_id, "cancelled")
        assert updated is not None
        await self._publish_friend_request(updated, action="cancelled")
        logger.info("friend.request_cancelled", request=request_id, by=user_id)
        return updated

    async def remove_friend(self, *, user_id: str, friend_id: str) -> None:
        friends = self._require_friends()
        if friend_id == user_id:
            raise ValidationError("不能删除自己")
        removed = await friends.remove_friendship(user_id, friend_id)
        if not removed:
            raise NotFoundError("好友关系不存在")
        logger.info("friend.removed", user=user_id, friend=friend_id)

    # --- Directory settings (discoverability + who-can-DM / who-can-friend) ---

    async def get_directory_settings(self, *, user_id: str) -> DirectoryView:
        settings = await self._directory.get(user_id)
        if settings is None:
            return DirectoryView(
                discoverable=True, who_can_dm="anyone", who_can_friend="anyone"
            )
        who_friend = getattr(settings, "who_can_friend", None) or "anyone"
        return DirectoryView(
            discoverable=settings.discoverable,
            who_can_dm=settings.who_can_dm,
            who_can_friend=who_friend,
        )

    async def update_directory_settings(
        self,
        *,
        user_id: str,
        discoverable: bool | None = None,
        who_can_dm: str | None = None,
        who_can_friend: str | None = None,
    ) -> DirectoryView:
        """Patch the user's privacy settings; ``None`` leaves a field unchanged."""
        if who_can_dm is not None and who_can_dm not in ("anyone", "friends"):
            raise ValidationError("who_can_dm 仅支持 anyone / friends")
        if who_can_friend is not None and who_can_friend not in (
            "anyone",
            "group_members",
            "nobody",
        ):
            raise ValidationError("who_can_friend 仅支持 anyone / group_members / nobody")
        changes: dict[str, Any] = {}
        if discoverable is not None:
            changes["discoverable"] = discoverable
        if who_can_dm is not None:
            changes["who_can_dm"] = who_can_dm
        if who_can_friend is not None:
            changes["who_can_friend"] = who_can_friend
        settings = await self._directory.upsert(user_id, **changes)
        who_friend = getattr(settings, "who_can_friend", None) or "anyone"
        return DirectoryView(
            discoverable=settings.discoverable,
            who_can_dm=settings.who_can_dm,
            who_can_friend=who_friend,
        )

    # --- Realtime event payloads ---

    async def _publish_chat_changed(
        self,
        chat_id: str,
        *,
        reason: ChatChangedReason,
        user_ids: list[str],
    ) -> None:
        """Thin membership nudge — clients re-pull ChatView (viewer-scoped)."""
        if not user_ids:
            return
        await self._events.publish(
            user_ids,
            {"type": "chat_changed", "chat_id": chat_id, "reason": reason},
        )

    async def _publish_friend_request(
        self, req: FriendRequest, *, action: FriendRequestAction
    ) -> None:
        event = {
            "type": "friend_request",
            "action": action,
            "request": {
                "id": req.id,
                "from_user_id": req.from_user_id,
                "to_user_id": req.to_user_id,
                "message": req.message,
                "status": req.status,
                "created_at": req.created_at.isoformat() if req.created_at else None,
            },
        }
        await self._events.publish([req.from_user_id, req.to_user_id], event)

    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, Any]:
        """Wire shape shared by ``chat_message`` / ``chat_message_updated``."""
        return {
            "id": message.id,
            "chat_id": message.chat_id,
            "sender_user_id": message.sender_user_id,
            "sender_type": message.sender_type,
            "content": message.content,
            "content_type": message.content_type,
            "attachments": message.attachments or [],
            "payload": message.payload,
            "reply_to_message_id": message.reply_to_message_id,
            "reply_to": message.reply_to,
            "mentions": message.mentions or [],
            "recalled_at": (
                message.recalled_at.isoformat() if message.recalled_at else None
            ),
            "recalled_by_user_id": getattr(message, "recalled_by_user_id", None),
            "edited_at": (
                message.edited_at.isoformat()
                if getattr(message, "edited_at", None)
                else None
            ),
            "created_at": (message.created_at.isoformat() if message.created_at else None),
        }

    @staticmethod
    def _message_event(message: ChatMessage) -> dict[str, Any]:
        """The ``chat_message`` realtime event (mirrors ``ChatMessageDetail``)."""
        return {
            "type": "chat_message",
            "chat_id": message.chat_id,
            "message": MessagingService._message_payload(message),
        }

    @staticmethod
    def _message_updated_event(message: ChatMessage) -> dict[str, Any]:
        """In-place update (recall/edit): clients replace by id — no unread bump."""
        return {
            "type": "chat_message_updated",
            "chat_id": message.chat_id,
            "message": MessagingService._message_payload(message),
        }
