"""Unit tests for FolderDeskService invite lifecycle and dual-identity helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.core.rate_limit import FixedWindowRateLimiter
from agentcore.core.types import new_id
from agentcore.folders.desk import (
    DeskAccess,
    billing_actor_user_id,
    desk_workspace_user_id,
)
from agentcore.folders.service import FolderDeskService

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FakeUsers:
    def __init__(self) -> None:
        self._by_id: dict = {}

    def add(self, username, *, status="active"):
        user = SimpleNamespace(
            user_id=new_id(),
            username=username,
            display_name=username,
            status=status,
            role="user",
        )
        self._by_id[user.user_id] = user
        return user

    async def get_by_id(self, user_id):
        return self._by_id.get(user_id)

    async def get_by_ids(self, user_ids):
        return {uid: self._by_id[uid] for uid in user_ids if uid in self._by_id}


class FakeBlocks:
    def __init__(self) -> None:
        self._pairs: set[tuple[str, str]] = set()

    async def block(self, a, b):
        self._pairs.add((a, b))

    async def is_blocked_between(self, a, b):
        return (a, b) in self._pairs or (b, a) in self._pairs


class FakeDirectory:
    def __init__(self) -> None:
        self._rows: dict = {}

    async def get(self, user_id):
        return self._rows.get(user_id)


class FakeEvents:
    def __init__(self) -> None:
        self.published: list[tuple[list, dict]] = []

    async def publish(self, user_ids, event):
        self.published.append((list(user_ids), event))


class FakeFolders:
    def __init__(self) -> None:
        self._folders: dict = {}
        self._session = object()

    def add(self, *, user_id, name="桌", local_root_id=None, rel_path=None):
        folder = SimpleNamespace(
            id=new_id(),
            name=name,
            user_id=user_id,
            rel_path=rel_path if rel_path is not None else (None if local_root_id else name),
            local_root_id=local_root_id,
            local_subpath=None,
            created_at=_EPOCH,
            updated_at=_EPOCH,
            deleted_at=None,
        )
        self._folders[folder.id] = folder
        return folder

    async def get_by_id_unscoped(self, folder_id):
        folder = self._folders.get(folder_id)
        if folder is None or folder.deleted_at is not None:
            return None
        return folder

    async def list_owned_ids(self, user_id, include_deleted=False):
        return [
            f.id
            for f in self._folders.values()
            if f.user_id == user_id and (include_deleted or f.deleted_at is None)
        ]


class FakeMembers:
    def __init__(self) -> None:
        self._rows: list = []
        self._seq = 0
        self._folders: FakeFolders | None = None

    def _now(self):
        self._seq += 1
        return _EPOCH + timedelta(seconds=self._seq)

    async def get_member(self, folder_id, user_id):
        return next(
            (
                m
                for m in self._rows
                if m.folder_id == folder_id and m.user_id == user_id
            ),
            None,
        )

    async def list_members(self, folder_id):
        return [m for m in self._rows if m.folder_id == folder_id]

    async def count_members(self, folder_id):
        return len([m for m in self._rows if m.folder_id == folder_id])

    async def add_member(self, *, folder_id, user_id, role, state, invited_by):
        member = SimpleNamespace(
            folder_id=folder_id,
            user_id=user_id,
            role=role,
            state=state,
            invited_by=invited_by,
            joined_at=self._now(),
        )
        self._rows.append(member)
        return member

    async def set_member_state(self, folder_id, user_id, *, state):
        member = await self.get_member(folder_id, user_id)
        if member is not None:
            member.state = state

    async def set_member_role(self, folder_id, user_id, *, role):
        member = await self.get_member(folder_id, user_id)
        if member is not None:
            member.role = role

    async def remove_member(self, folder_id, user_id):
        self._rows = [
            m
            for m in self._rows
            if not (m.folder_id == folder_id and m.user_id == user_id)
        ]

    async def list_for_user(self, user_id, *, state="accepted"):
        out = []
        catalog = self._folders._folders if self._folders is not None else {}
        for m in self._rows:
            if m.user_id == user_id and m.state == state:
                folder = catalog.get(m.folder_id)
                if folder is not None:
                    out.append((folder, m))
        return out

    async def list_pending_for_user(self, user_id):
        return await self.list_for_user(user_id, state="pending")

    async def delete_pending_between(self, user_a, user_b):
        before = len(self._rows)
        self._rows = [
            m
            for m in self._rows
            if not (
                m.state == "pending"
                and (
                    (m.user_id == user_a and m.invited_by == user_b)
                    or (m.user_id == user_b and m.invited_by == user_a)
                )
            )
        ]
        return before - len(self._rows)

    async def delete_all_memberships_for_user(self, user_id):
        ids = [m.folder_id for m in self._rows if m.user_id == user_id]
        self._rows = [m for m in self._rows if m.user_id != user_id]
        return ids

    async def delete_memberships_for_folders(self, folder_ids):
        ids = set(folder_ids)
        before = len(self._rows)
        self._rows = [m for m in self._rows if m.folder_id not in ids]
        return before - len(self._rows)


def _patch_access(monkeypatch, folders: FakeFolders, members: FakeMembers):
    async def _resolve(session, *, folder_id, user_id):
        folder = await folders.get_by_id_unscoped(folder_id)
        if folder is None:
            return None
        if folder.user_id == user_id:
            return DeskAccess(folder=folder, role="owner")
        member = await members.get_member(folder_id, user_id)
        if member is None or member.state != "accepted":
            return None
        return DeskAccess(folder=folder, role=member.role)

    monkeypatch.setattr("agentcore.folders.service.resolve_desk_access", _resolve)


def _svc(monkeypatch, **kwargs):
    users = kwargs.pop("users", FakeUsers())
    folders = kwargs.pop("folders", FakeFolders())
    members = kwargs.pop("members", FakeMembers())
    members._folders = folders
    blocks = kwargs.pop("blocks", FakeBlocks())
    directory = kwargs.pop("directory", FakeDirectory())
    events = kwargs.pop("events", FakeEvents())
    _patch_access(monkeypatch, folders, members)
    svc = FolderDeskService(
        folders=folders,
        members=members,
        users=users,
        blocks=blocks,
        directory=directory,
        events=events,
        invite_limiter=FixedWindowRateLimiter(max_requests=100, window_seconds=3600),
        **kwargs,
    )
    return svc, users, folders, members, blocks, directory, events


@pytest.mark.asyncio
async def test_invite_accept_lifecycle(monkeypatch):
    svc, users, folders, members, *_ = _svc(monkeypatch)
    owner = users.add("owner")
    peer = users.add("peer")
    folder = folders.add(user_id=owner.user_id, name="协作桌")

    invited = await svc.invite(
        folder_id=folder.id,
        actor_id=owner.user_id,
        target_user_id=peer.user_id,
        role="editor",
    )
    assert invited.state == "pending"
    assert invited.role == "editor"

    pending = await svc.list_pending_invites(user_id=peer.user_id)
    assert len(pending) == 1
    assert pending[0].id == folder.id

    with pytest.raises(NotFoundError):
        await svc.get_desk(folder_id=folder.id, user_id=peer.user_id)

    accepted = await svc.accept_invite(folder_id=folder.id, user_id=peer.user_id)
    assert accepted.my_role == "editor"
    assert accepted.my_state == "accepted"

    roster = await svc.list_members(folder_id=folder.id, user_id=owner.user_id)
    assert [m.role for m in roster] == ["owner", "editor"]


@pytest.mark.asyncio
async def test_local_folder_refuses_invite(monkeypatch):
    svc, users, folders, *_ = _svc(monkeypatch)
    owner = users.add("owner")
    peer = users.add("peer")
    folder = folders.add(
        user_id=owner.user_id,
        name="本机",
        local_root_id="11111111-2222-3333-4444-555555555555",
        rel_path="本机",
    )
    with pytest.raises(ValidationError, match="本机"):
        await svc.invite(
            folder_id=folder.id,
            actor_id=owner.user_id,
            target_user_id=peer.user_id,
            role="editor",
        )


@pytest.mark.asyncio
async def test_block_blocks_new_invite_not_existing_member(monkeypatch):
    svc, users, folders, members, blocks, *_ = _svc(monkeypatch)
    owner = users.add("owner")
    peer = users.add("peer")
    other = users.add("other")
    folder = folders.add(user_id=owner.user_id)

    await svc.invite(
        folder_id=folder.id,
        actor_id=owner.user_id,
        target_user_id=peer.user_id,
        role="editor",
    )
    await svc.accept_invite(folder_id=folder.id, user_id=peer.user_id)

    await svc.invite(
        folder_id=folder.id,
        actor_id=owner.user_id,
        target_user_id=other.user_id,
        role="viewer",
    )
    await blocks.block(owner.user_id, other.user_id)
    n = await svc.on_users_blocked(owner.user_id, other.user_id)
    assert n == 1
    assert await members.get_member(folder.id, other.user_id) is None

    await blocks.block(owner.user_id, peer.user_id)
    with pytest.raises(ValidationError, match="无法邀请"):
        await svc.invite(
            folder_id=folder.id,
            actor_id=owner.user_id,
            target_user_id=peer.user_id,
            role="viewer",
        )
    still = await svc.get_desk(folder_id=folder.id, user_id=peer.user_id)
    assert still.my_role == "editor"


@pytest.mark.asyncio
async def test_non_member_404(monkeypatch):
    svc, users, folders, *_ = _svc(monkeypatch)
    owner = users.add("owner")
    stranger = users.add("stranger")
    folder = folders.add(user_id=owner.user_id)
    with pytest.raises(NotFoundError):
        await svc.get_desk(folder_id=folder.id, user_id=stranger.user_id)
    with pytest.raises(NotFoundError):
        await svc.list_members(folder_id=folder.id, user_id=stranger.user_id)


def test_dual_identity_helpers():
    assert (
        desk_workspace_user_id(folder_owner_user_id="owner", caller_user_id="editor")
        == "owner"
    )
    assert billing_actor_user_id(caller_user_id="editor") == "editor"
    folder = SimpleNamespace(user_id="owner", id="f")
    assert DeskAccess(folder=folder, role="viewer").can_write is False
    assert DeskAccess(folder=folder, role="editor").can_write is True
    assert DeskAccess(folder=folder, role="editor").is_member_actor is True
    assert DeskAccess(folder=folder, role="owner").is_member_actor is False


@pytest.mark.asyncio
async def test_cleanup_drops_memberships(monkeypatch):
    svc, users, folders, members, *_ = _svc(monkeypatch)
    owner = users.add("owner")
    peer = users.add("peer")
    folder = folders.add(user_id=owner.user_id)
    await members.add_member(
        folder_id=folder.id,
        user_id=peer.user_id,
        role="editor",
        state="accepted",
        invited_by=owner.user_id,
    )
    await svc.cleanup_for_deleted_user(owner.user_id)
    assert await members.list_members(folder.id) == []


@pytest.mark.asyncio
async def test_resolve_folder_owner_user_id_skips_non_uuid():
    """Resume e2e 夹具 ``F1`` / ``test_birth`` 不是合法 folder id，禁止绑进 folders.id。"""
    from agentcore.folders.desk import resolve_folder_owner_user_id

    class BoomSession:
        async def execute(self, stmt):
            raise AssertionError("non-UUID folder_id must not hit Postgres")

    session = BoomSession()
    assert await resolve_folder_owner_user_id("F1", session=session) is None
    assert await resolve_folder_owner_user_id("test_birth", session=session) is None
    assert await resolve_folder_owner_user_id(None, session=session) is None
    assert await resolve_folder_owner_user_id("", session=session) is None
