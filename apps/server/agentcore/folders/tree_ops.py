"""云文件夹目录树的结构操作：改名 / 移动 / 软删 / 还原（DB + 盘一起动）。

`FolderRepository` 只管 ``rel_path`` 这张表；盘上的 ``mv`` 在这里，两件事必须一起
成功。顺序是**先写 DB（不提交）→ 再动盘 → 最后提交**：``mv`` 失败就回滚，DB 与盘仍
然一致，不需要事后对账或补偿改名。

锁：走既有的 ``workspace_lock`` 键（id 派生，改名不变），并且用
:func:`workspace_lock_nowait`——搬目录会把正在跑的回合脚下的路抽走，静默排队等锁等于
让用户以为改名失败、几分钟后又莫名生效。整棵子树的锁按键排序依次拿，顺序固定所以不
会和别人交叉死锁。
"""

from __future__ import annotations

import shutil
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.logging import get_logger
from agentcore.db.models import Folder
from agentcore.db.repositories.folders import (
    FOLDER_DELETE_ORIGIN_USER,
    FolderRepository,
    FolderTreeError,
)
from agentcore.workspace.cloud_tree import normalize_rel_path
from agentcore.workspace.locate import (
    folder_tombstone_path,
    workspace_root_path,
    workspace_storage_key,
)
from agentcore.workspace.locks import workspace_lock_nowait

logger = get_logger(__name__)

__all__ = [
    "FolderTreeError",
    "rename_folder",
    "restore_folder_tree",
    "soft_delete_folder_tree",
]


def _tree_path(*, user_id: str, rel_path: str) -> Path:
    return workspace_root_path(
        user_id=user_id, folder_rel_path=rel_path, conversation_id=""
    )


async def _lock_subtree(stack: AsyncExitStack, *, user_id: str, folder_ids: list[str]) -> None:
    """Hold every affected folder's lock, in a fixed order (no deadlock windows)."""
    keys = sorted(
        workspace_storage_key(user_id=user_id, folder_id=fid, conversation_id="")
        for fid in folder_ids
    )
    for key in keys:
        await stack.enter_async_context(workspace_lock_nowait(key))


def _move_directory(src: Path, dest: Path) -> None:
    """Move a workspace directory, creating the destination's parent chain.

    A missing source is fine: the folder simply never materialized on disk (no
    turn ever wrote to it). A destination that already exists is not — that would
    mean two folders claim one directory, which the sibling-uniqueness rule is
    supposed to have prevented.
    """
    if not src.exists():
        return
    if dest.exists():
        raise FolderTreeError(f"目标目录已存在：{dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


async def _apply_rewrite(
    session: AsyncSession,
    *,
    user_id: str,
    folder_id: str,
    new_name: str | None,
    new_parent_rel_path: str | None,
    move: bool,
) -> Folder | None:
    repo = FolderRepository(session)
    subtree_ids = await repo.list_live_subtree_ids(folder_id, user_id=user_id)
    if not subtree_ids:
        return None
    async with AsyncExitStack() as stack:
        await _lock_subtree(stack, user_id=user_id, folder_ids=subtree_ids)
        rewrite = await repo.replace_subtree_rel_path(
            folder_id,
            user_id=user_id,
            new_name=new_name,
            new_parent_rel_path=new_parent_rel_path,
            move=move,
            commit=False,
        )
        if rewrite is None:
            await session.rollback()
            return None
        try:
            if rewrite.moved:
                _move_directory(
                    _tree_path(user_id=user_id, rel_path=rewrite.old_rel_path),
                    _tree_path(user_id=user_id, rel_path=rewrite.new_rel_path),
                )
        except (OSError, FolderTreeError):
            await session.rollback()
            raise
        await session.commit()
    return await repo.get_by_id(folder_id, user_id=user_id)


async def rename_folder(
    session: AsyncSession, *, user_id: str, folder_id: str, new_name: str
) -> Folder | None:
    """Rename a folder and its directory; descendants follow by path prefix."""
    return await _apply_rewrite(
        session,
        user_id=user_id,
        folder_id=folder_id,
        new_name=new_name,
        new_parent_rel_path=None,
        move=False,
    )


async def soft_delete_folder_tree(
    session: AsyncSession,
    *,
    user_id: str,
    folder_id: str,
    origin: str = FOLDER_DELETE_ORIGIN_USER,
) -> bool:
    """Soft-delete a folder subtree and park its directory in the tombstone area.

    The directory leaves the visible tree immediately so the name is free again the
    moment the user deletes it. Leaving it in place for the retention window would
    make a new folder of the same name land on the old one's files — and then the
    purge sweep would delete the new one.
    """
    repo = FolderRepository(session)
    folder = await repo.get_by_id(folder_id, user_id=user_id)
    if folder is None:
        return False
    rel_path = normalize_rel_path(folder.rel_path)
    subtree_ids = await repo.list_live_subtree_ids(folder_id, user_id=user_id)
    async with AsyncExitStack() as stack:
        await _lock_subtree(stack, user_id=user_id, folder_ids=subtree_ids)
        if not await repo.soft_delete(folder_id, user_id=user_id, origin=origin):
            return False
        if rel_path:
            try:
                _move_directory(
                    _tree_path(user_id=user_id, rel_path=rel_path),
                    folder_tombstone_path(user_id=user_id, folder_id=folder_id),
                )
            except (OSError, FolderTreeError):
                # DB already committed (the archive cascade is not re-runnable);
                # an un-moved directory only wastes the name until purge, so log
                # loudly rather than resurrect a half-deleted folder.
                logger.warning(
                    "folder.tombstone_move_failed",
                    folder_id=folder_id,
                    user_id=user_id,
                    exc_info=True,
                )
    return True


async def restore_folder_tree(
    session: AsyncSession,
    *,
    user_id: str,
    folder_id: str,
    not_before: datetime,
) -> Folder | None:
    """Restore a soft-deleted subtree and move its directory back into the tree.

    The slot may differ from the one it left (a live sibling could have taken the
    name), so the directory follows whatever ``rel_path`` the restore allocated.
    """
    repo = FolderRepository(session)
    key = workspace_storage_key(user_id=user_id, folder_id=folder_id, conversation_id="")
    async with workspace_lock_nowait(key):
        folder = await repo.restore(folder_id, user_id=user_id, not_before=not_before)
        if folder is None or not folder.rel_path:
            return folder
        try:
            _move_directory(
                folder_tombstone_path(user_id=user_id, folder_id=folder_id),
                _tree_path(user_id=user_id, rel_path=folder.rel_path),
            )
        except (OSError, FolderTreeError):
            logger.warning(
                "folder.tombstone_restore_failed",
                folder_id=folder_id,
                user_id=user_id,
                rel_path=folder.rel_path,
                exc_info=True,
            )
    return folder
