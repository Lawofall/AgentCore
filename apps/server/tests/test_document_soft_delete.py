"""Unit tests for DocumentRepository.soft_delete batch UPDATE semantics."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.sql.dml import Update

from agentcore.core.types import new_id
from agentcore.db.models import Document
from agentcore.db.repositories.documents import DocumentRepository


def _doc(
    *,
    user_id: str,
    name: str,
    parent_id: str | None = None,
    kind: str = "document",
    deleted_at: datetime | None = None,
) -> Document:
    return Document(
        id=new_id(),
        user_id=user_id,
        parent_id=parent_id,
        folder_id=None,
        kind=kind,
        role="general",
        ai_maintained=False,
        name=name,
        content="",
        deleted_at=deleted_at,
    )


@pytest.mark.asyncio
async def test_soft_delete_batch_marks_subtree_and_is_idempotent(monkeypatch):
    """One batch UPDATE soft-deletes the live subtree; same-session rows see it; re-delete is no-op."""
    uid = new_id()
    root = _doc(user_id=uid, name="root", kind="folder")
    child = _doc(user_id=uid, name="child", parent_id=root.id, kind="folder")
    leaf = _doc(user_id=uid, name="leaf.md", parent_id=child.id)
    # Already soft-deleted grandchild must stay at its prior timestamp (idempotent WHERE).
    prior = datetime(2020, 1, 1)
    tombstone = _doc(
        user_id=uid, name="gone.md", parent_id=child.id, deleted_at=prior
    )
    by_id = {d.id: d for d in (root, child, leaf, tombstone)}

    session = AsyncMock()
    session.get = AsyncMock()
    updates: list[Update] = []

    async def fake_execute(stmt):
        assert isinstance(stmt, Update), "soft_delete must use one batch UPDATE"
        updates.append(stmt)
        # Mirror synchronize_session for live ids in the subtree (root + descendants).
        target_ids = {root.id, child.id, leaf.id, tombstone.id}
        for doc in by_id.values():
            if doc.id in target_ids and doc.deleted_at is None:
                # Values bind is a datetime (or BindParameter wrapping it).
                raw = stmt._values[Document.__table__.c.deleted_at]
                doc.deleted_at = getattr(raw, "value", raw)
        return AsyncMock()

    session.execute = fake_execute
    session.commit = AsyncMock()

    repo = DocumentRepository(session)
    monkeypatch.setattr(repo, "get", AsyncMock(side_effect=[root, None]))
    monkeypatch.setattr(
        repo, "_descendant_ids", AsyncMock(return_value=[child.id, leaf.id, tombstone.id])
    )

    assert await repo.soft_delete(root.id, user_id=uid) is True
    assert len(updates) == 1
    session.get.assert_not_called()
    session.commit.assert_awaited_once()

    assert root.deleted_at is not None
    assert child.deleted_at is not None
    assert leaf.deleted_at is not None
    assert root.deleted_at == child.deleted_at == leaf.deleted_at
    # Same-session visibility: loaded ORM rows already carry deleted_at.
    assert by_id[root.id].deleted_at is not None
    assert by_id[leaf.id].deleted_at is not None
    # Idempotent: pre-deleted row is not rewritten.
    assert tombstone.deleted_at == prior

    assert await repo.soft_delete(root.id, user_id=uid) is False
    assert len(updates) == 1  # second call short-circuits on get → None


@pytest.mark.asyncio
async def test_hard_delete_for_folders_issues_one_delete_and_respects_commit():
    from sqlalchemy.sql.dml import Delete

    session = AsyncMock()
    deletes: list[Delete] = []

    async def fake_execute(stmt):
        assert isinstance(stmt, Delete), "folder purge must physically DELETE"
        deletes.append(stmt)
        result = AsyncMock()
        result.rowcount = 3
        return result

    session.execute = fake_execute
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    repo = DocumentRepository(session)

    assert await repo.hard_delete_for_folders("u1", []) == 0
    assert await repo.hard_delete_for_folders("u1", [""]) == 0
    assert deletes == []
    session.commit.assert_not_called()

    assert await repo.hard_delete_for_folders("u1", ["f1", "", "f2"]) == 3
    assert len(deletes) == 1
    session.commit.assert_awaited_once()

    session.commit.reset_mock()
    session.flush.reset_mock()
    assert await repo.hard_delete_for_folders("u1", ["f1"], commit=False) == 3
    session.flush.assert_awaited_once()
    session.commit.assert_not_called()
