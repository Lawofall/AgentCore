"""Unit tests for MessagingService using in-memory fake repositories (no DB).

Covers the 消息 page (找人 IM) policy: people-search visibility, the start-dm
gates (self / unknown / disabled / blocked / friends-only), friend request
lifecycle, send-message member + block + message-request handling and
idempotency, list/unread, read cursor, blocking, and directory settings.
Mirrors test_auth_service.py's fake-repo style.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.core.errors import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.types import new_id
from agentcore.messaging import MessagingService

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FakeUsers:
    def __init__(self) -> None:
        self._by_id: dict = {}

    def add(self, username, *, status="active", display_name=None, role="user"):
        from types import SimpleNamespace

        user = SimpleNamespace(
            user_id=new_id(),
            username=username,
            display_name=display_name or username,
            status=status,
            role=role,
        )
        self._by_id[user.user_id] = user
        return user

    async def get_by_id(self, user_id):
        return self._by_id.get(user_id)

    async def get_by_ids(self, user_ids):
        return {uid: self._by_id[uid] for uid in user_ids if uid in self._by_id}

    async def search(self, query, *, limit=20):
        q = query.strip()
        if not q:
            return []
        by_id = self._by_id.get(q)
        if by_id is not None and by_id.status == "active":
            return [by_id]
        q_lower = q.lower()
        hits = [
            u for u in self._by_id.values()
            if u.username.lower() == q_lower and u.status == "active"
        ]
        return hits[:limit]


class FakeChats:
    """In-memory chats/members/messages with a counter-driven clock so message
    ordering is deterministic (real created_at could tie on fast inserts).
    """

    def __init__(self) -> None:
        self._chats: dict = {}
        self._members: list = []
        self._messages: list = []
        self._seq = 0

    def _now(self):
        # Near-real clock so the 2-minute recall window is exercisable without
        # per-test created_at patching; ``_seq`` keeps insert order unique.
        self._seq += 1
        return datetime.now(UTC) + timedelta(microseconds=self._seq)

    @staticmethod
    def dm_key(user_a, user_b):
        return ":".join(sorted([user_a, user_b]))

    async def get_dm(self, user_a, user_b):
        key = self.dm_key(user_a, user_b)
        return next((c for c in self._chats.values() if c.dm_key == key), None)

    async def create_dm(self, *, creator_id, peer_id, peer_state="pending"):
        from types import SimpleNamespace

        chat = SimpleNamespace(
            id=new_id(),
            type="dm",
            created_by=creator_id,
            dm_key=self.dm_key(creator_id, peer_id),
            title=None,
            avatar_url=None,
            auto_join=False,
            last_message_at=None,
            last_message_preview=None,
        )
        self._chats[chat.id] = chat
        for uid, state in ((creator_id, "accepted"), (peer_id, peer_state)):
            self._members.append(
                SimpleNamespace(
                    chat_id=chat.id,
                    user_id=uid,
                    role="member",
                    state=state,
                    pinned=False,
                    muted=False,
                    muted_by_admin=False,
                    last_read_at=None,
                    last_read_message_id=None,
                    joined_at=self._now(),
                )
            )
        return chat

    async def create_group(self, *, title="群", auto_join=False, member_ids=(), chat_id=None):
        """Test helper: a group chat (the real row is created by a migration)."""
        from types import SimpleNamespace

        chat = SimpleNamespace(
            id=chat_id or new_id(),
            type="group",
            created_by=None,
            dm_key=None,
            title=title,
            avatar_url=None,
            auto_join=auto_join,
            last_message_at=None,
            last_message_preview=None,
        )
        self._chats[chat.id] = chat
        for uid in member_ids:
            await self.add_member(chat.id, uid)
        return chat

    async def create_official(self, *, title="官方号", auto_join=True, member_ids=()):
        """Test helper: the singleton official broadcast chat."""
        from types import SimpleNamespace

        chat = SimpleNamespace(
            id=new_id(),
            type="official",
            created_by=None,
            dm_key=None,
            title=title,
            avatar_url=None,
            auto_join=auto_join,
            last_message_at=None,
            last_message_preview=None,
        )
        self._chats[chat.id] = chat
        for uid in member_ids:
            await self.add_member(chat.id, uid, pinned=True)
        return chat

    async def list_auto_join_chats(self):
        return [c for c in self._chats.values() if getattr(c, "auto_join", False)]

    async def get_official_chat(self):
        return next((c for c in self._chats.values() if c.type == "official"), None)

    async def get_or_create_official_chat(self):
        existing = await self.get_official_chat()
        if existing is not None:
            return existing
        return await self.create_official()

    async def add_member(self, chat_id, user_id, *, role="member", state="accepted", pinned=False):
        from types import SimpleNamespace

        if await self.get_member(chat_id, user_id) is not None:
            return
        self._members.append(
            SimpleNamespace(
                chat_id=chat_id,
                user_id=user_id,
                role=role,
                state=state,
                pinned=pinned,
                muted=False,
                muted_by_admin=False,
                last_read_at=None,
                last_read_message_id=None,
                joined_at=self._now(),
            )
        )

    async def get_chat(self, chat_id):
        return self._chats.get(chat_id)

    async def get_member(self, chat_id, user_id):
        return next(
            (m for m in self._members if m.chat_id == chat_id and m.user_id == user_id),
            None,
        )

    async def list_members(self, chat_id):
        return [m for m in self._members if m.chat_id == chat_id]

    async def remove_member(self, chat_id, user_id):
        self._members = [
            m for m in self._members if not (m.chat_id == chat_id and m.user_id == user_id)
        ]

    async def set_membership_flags(self, chat_id, user_id, *, muted=None, pinned=None):
        member = await self.get_member(chat_id, user_id)
        if member is None:
            return
        if muted is not None:
            member.muted = muted
        if pinned is not None:
            member.pinned = pinned

    async def set_admin_mute(self, chat_id, user_id, *, muted_by_admin):
        member = await self.get_member(chat_id, user_id)
        if member is not None:
            member.muted_by_admin = muted_by_admin

    async def set_member_role(self, chat_id, user_id, *, role):
        member = await self.get_member(chat_id, user_id)
        if member is None:
            return None
        member.role = role
        return member

    async def list_memberships(self, user_id):
        rows = [(self._chats[m.chat_id], m) for m in self._members if m.user_id == user_id]
        rows.sort(key=lambda cm: cm[0].last_message_at or _EPOCH, reverse=True)
        return rows

    async def peer_ids_for(self, chat_ids, *, exclude_user_id):
        out: dict = {}
        for m in self._members:
            if m.chat_id in chat_ids and m.user_id != exclude_user_id:
                out.setdefault(m.chat_id, m.user_id)
        return out

    async def list_co_member_ids(self, user_id):
        my_chats = {m.chat_id for m in self._members if m.user_id == user_id}
        return sorted(
            {
                m.user_id
                for m in self._members
                if m.chat_id in my_chats and m.user_id != user_id
            }
        )

    async def get_message(self, message_id):
        return next((m for m in self._messages if m.id == message_id), None)

    async def add_message(
        self,
        *,
        chat_id,
        sender_user_id,
        content,
        sender_type="user",
        content_type="text",
        attachments=None,
        payload=None,
        reply_to_message_id=None,
        reply_to=None,
        mentions=None,
        client_msg_id=None,
    ):
        from types import SimpleNamespace

        if client_msg_id is not None and sender_user_id is not None:
            existing = next(
                (
                    m
                    for m in self._messages
                    if m.chat_id == chat_id
                    and m.sender_user_id == sender_user_id
                    and m.client_msg_id == client_msg_id
                ),
                None,
            )
            if existing is not None:
                return existing
        msg = SimpleNamespace(
            id=new_id(),
            chat_id=chat_id,
            sender_user_id=sender_user_id,
            sender_type=sender_type,
            content=content,
            content_type=content_type,
            attachments=attachments or [],
            payload=payload,
            reply_to_message_id=reply_to_message_id,
            reply_to=reply_to,
            mentions=list(mentions) if mentions is not None else [],
            client_msg_id=client_msg_id,
            recalled_at=None,
            recalled_by_user_id=None,
            edited_at=None,
            created_at=self._now(),
        )
        self._messages.append(msg)
        chat = self._chats[chat_id]
        chat.last_message_at = msg.created_at
        chat.last_message_preview = (content or "")[:200]
        return msg

    async def recall_message(self, *, message_id, recalled_by_user_id, list_preview):
        msg = await self.get_message(message_id)
        if msg is None:
            return None
        if getattr(msg, "recalled_at", None) is not None:
            return msg
        msg.recalled_at = self._now()
        msg.recalled_by_user_id = recalled_by_user_id
        msg.content = None
        msg.attachments = []
        msg.payload = None
        rows = sorted(
            (m for m in self._messages if m.chat_id == msg.chat_id),
            key=lambda m: m.created_at,
        )
        if rows and rows[-1].id == msg.id:
            self._chats[msg.chat_id].last_message_preview = (list_preview or "")[:200]
        return msg

    async def edit_message(self, *, message_id, content, list_preview):
        msg = await self.get_message(message_id)
        if msg is None:
            return None
        msg.content = content
        msg.edited_at = self._now()
        rows = sorted(
            (m for m in self._messages if m.chat_id == msg.chat_id),
            key=lambda m: m.created_at,
        )
        if list_preview is not None and rows and rows[-1].id == msg.id:
            self._chats[msg.chat_id].last_message_preview = (list_preview or "")[:200]
        return msg

    async def list_messages(self, chat_id, *, limit=50, offset=0):
        rows = sorted(
            (m for m in self._messages if m.chat_id == chat_id),
            key=lambda m: m.created_at,
        )
        return rows[offset : offset + limit], len(rows)

    async def mark_read(self, chat_id, user_id, *, last_read_message_id, last_read_at=None):
        member = await self.get_member(chat_id, user_id)
        member.last_read_message_id = last_read_message_id
        member.last_read_at = last_read_at or self._now()

    async def accept_request(self, chat_id, user_id):
        member = await self.get_member(chat_id, user_id)
        member.state = "accepted"

    async def share_group(self, user_a, user_b):
        a_groups = {
            m.chat_id
            for m in self._members
            if m.user_id == user_a
            and m.chat_id in self._chats
            and getattr(self._chats[m.chat_id], "type", None) == "group"
        }
        b_groups = {
            m.chat_id
            for m in self._members
            if m.user_id == user_b
            and m.chat_id in self._chats
            and getattr(self._chats[m.chat_id], "type", None) == "group"
        }
        return bool(a_groups & b_groups)

    async def unread_counts(self, user_id):
        out: dict = {}
        my_chats = {m.chat_id: m for m in self._members if m.user_id == user_id}
        for msg in self._messages:
            member = my_chats.get(msg.chat_id)
            if member is None or msg.sender_user_id == user_id:
                continue
            if member.last_read_at is None or msg.created_at > member.last_read_at:
                out[msg.chat_id] = out.get(msg.chat_id, 0) + 1
        return out


class FakeBlocks:
    def __init__(self) -> None:
        self._pairs: set = set()

    async def is_blocked_between(self, user_a, user_b):
        return (user_a, user_b) in self._pairs or (user_b, user_a) in self._pairs

    async def block(self, user_id, blocked_user_id):
        self._pairs.add((user_id, blocked_user_id))

    async def unblock(self, user_id, blocked_user_id):
        self._pairs.discard((user_id, blocked_user_id))

    async def list_blocked(self, user_id):
        return [b for (a, b) in self._pairs if a == user_id]


class FakeFriends:
    def __init__(self) -> None:
        self._pairs: set = set()
        self._requests: dict = {}

    @staticmethod
    def _pair(a, b):
        return (a, b) if a < b else (b, a)

    @staticmethod
    def _pair_key(a, b):
        x, y = (a, b) if a < b else (b, a)
        return f"{x}:{y}"

    async def are_friends(self, user_a, user_b):
        return self._pair(user_a, user_b) in self._pairs

    async def list_friend_ids(self, user_id):
        out = []
        for a, b in self._pairs:
            if a == user_id:
                out.append(b)
            elif b == user_id:
                out.append(a)
        return out

    async def add_friendship(self, user_a, user_b):
        self._pairs.add(self._pair(user_a, user_b))

    async def remove_friendship(self, user_a, user_b):
        key = self._pair(user_a, user_b)
        if key not in self._pairs:
            return False
        self._pairs.discard(key)
        return True

    async def get_request(self, request_id):
        return self._requests.get(request_id)

    async def get_pending_between(self, user_a, user_b):
        key = self._pair_key(user_a, user_b)
        for req in self._requests.values():
            if req.status == "pending" and req.pair_key == key:
                return req
        return None

    async def create_request(self, *, from_user_id, to_user_id, message=None):
        from types import SimpleNamespace

        req = SimpleNamespace(
            id=new_id(),
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            pair_key=self._pair_key(from_user_id, to_user_id),
            message=message,
            status="pending",
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )
        self._requests[req.id] = req
        return req

    async def set_request_status(self, request_id, status):
        req = self._requests.get(request_id)
        if req is None:
            return None
        req.status = status
        return req

    async def cancel_pending_between(self, user_a, user_b):
        key = self._pair_key(user_a, user_b)
        cancelled = []
        for req in self._requests.values():
            if req.status == "pending" and req.pair_key == key:
                req.status = "cancelled"
                cancelled.append(req)
        return cancelled

    async def list_pending_incoming(self, user_id):
        return [
            r
            for r in self._requests.values()
            if r.to_user_id == user_id and r.status == "pending"
        ]

    async def list_pending_outgoing(self, user_id):
        return [
            r
            for r in self._requests.values()
            if r.from_user_id == user_id and r.status == "pending"
        ]


class FakeDirectory:
    def __init__(self) -> None:
        self._by_user: dict = {}

    async def get(self, user_id):
        return self._by_user.get(user_id)

    async def upsert(self, user_id, *, discoverable=None, who_can_dm=None, who_can_friend=None):
        from types import SimpleNamespace

        settings = self._by_user.get(user_id)
        if settings is None:
            settings = SimpleNamespace(
                user_id=user_id,
                discoverable=True,
                who_can_dm="anyone",
                who_can_friend="anyone",
            )
            self._by_user[user_id] = settings
        if discoverable is not None:
            settings.discoverable = discoverable
        if who_can_dm is not None:
            settings.who_can_dm = who_can_dm
        if who_can_friend is not None:
            settings.who_can_friend = who_can_friend
        return settings

    def set(self, user_id, *, discoverable=True, who_can_dm="anyone", who_can_friend="anyone"):
        from types import SimpleNamespace

        self._by_user[user_id] = SimpleNamespace(
            user_id=user_id,
            discoverable=discoverable,
            who_can_dm=who_can_dm,
            who_can_friend=who_can_friend,
        )


class FakeEvents:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, user_ids, event):
        self.published.append((list(user_ids), event))


def _make():
    users = FakeUsers()
    chats = FakeChats()
    blocks = FakeBlocks()
    directory = FakeDirectory()
    friends = FakeFriends()
    events = FakeEvents()
    svc = MessagingService(
        users=users,
        chats=chats,
        blocks=blocks,
        directory=directory,
        friends=friends,
        events=events,
    )
    return svc, users, chats, blocks, directory, events, friends


# --- search_users ---


async def test_search_returns_exact_match():
    svc, users, *_ = _make()
    alice = users.add("alice")
    users.add("bob")
    hits = await svc.search_users(requester_id=alice.user_id, query="bob")
    assert [u.username for u in hits] == ["bob"]


async def test_search_excludes_self():
    svc, users, *_ = _make()
    alice = users.add("alice")
    hits = await svc.search_users(requester_id=alice.user_id, query="alice")
    assert hits == []


async def test_search_excludes_blocked_pair():
    svc, users, _chats, blocks, *_ = _make()
    alice = users.add("alice")
    carol = users.add("carol")
    await blocks.block(alice.user_id, carol.user_id)
    assert await svc.search_users(requester_id=alice.user_id, query="carol") == []
    # symmetric: carol also cannot find alice
    assert await svc.search_users(requester_id=carol.user_id, query="alice") == []


async def test_search_excludes_undiscoverable():
    svc, users, _chats, _blocks, directory, _events, _friends = _make()
    alice = users.add("alice")
    carol = users.add("carol")
    directory.set(carol.user_id, discoverable=False)
    assert await svc.search_users(requester_id=alice.user_id, query="carol") == []


# --- start_dm ---


async def test_start_dm_creates_chat_peer_pending():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    view = await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    assert view.chat.type == "dm"
    assert view.peer.user_id == bob.user_id
    assert view.member.state == "accepted"
    peer_member = await chats.get_member(view.chat.id, bob.user_id)
    assert peer_member.state == "pending"
    created = [
        (uids, ev)
        for uids, ev in events.published
        if ev.get("type") == "chat_changed" and ev.get("reason") == "created"
    ]
    assert len(created) == 1
    assert created[0][0] == [bob.user_id]
    assert created[0][1]["chat_id"] == view.chat.id


async def test_start_dm_reuses_existing():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    first = await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    # the peer opening it from their side resolves to the same chat row
    second = await svc.start_dm(requester_id=bob.user_id, peer_id=alice.user_id)
    assert first.chat.id == second.chat.id


async def test_start_dm_self_raises():
    svc, users, *_ = _make()
    alice = users.add("alice")
    with pytest.raises(ValidationError):
        await svc.start_dm(requester_id=alice.user_id, peer_id=alice.user_id)


async def test_start_dm_unknown_peer_raises():
    svc, users, *_ = _make()
    alice = users.add("alice")
    with pytest.raises(NotFoundError):
        await svc.start_dm(requester_id=alice.user_id, peer_id="ghost")


async def test_start_dm_disabled_peer_raises():
    svc, users, *_ = _make()
    alice = users.add("alice")
    banned = users.add("banned", status="disabled")
    with pytest.raises(NotFoundError):
        await svc.start_dm(requester_id=alice.user_id, peer_id=banned.user_id)


async def test_start_dm_blocked_raises():
    svc, users, _chats, blocks, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    await blocks.block(bob.user_id, alice.user_id)
    with pytest.raises(AuthorizationError):
        await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)


async def test_start_dm_friends_only_raises():
    svc, users, _chats, _blocks, directory, _events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    directory.set(bob.user_id, who_can_dm="friends")
    with pytest.raises(AuthorizationError):
        await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)


async def test_start_dm_reuse_skips_friends_gate():
    svc, users, _chats, _blocks, directory, _events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    first = await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    # bob later locks down to friends-only; the existing dm still reopens
    directory.set(bob.user_id, who_can_dm="friends")
    again = await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    assert first.chat.id == again.chat.id


async def test_start_dm_friends_both_accepted():
    svc, users, chats, _blocks, _directory, _events, friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    await friends.add_friendship(alice.user_id, bob.user_id)
    view = await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    peer_member = await chats.get_member(view.chat.id, bob.user_id)
    assert peer_member.state == "accepted"


async def test_start_dm_anyone_message_request():
    svc, users, chats, _blocks, directory, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    directory.set(bob.user_id, who_can_dm="anyone")
    view = await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    peer_member = await chats.get_member(view.chat.id, bob.user_id)
    assert peer_member.state == "pending"


# --- send_message ---


async def test_send_message_non_member_404():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    stranger = users.add("stranger")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(NotFoundError):
        await svc.send_message(chat_id=chat.id, sender_id=stranger.user_id, content="hi")


async def test_send_message_persists_and_fans_out():
    svc, users, _chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    events.published.clear()
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="hello bob")
    assert msg.content == "hello bob"
    assert msg.reply_to_message_id is None
    assert msg.reply_to is None
    assert len(events.published) == 1
    recipients, event = events.published[0]
    assert set(recipients) == {alice.user_id, bob.user_id}
    assert event["type"] == "chat_message"
    assert event["message"]["id"] == msg.id
    assert event["message"]["reply_to"] is None
    assert event["message"]["mentions"] == []
    assert msg.mentions == []


async def test_send_message_mentions_user_accepted_member():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    carol = users.add("carol")
    chat = await chats.create_group(member_ids=(alice.user_id, bob.user_id, carol.user_id))
    msg = await svc.send_message(
        chat_id=chat.id,
        sender_id=alice.user_id,
        content="hey @bob",
        mentions=[{"kind": "user", "user_id": bob.user_id}],
    )
    assert msg.mentions == [{"kind": "user", "user_id": bob.user_id}]
    _recipients, event = events.published[-1]
    assert event["message"]["mentions"] == [{"kind": "user", "user_id": bob.user_id}]


async def test_send_message_mentions_non_member_rejected():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    stranger = users.add("stranger")
    chat = await chats.create_group(member_ids=(alice.user_id, bob.user_id))
    with pytest.raises(ValidationError, match="@提及的用户不是本会话成员"):
        await svc.send_message(
            chat_id=chat.id,
            sender_id=alice.user_id,
            content="hey stranger",
            mentions=[{"kind": "user", "user_id": stranger.user_id}],
        )


async def test_send_message_mentions_pending_member_rejected():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    # bob is pending until they accept; @ requires accepted membership.
    with pytest.raises(ValidationError, match="@提及的用户不是本会话成员"):
        await svc.send_message(
            chat_id=chat.id,
            sender_id=alice.user_id,
            content="hey bob",
            mentions=[{"kind": "user", "user_id": bob.user_id}],
        )


async def test_send_message_mentions_everyone_admin_ok():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    admin = users.add("admin", role="admin")
    bob = users.add("bob")
    chat = await chats.create_group(member_ids=(admin.user_id, bob.user_id))
    msg = await svc.send_message(
        chat_id=chat.id,
        sender_id=admin.user_id,
        content="@所有人 standup",
        mentions=[{"kind": "everyone"}],
    )
    assert msg.mentions == [{"kind": "everyone"}]
    _recipients, event = events.published[-1]
    assert event["message"]["mentions"] == [{"kind": "everyone"}]


async def test_send_message_mentions_everyone_non_admin_403():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")  # platform role=user
    bob = users.add("bob")
    chat = await chats.create_group(member_ids=(alice.user_id, bob.user_id))
    with pytest.raises(AuthorizationError, match="仅管理员可@所有人"):
        await svc.send_message(
            chat_id=chat.id,
            sender_id=alice.user_id,
            content="@所有人",
            mentions=[{"kind": "everyone"}],
        )


async def test_send_message_mentions_everyone_dm_rejected():
    svc, users, *_ = _make()
    admin = users.add("admin", role="admin")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=admin.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(ValidationError, match="单聊不支持@所有人"):
        await svc.send_message(
            chat_id=chat.id,
            sender_id=admin.user_id,
            content="@所有人",
            mentions=[{"kind": "everyone"}],
        )


async def test_send_message_mentions_dedupes_user_and_everyone():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    bob = users.add("bob")
    chat = await chats.create_group(member_ids=(admin.user_id, bob.user_id))
    msg = await svc.send_message(
        chat_id=chat.id,
        sender_id=admin.user_id,
        content="hey",
        mentions=[
            {"kind": "user", "user_id": bob.user_id},
            {"kind": "user", "user_id": bob.user_id},
            {"kind": "everyone"},
            {"kind": "everyone"},
        ],
    )
    assert msg.mentions == [
        {"kind": "user", "user_id": bob.user_id},
        {"kind": "everyone"},
    ]


async def test_send_message_reply_freezes_snapshot():
    svc, users, _chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice", display_name="Alice Chen")
    bob = users.add("bob", display_name="Bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    original = await svc.send_message(
        chat_id=chat.id, sender_id=alice.user_id, content="hello bob"
    )
    reply = await svc.send_message(
        chat_id=chat.id,
        sender_id=bob.user_id,
        content="hi alice",
        reply_to_message_id=original.id,
    )
    assert reply.reply_to_message_id == original.id
    assert reply.reply_to == {
        "sender_user_id": alice.user_id,
        "sender_display_name": "Alice Chen",
        "body_preview": "hello bob",
    }
    _recipients, event = events.published[-1]
    assert event["message"]["reply_to_message_id"] == original.id
    assert event["message"]["reply_to"] == reply.reply_to


async def test_send_message_reply_preview_collapses_whitespace_and_caps():
    svc, users, *_ = _make()
    alice = users.add("alice", display_name="Alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    original = await svc.send_message(
        chat_id=chat.id,
        sender_id=alice.user_id,
        content="hello\n\nbob   there " + ("x" * 120),
    )
    reply = await svc.send_message(
        chat_id=chat.id,
        sender_id=bob.user_id,
        content="ok",
        reply_to_message_id=original.id,
    )
    preview = reply.reply_to["body_preview"]
    assert "\n" not in preview
    assert "  " not in preview
    assert preview.startswith("hello bob there ")
    assert len(preview) == 100


async def test_send_message_reply_attachment_preview_label():
    svc, users, *_ = _make()
    alice = users.add("alice", display_name="Alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    original = await svc.send_message(
        chat_id=chat.id,
        sender_id=alice.user_id,
        content=None,
        content_type="image",
        attachments=[{"name": "a.png", "path": "a.png", "thumb_path": "a.png.thumb.webp"}],
    )
    reply = await svc.send_message(
        chat_id=chat.id,
        sender_id=bob.user_id,
        content="nice pic",
        reply_to_message_id=original.id,
    )
    assert reply.reply_to["body_preview"] == "[图片]"
    assert reply.reply_to["sender_display_name"] == "Alice"


async def test_send_message_reply_unknown_id_rejected():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(ValidationError, match="回复的消息"):
        await svc.send_message(
            chat_id=chat.id,
            sender_id=alice.user_id,
            content="orphan reply",
            reply_to_message_id=new_id(),
        )


async def test_send_message_reply_cross_chat_rejected():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    carol = users.add("carol")
    chat_ab = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    chat_ac = (await svc.start_dm(requester_id=alice.user_id, peer_id=carol.user_id)).chat
    in_ab = await svc.send_message(chat_id=chat_ab.id, sender_id=alice.user_id, content="to bob")
    with pytest.raises(ValidationError, match="回复的消息"):
        await svc.send_message(
            chat_id=chat_ac.id,
            sender_id=alice.user_id,
            content="cross",
            reply_to_message_id=in_ab.id,
        )


# --- recall (S3 撤回) ---


async def test_recall_own_message_within_window():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="secret")
    msg.created_at = datetime.now(UTC)
    events.published.clear()
    recalled = await svc.recall_message(
        chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id
    )
    assert recalled.recalled_at is not None
    assert recalled.recalled_by_user_id == alice.user_id
    assert recalled.content is None
    assert chats._chats[chat.id].last_message_preview == "[已撤回]"
    recipients, event = events.published[-1]
    assert set(recipients) == {alice.user_id, bob.user_id}
    assert event["type"] == "chat_message_updated"
    assert event["message"]["id"] == msg.id
    assert event["message"]["recalled_at"] is not None
    assert event["message"]["content"] is None


async def test_recall_own_message_after_window_403():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="late")
    msg.created_at = datetime.now(UTC) - timedelta(minutes=3)
    with pytest.raises(AuthorizationError, match="撤回时限"):
        await svc.recall_message(chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id)


async def test_recall_other_user_message_403():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="hi")
    with pytest.raises(AuthorizationError, match="无权撤回"):
        await svc.recall_message(chat_id=chat.id, message_id=msg.id, actor_id=bob.user_id)


async def test_admin_recall_group_member_message_no_window():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    msg = await svc.send_message(chat_id=group.id, sender_id=alice.user_id, content="spam")
    msg.created_at = datetime.now(UTC) - timedelta(hours=1)
    events.published.clear()
    recalled = await svc.recall_message(
        chat_id=group.id, message_id=msg.id, actor_id=admin.user_id
    )
    assert recalled.recalled_by_user_id == admin.user_id
    assert events.published[-1][1]["type"] == "chat_message_updated"


async def test_admin_cannot_recall_others_dm_after_window():
    """Admin governance is group-scoped — DM still needs the sender window."""
    svc, users, *_ = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    chat = (await svc.start_dm(requester_id=admin.user_id, peer_id=alice.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="hi")
    msg.created_at = datetime.now(UTC) - timedelta(minutes=5)
    with pytest.raises(AuthorizationError):
        await svc.recall_message(chat_id=chat.id, message_id=msg.id, actor_id=admin.user_id)


async def test_non_admin_cannot_recall_system_card():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    notice = await svc.post_announcement(
        chat_id=group.id, actor_id=admin.user_id, content="公告"
    )
    with pytest.raises(AuthorizationError, match="无权撤回"):
        await svc.recall_message(
            chat_id=group.id, message_id=notice.id, actor_id=alice.user_id
        )


async def test_admin_can_recall_system_card():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    notice = await svc.post_announcement(
        chat_id=group.id, actor_id=admin.user_id, content="公告"
    )
    recalled = await svc.recall_message(
        chat_id=group.id, message_id=notice.id, actor_id=admin.user_id
    )
    assert recalled.recalled_at is not None
    assert recalled.content is None


async def test_recall_keeps_reply_snapshot_readable():
    svc, users, *_ = _make()
    alice = users.add("alice", display_name="Alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    original = await svc.send_message(
        chat_id=chat.id, sender_id=alice.user_id, content="quote me"
    )
    original.created_at = datetime.now(UTC)
    reply = await svc.send_message(
        chat_id=chat.id,
        sender_id=bob.user_id,
        content="ok",
        reply_to_message_id=original.id,
    )
    assert reply.reply_to["body_preview"] == "quote me"
    await svc.recall_message(chat_id=chat.id, message_id=original.id, actor_id=alice.user_id)
    # Frozen snapshot on the reply row is unchanged.
    assert reply.reply_to["body_preview"] == "quote me"
    # New replies to a recalled target get the withdrawn label.
    later = await svc.send_message(
        chat_id=chat.id,
        sender_id=bob.user_id,
        content="again",
        reply_to_message_id=original.id,
    )
    assert later.reply_to["body_preview"] == "[已撤回]"


async def test_recall_idempotent():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="x")
    msg.created_at = datetime.now(UTC)
    first = await svc.recall_message(chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id)
    second = await svc.recall_message(chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id)
    assert first.recalled_at == second.recalled_at


# --- edit (S4 编辑) ---


async def test_edit_own_text_within_window():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="old")
    msg.created_at = datetime.now(UTC)
    events.published.clear()
    edited = await svc.edit_message(
        chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id, content="new body"
    )
    assert edited.content == "new body"
    assert edited.edited_at is not None
    assert chats._chats[chat.id].last_message_preview == "new body"
    recipients, event = events.published[-1]
    assert set(recipients) == {alice.user_id, bob.user_id}
    assert event["type"] == "chat_message_updated"
    assert event["message"]["content"] == "new body"
    assert event["message"]["edited_at"] is not None


async def test_edit_own_message_after_window_403():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="old")
    msg.created_at = datetime.now(UTC) - timedelta(minutes=16)
    with pytest.raises(AuthorizationError, match="编辑时限"):
        await svc.edit_message(
            chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id, content="new"
        )


async def test_edit_other_user_message_403():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="old")
    msg.created_at = datetime.now(UTC)
    with pytest.raises(AuthorizationError, match="无权编辑"):
        await svc.edit_message(
            chat_id=chat.id, message_id=msg.id, actor_id=bob.user_id, content="hack"
        )


async def test_edit_attachment_message_403():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(
        chat_id=chat.id,
        sender_id=alice.user_id,
        content=None,
        content_type="image",
        attachments=[{"name": "a.png", "workspace_path": "a.png"}],
    )
    msg.created_at = datetime.now(UTC)
    with pytest.raises(AuthorizationError, match="附件"):
        await svc.edit_message(
            chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id, content="caption"
        )


async def test_edit_recalled_message_403():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    msg = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="old")
    msg.created_at = datetime.now(UTC)
    await svc.recall_message(chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id)
    with pytest.raises(AuthorizationError, match="已撤回"):
        await svc.edit_message(
            chat_id=chat.id, message_id=msg.id, actor_id=alice.user_id, content="new"
        )


async def test_edit_does_not_refresh_preview_when_not_latest():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    first = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="first")
    first.created_at = datetime.now(UTC) - timedelta(seconds=10)
    second = await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="second")
    second.created_at = datetime.now(UTC)
    assert chats._chats[chat.id].last_message_preview == "second"
    await svc.edit_message(
        chat_id=chat.id, message_id=first.id, actor_id=alice.user_id, content="edited first"
    )
    assert chats._chats[chat.id].last_message_preview == "second"


async def test_send_message_blocked_dm_raises():
    svc, users, _chats, blocks, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    await blocks.block(bob.user_id, alice.user_id)
    with pytest.raises(AuthorizationError):
        await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="hi")


async def test_reply_accepts_pending_request():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    # alice's opening message leaves bob pending (a message request)
    await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content="hi bob")
    assert (await chats.get_member(chat.id, bob.user_id)).state == "pending"
    # bob replying accepts the request
    await svc.send_message(chat_id=chat.id, sender_id=bob.user_id, content="hey")
    assert (await chats.get_member(chat.id, bob.user_id)).state == "accepted"


async def test_send_message_idempotent_client_msg_id():
    svc, users, _chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    first = await svc.send_message(
        chat_id=chat.id, sender_id=alice.user_id, content="hi", client_msg_id="c1"
    )
    second = await svc.send_message(
        chat_id=chat.id, sender_id=alice.user_id, content="hi", client_msg_id="c1"
    )
    assert first.id == second.id
    page = await svc.list_messages(chat_id=chat.id, user_id=alice.user_id)
    assert page.total == 1


# --- list_chats / unread / mark_read ---


async def test_list_chats_resolves_peer_and_unread():
    svc, users, _chats, _blocks, _directory, _events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    await svc.send_message(chat_id=chat.id, sender_id=bob.user_id, content="m1")
    last = await svc.send_message(chat_id=chat.id, sender_id=bob.user_id, content="m2")

    views = await svc.list_chats(user_id=alice.user_id)
    assert len(views) == 1
    assert views[0].peer.user_id == bob.user_id
    assert views[0].unread == 2

    await svc.mark_read(chat_id=chat.id, user_id=alice.user_id, last_read_message_id=last.id)
    views = await svc.list_chats(user_id=alice.user_id)
    assert views[0].unread == 0


async def test_list_chats_orders_recent_first():
    svc, users, _chats, _blocks, _directory, _events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    carol = users.add("carol")
    chat_b = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    chat_c = (await svc.start_dm(requester_id=alice.user_id, peer_id=carol.user_id)).chat
    await svc.send_message(chat_id=chat_b.id, sender_id=alice.user_id, content="b")
    await svc.send_message(chat_id=chat_c.id, sender_id=alice.user_id, content="c")
    # chat_c has the most recent message -> it sorts first
    views = await svc.list_chats(user_id=alice.user_id)
    assert [v.chat.id for v in views] == [chat_c.id, chat_b.id]


# --- list_messages ---


async def test_list_messages_paginates():
    svc, users, _chats, _blocks, _directory, _events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    for i in range(5):
        await svc.send_message(chat_id=chat.id, sender_id=alice.user_id, content=f"m{i}")
    page = await svc.list_messages(chat_id=chat.id, user_id=alice.user_id, page=1, page_size=2)
    assert page.total == 5
    assert [m.content for m in page.messages] == ["m0", "m1"]
    page2 = await svc.list_messages(chat_id=chat.id, user_id=alice.user_id, page=2, page_size=2)
    assert [m.content for m in page2.messages] == ["m2", "m3"]


async def test_list_messages_non_member_404():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    stranger = users.add("stranger")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(NotFoundError):
        await svc.list_messages(chat_id=chat.id, user_id=stranger.user_id)


async def test_mark_read_non_member_404():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    stranger = users.add("stranger")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(NotFoundError):
        await svc.mark_read(chat_id=chat.id, user_id=stranger.user_id, last_read_message_id="x")


# --- blocking ---


async def test_block_self_raises():
    svc, users, *_ = _make()
    alice = users.add("alice")
    with pytest.raises(ValidationError):
        await svc.block_user(user_id=alice.user_id, target_id=alice.user_id)


async def test_block_unknown_target_raises():
    svc, users, *_ = _make()
    alice = users.add("alice")
    with pytest.raises(NotFoundError):
        await svc.block_user(user_id=alice.user_id, target_id="ghost")


async def test_block_list_and_unblock_roundtrip():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    await svc.block_user(user_id=alice.user_id, target_id=bob.user_id)
    blocked = await svc.list_blocked(user_id=alice.user_id)
    assert [u.user_id for u in blocked] == [bob.user_id]
    await svc.unblock_user(user_id=alice.user_id, target_id=bob.user_id)
    assert await svc.list_blocked(user_id=alice.user_id) == []


# --- directory settings ---


async def test_directory_defaults_when_missing():
    svc, users, *_ = _make()
    alice = users.add("alice")
    view = await svc.get_directory_settings(user_id=alice.user_id)
    assert view.discoverable is True
    assert view.who_can_dm == "anyone"
    assert view.who_can_friend == "anyone"


async def test_update_directory_partial_preserves_other_field():
    svc, users, *_ = _make()
    alice = users.add("alice")
    await svc.update_directory_settings(user_id=alice.user_id, discoverable=False)
    view = await svc.update_directory_settings(user_id=alice.user_id, who_can_dm="friends")
    assert view.discoverable is False  # untouched by the second patch
    assert view.who_can_dm == "friends"


async def test_update_directory_rejects_legacy_contacts():
    svc, users, *_ = _make()
    alice = users.add("alice")
    with pytest.raises(ValidationError, match="who_can_dm"):
        await svc.update_directory_settings(user_id=alice.user_id, who_can_dm="contacts")


# --- friends (§九) ---


async def test_friend_request_lifecycle():
    svc, users, chats, _blocks, _directory, events, friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    req = await svc.send_friend_request(
        from_user_id=alice.user_id, to_user_id=bob.user_id, message="hi"
    )
    assert req.status == "pending"
    assert any(e[1]["type"] == "friend_request" and e[1]["action"] == "created" for e in events.published)

    box = await svc.list_friend_requests(user_id=bob.user_id)
    assert len(box.incoming) == 1
    assert box.incoming[0].id == req.id

    accepted = await svc.accept_friend_request(user_id=bob.user_id, request_id=req.id)
    assert accepted.status == "accepted"
    assert await friends.are_friends(alice.user_id, bob.user_id)
    # Both sides' address books must list each other (prior coverage only checked initiator).
    friend_list_alice = await svc.list_friends(user_id=alice.user_id)
    assert [u.user_id for u in friend_list_alice] == [bob.user_id]
    friend_list_bob = await svc.list_friends(user_id=bob.user_id)
    assert [u.user_id for u in friend_list_bob] == [alice.user_id]
    # Initiator's outgoing inbox must clear — otherwise UI keeps「等待对方处理」.
    alice_box = await svc.list_friend_requests(user_id=alice.user_id)
    assert alice_box.outgoing == []
    bob_box = await svc.list_friend_requests(user_id=bob.user_id)
    assert bob_box.incoming == []

    accepted_events = [
        (uids, ev)
        for uids, ev in events.published
        if ev.get("type") == "friend_request" and ev.get("action") == "accepted"
    ]
    assert len(accepted_events) == 1
    assert set(accepted_events[0][0]) == {alice.user_id, bob.user_id}

    dm = await chats.get_dm(alice.user_id, bob.user_id)
    assert dm is not None
    alice_m = await chats.get_member(dm.id, alice.user_id)
    bob_m = await chats.get_member(dm.id, bob.user_id)
    assert alice_m.state == "accepted"
    assert bob_m.state == "accepted"
    msgs, total = await chats.list_messages(dm.id)
    assert total == 1
    assert msgs[0].content_type == "system_card"
    assert msgs[0].sender_user_id is None
    assert msgs[0].content == "我通过了你的朋友验证请求，现在我们可以开始聊天了"

    created = [
        (uids, ev)
        for uids, ev in events.published
        if ev.get("type") == "chat_changed" and ev.get("reason") == "created"
    ]
    assert len(created) == 1
    assert created[0][0] == [alice.user_id]
    assert created[0][1]["chat_id"] == dm.id


async def test_list_friend_requests_heals_stale_pending_when_already_friends():
    """If friendship exists but request row is still pending, inbox must not show it."""
    svc, users, _chats, _blocks, _directory, _events, friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    req = await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    await friends.add_friendship(alice.user_id, bob.user_id)
    # Leave status pending on purpose (simulates lagged / partial write).
    assert req.status == "pending"
    alice_box = await svc.list_friend_requests(user_id=alice.user_id)
    assert alice_box.outgoing == []
    assert (await friends.get_request(req.id)).status == "accepted"


async def test_friend_request_group_members_gate():
    svc, users, chats, _blocks, directory, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    directory.set(bob.user_id, who_can_friend="group_members")
    with pytest.raises(AuthorizationError):
        await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    await chats.create_group(member_ids=(alice.user_id, bob.user_id))
    req = await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    assert req.status == "pending"


async def test_accept_friend_activates_pending_dm():
    svc, users, chats, _blocks, directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    directory.set(bob.user_id, who_can_dm="anyone")
    dm = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    peer = await chats.get_member(dm.id, bob.user_id)
    assert peer.state == "pending"
    req = await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    events.published.clear()
    await svc.accept_friend_request(user_id=bob.user_id, request_id=req.id)
    peer = await chats.get_member(dm.id, bob.user_id)
    assert peer.state == "accepted"
    activated = [
        (uids, ev)
        for uids, ev in events.published
        if ev.get("type") == "chat_changed" and ev.get("reason") == "activated"
    ]
    assert len(activated) == 1
    assert set(activated[0][0]) == {alice.user_id, bob.user_id}
    assert activated[0][1]["chat_id"] == dm.id
    # Same DM reused — no second create; system notice still lands.
    assert await chats.get_dm(alice.user_id, bob.user_id) is dm
    msgs, _total = await chats.list_messages(dm.id)
    assert any(
        m.content_type == "system_card"
        and m.content == "我通过了你的朋友验证请求，现在我们可以开始聊天了"
        for m in msgs
    )


async def test_chat_changed_member_added_on_auto_join():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    group = await chats.create_group(title="内测群", auto_join=True)
    await svc.join_auto_join_chats(user_id=alice.user_id)
    added = [
        (uids, ev)
        for uids, ev in events.published
        if ev.get("type") == "chat_changed" and ev.get("reason") == "member_added"
    ]
    assert len(added) == 1
    assert added[0][0] == [alice.user_id]
    assert added[0][1]["chat_id"] == group.id
    # Idempotent re-join must not re-nudge.
    events.published.clear()
    await svc.join_auto_join_chats(user_id=alice.user_id)
    assert events.published == []


async def test_chat_changed_activated_when_friends_reopen_pending_dm():
    svc, users, chats, _blocks, directory, events, friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    directory.set(bob.user_id, who_can_dm="anyone")
    dm = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    await friends.add_friendship(alice.user_id, bob.user_id)
    events.published.clear()
    await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)
    peer = await chats.get_member(dm.id, bob.user_id)
    assert peer.state == "accepted"
    activated = [
        (uids, ev)
        for uids, ev in events.published
        if ev.get("type") == "chat_changed" and ev.get("reason") == "activated"
    ]
    assert len(activated) == 1
    assert set(activated[0][0]) == {alice.user_id, bob.user_id}


async def test_block_cascades_friendship_and_requests():
    svc, users, _chats, blocks, _directory, events, friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    req = await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    await svc.accept_friend_request(user_id=bob.user_id, request_id=req.id)
    assert await friends.are_friends(alice.user_id, bob.user_id)
    # New pending from carol path not needed — create another pending via carol?
    # Re-request after remove: send from alice after unfriend via a third?
    # Just block: should drop friendship. Also cancel a fresh pending.
    await friends.remove_friendship(alice.user_id, bob.user_id)
    pending = await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    await svc.block_user(user_id=bob.user_id, target_id=alice.user_id)
    assert not await friends.are_friends(alice.user_id, bob.user_id)
    assert await blocks.is_blocked_between(alice.user_id, bob.user_id)
    assert (await friends.get_request(pending.id)).status == "cancelled"
    assert any(
        e[1].get("type") == "friend_request" and e[1].get("action") == "cancelled"
        for e in events.published
    )


async def test_profile_relations():
    svc, users, *_rest = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    self_p = await svc.get_profile(viewer_id=alice.user_id, target_id=alice.user_id)
    assert self_p.relation == "self"
    none_p = await svc.get_profile(viewer_id=alice.user_id, target_id=bob.user_id)
    assert none_p.relation == "none"
    req = await svc.send_friend_request(from_user_id=alice.user_id, to_user_id=bob.user_id)
    out_p = await svc.get_profile(viewer_id=alice.user_id, target_id=bob.user_id)
    assert out_p.relation == "outgoing_request"
    assert out_p.request_id == req.id
    in_p = await svc.get_profile(viewer_id=bob.user_id, target_id=alice.user_id)
    assert in_p.relation == "incoming_request"


# --- auto-join (内测全员群) + group members ---


async def test_join_auto_join_chats_enrolls_pinned():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(title="内测群", auto_join=True)
    await svc.join_auto_join_chats(user_id=alice.user_id)
    member = await chats.get_member(group.id, alice.user_id)
    assert member is not None
    assert member.state == "accepted"
    assert member.pinned is True
    # The group now shows up in the user's chat list.
    views = await svc.list_chats(user_id=alice.user_id)
    assert [v.chat.id for v in views] == [group.id]


async def test_join_auto_join_is_idempotent():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(auto_join=True)
    await svc.join_auto_join_chats(user_id=alice.user_id)
    await svc.join_auto_join_chats(user_id=alice.user_id)
    members = [m for m in await chats.list_members(group.id) if m.user_id == alice.user_id]
    assert len(members) == 1


async def test_join_auto_join_skips_non_auto_chats():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    regular = await chats.create_group(auto_join=False)
    await svc.join_auto_join_chats(user_id=alice.user_id)
    assert await chats.get_member(regular.id, alice.user_id) is None


async def test_list_members_returns_participants():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id, bob.user_id])
    members = await svc.list_members(chat_id=group.id, user_id=alice.user_id)
    assert {m.user.user_id for m in members} == {alice.user_id, bob.user_id}


async def test_list_members_non_member_404():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    stranger = users.add("stranger")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id])
    with pytest.raises(NotFoundError):
        await svc.list_members(chat_id=group.id, user_id=stranger.user_id)


# --- leave_chat / set_chat_flags (群自助管理) ---


async def test_leave_chat_removes_member():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id, bob.user_id])
    await svc.leave_chat(chat_id=group.id, user_id=alice.user_id)
    assert await chats.get_member(group.id, alice.user_id) is None
    # bob is untouched; the group lives on.
    assert await chats.get_member(group.id, bob.user_id) is not None
    # alice no longer sees it in her list.
    assert await svc.list_chats(user_id=alice.user_id) == []


async def test_leave_chat_non_member_404():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    stranger = users.add("stranger")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id])
    with pytest.raises(NotFoundError):
        await svc.leave_chat(chat_id=group.id, user_id=stranger.user_id)


async def test_leave_chat_dm_rejected():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(ValidationError):
        await svc.leave_chat(chat_id=chat.id, user_id=alice.user_id)


async def test_leave_chat_official_rejected():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    official = await chats.create_official(member_ids=[alice.user_id])
    with pytest.raises(ValidationError, match="官方号"):
        await svc.leave_chat(chat_id=official.id, user_id=alice.user_id)
    assert await chats.get_member(official.id, alice.user_id) is not None


async def test_send_message_official_rejected():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    official = await chats.create_official(member_ids=[alice.user_id])
    with pytest.raises(ValidationError, match="官方号"):
        await svc.send_message(
            chat_id=official.id, sender_id=alice.user_id, content="hello"
        )


async def test_ensure_official_membership_enrolls_pinned():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    official = await chats.create_official(member_ids=())
    await svc.ensure_official_membership(user_id=alice.user_id)
    member = await chats.get_member(official.id, alice.user_id)
    assert member is not None
    assert member.pinned is True
    # Idempotent — second call does not reset flags.
    await chats.set_membership_flags(official.id, alice.user_id, pinned=False)
    await svc.ensure_official_membership(user_id=alice.user_id)
    member = await chats.get_member(official.id, alice.user_id)
    assert member.pinned is False


async def test_list_chats_ensures_official_membership():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    official = await chats.create_official(member_ids=())
    views = await svc.list_chats(user_id=alice.user_id)
    assert any(v.chat.id == official.id for v in views)
    assert await chats.get_member(official.id, alice.user_id) is not None


async def test_ensure_official_does_not_rejoin_beta_group():
    """Login/list兜底 only touches official — leaving the 内测群 still sticks."""
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id])
    await chats.create_official(member_ids=())
    await svc.leave_chat(chat_id=group.id, user_id=alice.user_id)
    await svc.ensure_official_membership(user_id=alice.user_id)
    assert await chats.get_member(group.id, alice.user_id) is None


async def test_publish_product_notice_inbox_posts_system_card():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    official = await chats.create_official(member_ids=[alice.user_id, bob.user_id])
    msg = await svc.publish_product_notice(
        notice_id="notice-1",
        title="维护公告",
        body="今晚 22:00 维护",
        severity="high",
        surface="inbox",
        cta_label="详情",
        cta_url="https://example.com",
    )
    assert msg is not None
    assert msg.chat_id == official.id
    assert msg.sender_type == "official"
    assert msg.content_type == "system_card"
    assert msg.content == "维护公告\n今晚 22:00 维护"
    assert msg.payload == {
        "kind": "product_notice",
        "notice_id": "notice-1",
        "severity": "high",
        "card_template": "service",
        "cta_label": "详情",
        "cta_url": "https://example.com",
    }
    # One shared message, fanned out to every member (not N copies).
    assert len(chats._messages) == 1
    assert set(events.published[-1][0]) == {alice.user_id, bob.user_id}


async def test_publish_product_notice_banner_skips_im():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    await chats.create_official(member_ids=[alice.user_id])
    msg = await svc.publish_product_notice(
        notice_id="notice-2",
        title="横幅",
        body="只上 Banner",
        severity="normal",
        surface="banner",
    )
    assert msg is None
    assert chats._messages == []


async def test_publish_product_notice_both_posts_once():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    await chats.create_official(member_ids=[alice.user_id])
    msg = await svc.publish_product_notice(
        notice_id="notice-3",
        title="双面",
        body="Banner + Inbox",
        severity="normal",
        surface="both",
    )
    assert msg is not None
    assert len(chats._messages) == 1


async def test_publish_product_notice_modal_posts_system_card():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    await chats.create_official(member_ids=[alice.user_id])
    msg = await svc.publish_product_notice(
        notice_id="notice-4",
        title="弹窗公告",
        body="登录后弹一次",
        severity="normal",
        surface="modal",
    )
    assert msg is not None
    assert msg.payload["kind"] == "product_notice"
    assert msg.payload["notice_id"] == "notice-4"
    assert msg.payload["card_template"] == "service"
    assert len(chats._messages) == 1


async def test_publish_product_notice_article_payload_fields():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    await chats.create_official(member_ids=[alice.user_id])
    msg = await svc.publish_product_notice(
        notice_id="notice-article",
        title="图文标题",
        body="完整正文",
        severity="normal",
        surface="inbox",
        card_template="article",
        summary="卡面摘要",
        cover_url="https://cdn.example.com/cover.jpg",
    )
    assert msg is not None
    assert msg.content == "图文标题\n完整正文"
    assert msg.payload == {
        "kind": "product_notice",
        "notice_id": "notice-article",
        "severity": "normal",
        "card_template": "article",
        "summary": "卡面摘要",
        "cover_url": "https://cdn.example.com/cover.jpg",
    }


async def test_publish_product_notice_service_omits_empty_optional_fields():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    await chats.create_official(member_ids=[alice.user_id])
    msg = await svc.publish_product_notice(
        notice_id="notice-service",
        title="服务卡",
        body="短告知",
        severity="normal",
        surface="inbox",
        card_template="service",
        summary=None,
        cover_url=None,
    )
    assert msg is not None
    assert msg.payload == {
        "kind": "product_notice",
        "notice_id": "notice-service",
        "severity": "normal",
        "card_template": "service",
    }


async def test_set_chat_flags_updates_and_returns_view():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id])
    view = await svc.set_chat_flags(
        chat_id=group.id, user_id=alice.user_id, muted=True, pinned=True
    )
    assert view.member.muted is True
    assert view.member.pinned is True
    assert view.chat.id == group.id
    # group view resolves no dm peer
    assert view.peer is None


async def test_set_chat_flags_partial_preserves_other():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id])
    await svc.set_chat_flags(chat_id=group.id, user_id=alice.user_id, pinned=True)
    view = await svc.set_chat_flags(chat_id=group.id, user_id=alice.user_id, muted=True)
    assert view.member.pinned is True  # untouched by the second patch
    assert view.member.muted is True


async def test_set_chat_flags_non_member_404():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    stranger = users.add("stranger")
    group = await chats.create_group(auto_join=True, member_ids=[alice.user_id])
    with pytest.raises(NotFoundError):
        await svc.set_chat_flags(chat_id=group.id, user_id=stranger.user_id, muted=True)


# --- moderation: kick / mute / announce (Stage 3 审核治理) ---


async def test_kick_member_removes_and_posts_system_card():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    await svc.kick_member(chat_id=group.id, actor_id=admin.user_id, target_id=alice.user_id)
    assert await chats.get_member(group.id, alice.user_id) is None
    # the admin remains; the group lives on
    assert await chats.get_member(group.id, admin.user_id) is not None
    # a system_card notice (NULL sender) was fanned out to the remaining members
    recipients, event = events.published[-1]
    assert recipients == [admin.user_id]
    assert event["message"]["content_type"] == "system_card"
    assert event["message"]["sender_user_id"] is None
    assert "alice" in event["message"]["content"]


async def test_kick_admin_target_forbidden():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    other_admin = users.add("root", role="admin")
    group = await chats.create_group(member_ids=[admin.user_id, other_admin.user_id])
    with pytest.raises(AuthorizationError):
        await svc.kick_member(
            chat_id=group.id, actor_id=admin.user_id, target_id=other_admin.user_id
        )
    # the admin target is untouched
    assert await chats.get_member(group.id, other_admin.user_id) is not None


async def test_kick_non_member_404():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    stranger = users.add("stranger")
    group = await chats.create_group(member_ids=[admin.user_id])
    with pytest.raises(NotFoundError):
        await svc.kick_member(chat_id=group.id, actor_id=admin.user_id, target_id=stranger.user_id)


async def test_kick_dm_rejected():
    svc, users, *_ = _make()
    admin = users.add("admin", role="admin")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=admin.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(ValidationError):
        await svc.kick_member(chat_id=chat.id, actor_id=admin.user_id, target_id=bob.user_id)


async def test_admin_mute_blocks_send_then_unmute_restores():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    await svc.set_admin_mute(
        chat_id=group.id, actor_id=admin.user_id, target_id=alice.user_id, muted=True
    )
    with pytest.raises(AuthorizationError):
        await svc.send_message(chat_id=group.id, sender_id=alice.user_id, content="hi")
    # unmuting restores the ability to send
    await svc.set_admin_mute(
        chat_id=group.id, actor_id=admin.user_id, target_id=alice.user_id, muted=False
    )
    msg = await svc.send_message(chat_id=group.id, sender_id=alice.user_id, content="hi again")
    assert msg.content == "hi again"


async def test_admin_mute_reflected_in_roster():
    svc, users, chats, *_ = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    await svc.set_admin_mute(
        chat_id=group.id, actor_id=admin.user_id, target_id=alice.user_id, muted=True
    )
    members = await svc.list_members(chat_id=group.id, user_id=admin.user_id)
    by_id = {m.user.user_id: m for m in members}
    assert by_id[admin.user_id].is_admin is True
    assert by_id[alice.user_id].is_admin is False
    assert by_id[alice.user_id].muted_by_admin is True
    assert by_id[admin.user_id].muted_by_admin is False


async def test_announce_posts_system_card_to_all_members():
    svc, users, chats, _blocks, _directory, events, _friends = _make()
    admin = users.add("admin", role="admin")
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[admin.user_id, alice.user_id])
    msg = await svc.post_announcement(chat_id=group.id, actor_id=admin.user_id, content="维护通知")
    assert msg.content_type == "system_card"
    assert msg.sender_user_id is None
    recipients, event = events.published[-1]
    assert set(recipients) == {admin.user_id, alice.user_id}
    assert event["message"]["content"] == "维护通知"


async def test_announce_dm_rejected():
    svc, users, *_ = _make()
    admin = users.add("admin", role="admin")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=admin.user_id, peer_id=bob.user_id)).chat
    with pytest.raises(ValidationError):
        await svc.post_announcement(chat_id=chat.id, actor_id=admin.user_id, content="hi")


async def test_group_moderator_can_kick_and_everyone():
    from agentcore.db.repositories.chat import BETA_GROUP_ID, BETA_GROUP_TITLE

    svc, users, chats, _blocks, _directory, events, _friends = _make()
    mod = users.add("mod")
    alice = users.add("alice")
    group = await chats.create_group(
        title=BETA_GROUP_TITLE,
        member_ids=[mod.user_id, alice.user_id],
        chat_id=BETA_GROUP_ID,
    )
    await chats.set_member_role(group.id, mod.user_id, role="admin")

    await svc.kick_member(chat_id=group.id, actor_id=mod.user_id, target_id=alice.user_id)
    assert await chats.get_member(group.id, alice.user_id) is None

    # re-add alice to exercise @所有人
    await chats.add_member(group.id, alice.user_id)
    msg = await svc.send_message(
        chat_id=group.id,
        sender_id=mod.user_id,
        content="@所有人 hi",
        mentions=[{"kind": "everyone"}],
    )
    assert msg.mentions == [{"kind": "everyone"}]


async def test_plain_member_cannot_kick():
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    group = await chats.create_group(member_ids=[alice.user_id, bob.user_id])
    with pytest.raises(AuthorizationError, match="无权执行该操作"):
        await svc.kick_member(chat_id=group.id, actor_id=alice.user_id, target_id=bob.user_id)


async def test_group_moderator_cannot_kick_other_moderator():
    svc, users, chats, *_ = _make()
    mod_a = users.add("moda")
    mod_b = users.add("modb")
    group = await chats.create_group(member_ids=[mod_a.user_id, mod_b.user_id])
    await chats.set_member_role(group.id, mod_a.user_id, role="admin")
    await chats.set_member_role(group.id, mod_b.user_id, role="admin")
    with pytest.raises(AuthorizationError, match="不能对群管理员"):
        await svc.kick_member(chat_id=group.id, actor_id=mod_a.user_id, target_id=mod_b.user_id)


async def test_beta_group_moderator_appoint_and_revoke():
    from agentcore.db.repositories.chat import BETA_GROUP_ID, BETA_GROUP_TITLE

    svc, users, chats, *_ = _make()
    root = users.add("root", role="admin")
    alice = users.add("alice")
    await chats.create_group(
        title=BETA_GROUP_TITLE, member_ids=[alice.user_id], chat_id=BETA_GROUP_ID
    )
    await svc.set_beta_group_moderator(user_id=alice.user_id, actor_id=root.user_id)
    chat_id, title, mods = await svc.list_beta_group_moderators()
    assert chat_id == BETA_GROUP_ID
    assert title == BETA_GROUP_TITLE
    assert [m.user_id for m in mods] == [alice.user_id]
    members = await svc.list_members(chat_id=BETA_GROUP_ID, user_id=alice.user_id)
    assert members[0].group_role == "admin"
    assert members[0].is_admin is False

    await svc.clear_beta_group_moderator(user_id=alice.user_id, actor_id=root.user_id)
    _cid, _title, mods2 = await svc.list_beta_group_moderators()
    assert mods2 == []
    members2 = await svc.list_members(chat_id=BETA_GROUP_ID, user_id=alice.user_id)
    assert members2[0].group_role == "member"


# --- attachments: upload / download (Stage 4 富消息) ---
# These touch the real filesystem (build_chat_workspace), so data_dir is redirected
# to tmp_path; the repos stay in-memory fakes for the membership gate.


def _png_bytes(width: int, height: int) -> bytes:
    """A real PNG of the given size, for the thumbnail/upload tests."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 30, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_upload_attachment_roundtrips(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[alice.user_id])
    result = await svc.upload_attachment(
        chat_id=group.id,
        user_id=alice.user_id,
        path="attachments/x/pic.png",
        data=b"\x89PNG\r\n",
    )
    assert result.size_bytes == 6
    # non-image bytes → no thumbnail (the original is served inline)
    assert result.thumb_path is None
    # the bytes land under the chat's own im/<chat_id> space
    stored = tmp_path / "workspaces" / "im" / group.id / "attachments" / "x" / "pic.png"
    assert stored.read_bytes() == b"\x89PNG\r\n"
    # and a member can read them back byte-for-byte
    got = await svc.download_attachment(
        chat_id=group.id, user_id=alice.user_id, path="attachments/x/pic.png"
    )
    assert got == b"\x89PNG\r\n"


