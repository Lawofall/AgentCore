"""``folder_id`` → 物理落点（``rel_path``）的唯一解析入口。

路径策略本身是纯函数（:mod:`agentcore.workspace.locate` 只收 ``folder_rel_path``，
所以它的单测不用起数据库）。但深处的调用方——快照、回收区、文件服务、回合 backend
——手上只有一个 ``folder_id``。本模块就是那道翻译：读 ``folders.rel_path`` 这个**单一
真相源**，把两个坐标一起给出去。

不要在别处再写一份 id → path 的查询；那会变成第二个真相源，改名时必然漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.base import async_session_factory
from agentcore.db.models import Folder
from agentcore.workspace.locate import workspace_internal_root, workspace_root_path


@dataclass(frozen=True)
class FolderPlacement:
    """一个文件夹的两个坐标：稳定 id + 当前物理落点。

    ``folder_id`` 回答「是哪个文件夹」——锁键、快照前缀、隐藏 zone、站立任务 / 记忆 /
    白板 / 写权台账的外部引用都挂它，改名移动都不动。``rel_path`` 回答「它现在在哪」
    ——只有它决定盘上目录，改名移动会重写它（连同整棵子树）。

    ``rel_path is None`` = 这个 id 没有云端目录（文件夹已被硬删，或行不存在）；调用
    方应回落到会话 scratch 或直接放弃，**不要**自己拼一个 id 命名的目录。
    """

    folder_id: str | None
    rel_path: str | None

    @property
    def is_cloud_tree(self) -> bool:
        return bool(self.rel_path)


SCRATCH_PLACEMENT = FolderPlacement(folder_id=None, rel_path=None)


async def resolve_folder_placement(
    folder_id: str | None,
    *,
    session: AsyncSession | None = None,
    include_deleted: bool = False,
) -> FolderPlacement:
    """读出 ``folder_id`` 当前的 ``rel_path``（``None`` → 裸聊 scratch）。

    请求路径上**务必**传入该请求自己的 ``session``：另开一个 session 会读到另一个
    连接（集成测试里甚至是另一个 schema），于是查不到这一行、当成裸聊、把文件写进
    错误的目录。只有后台任务（保留期清理等）才用不带 session 的那条路。

    ``include_deleted`` 给保留期清理用——它要处理的正是软删行（目录此刻在墓碑区，
    :func:`agentcore.workspace.locate.folder_tombstone_path`）。
    """
    if not folder_id:
        return SCRATCH_PLACEMENT
    if session is None:
        from agentcore.folders.credentials import (
            FoldersCloudError,
            cloud_get_folder,
            get_folders_credentials,
        )

        creds = get_folders_credentials()
        if creds is not None:
            try:
                summary = await cloud_get_folder(creds, folder_id=folder_id)
            except FoldersCloudError:
                return FolderPlacement(folder_id=folder_id, rel_path=None)
            rel_path = summary.get("rel_path") if summary else None
            return FolderPlacement(
                folder_id=folder_id,
                rel_path=rel_path if isinstance(rel_path, str) and rel_path else None,
            )
        from agentcore.db.sidecar_tickets import sidecar_narrow_tickets_bound

        if sidecar_narrow_tickets_bound():
            return FolderPlacement(folder_id=folder_id, rel_path=None)
    stmt = select(Folder.rel_path).where(Folder.id == folder_id)
    if not include_deleted:
        stmt = stmt.where(Folder.deleted_at.is_(None))
    if session is not None:
        rel_path = (await session.execute(stmt)).scalar_one_or_none()
    else:
        async with async_session_factory() as owned:
            rel_path = (await owned.execute(stmt)).scalar_one_or_none()
    return FolderPlacement(folder_id=folder_id, rel_path=rel_path)


def placement_of(folder: Folder) -> FolderPlacement:
    """已经手握 ORM 行时的零查询版本。"""
    return FolderPlacement(folder_id=folder.id, rel_path=folder.rel_path)


async def resolve_workspace_paths(
    *,
    user_id: str,
    folder_id: str | None,
    conversation_id: str,
    session: AsyncSession | None = None,
) -> tuple[Path, Path]:
    """``(root, internal_root)`` —— 会话工作区的可见根与树外隐藏 zone 容器。

    给不构造 backend、直接读写盘的调用方（回收区路由等）：两个坐标一次给齐，省得
    有人只拿到 root 就去 ``root/AgentCore/trash`` 找一个已经不在那儿的回收区。
    """
    placement = await resolve_folder_placement(folder_id, session=session)
    root = workspace_root_path(
        user_id=user_id,
        folder_rel_path=placement.rel_path,
        conversation_id=conversation_id,
    )
    internal_root = workspace_internal_root(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    return root, internal_root