async def test_upload_image_generates_bounded_webp_thumbnail(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[alice.user_id])
    data = _png_bytes(1000, 800)
    result = await svc.upload_attachment(
        chat_id=group.id, user_id=alice.user_id, path="attachments/x/photo.png", data=data
    )
    # a sibling thumbnail path is returned and a member can fetch it
    assert result.thumb_path == "attachments/x/photo.png.thumb.webp"
    thumb = await svc.download_attachment(
        chat_id=group.id, user_id=alice.user_id, path=result.thumb_path
    )
    import io

    from PIL import Image

    with Image.open(io.BytesIO(thumb)) as img:
        assert img.format == "WEBP"
        assert max(img.size) <= 512  # bounded to the longest-edge cap


async def test_upload_small_image_skips_thumbnail(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[alice.user_id])
    result = await svc.upload_attachment(
        chat_id=group.id,
        user_id=alice.user_id,
        path="attachments/y/small.png",
        data=_png_bytes(100, 80),
    )
    # already within the cap → no thumbnail (it would save nothing)
    assert result.thumb_path is None


async def test_upload_attachment_non_member_404(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    stranger = users.add("stranger")
    group = await chats.create_group(member_ids=[alice.user_id])
    with pytest.raises(NotFoundError):
        await svc.upload_attachment(
            chat_id=group.id, user_id=stranger.user_id, path="attachments/a", data=b"x"
        )


async def test_download_attachment_non_member_404(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    stranger = users.add("stranger")
    group = await chats.create_group(member_ids=[alice.user_id])
    await svc.upload_attachment(
        chat_id=group.id, user_id=alice.user_id, path="attachments/a", data=b"x"
    )
    with pytest.raises(NotFoundError):
        await svc.download_attachment(
            chat_id=group.id, user_id=stranger.user_id, path="attachments/a"
        )


async def test_download_attachment_missing_404(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[alice.user_id])
    with pytest.raises(NotFoundError):
        await svc.download_attachment(
            chat_id=group.id, user_id=alice.user_id, path="attachments/missing.png"
        )


async def test_upload_attachment_traversal_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, chats, *_ = _make()
    alice = users.add("alice")
    group = await chats.create_group(member_ids=[alice.user_id])
    with pytest.raises(ValidationError):
        await svc.upload_attachment(
            chat_id=group.id,
            user_id=alice.user_id,
            path="../../escape.txt",
            data=b"x",
        )


async def test_send_message_attachments_only_no_content(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    svc, users, _chats, _blocks, _directory, events, _friends = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    chat = (await svc.start_dm(requester_id=alice.user_id, peer_id=bob.user_id)).chat
    att = [{"name": "pic.png", "path": "pic.png", "workspace_path": "attachments/x/pic.png"}]
    msg = await svc.send_message(
        chat_id=chat.id,
        sender_id=alice.user_id,
        content=None,
        content_type="image",
        attachments=att,
    )
    assert msg.content is None
    assert msg.content_type == "image"
    assert msg.attachments == att
    # the realtime fan-out carries the attachments + content_type
    _recipients, event = events.published[-1]
    assert event["message"]["content_type"] == "image"
    assert event["message"]["attachments"] == att


async def test_list_co_member_ids_excludes_self_and_strangers():
    """Presence audience = distinct co-chat users (dm + group), never self."""
    _svc, users, chats, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    carol = users.add("carol")
    stranger = users.add("stranger")
    await chats.create_dm(creator_id=alice.user_id, peer_id=bob.user_id)
    await chats.create_group(member_ids=[alice.user_id, carol.user_id])
    ids = await chats.list_co_member_ids(alice.user_id)
    assert set(ids) == {bob.user_id, carol.user_id}
    assert stranger.user_id not in ids
    assert alice.user_id not in ids


async def test_search_by_exact_user_id():
    svc, users, *_ = _make()
    alice = users.add("alice")
    bob = users.add("bob")
    hits = await svc.search_users(requester_id=bob.user_id, query=alice.user_id)
    assert len(hits) == 1
    assert hits[0].user_id == alice.user_id
