"""注入用的作用域链：全局之外，还有由外向里的文件夹祖先链。

双模式工作区 §5.4 定案「规则 / 记忆沿树由外向里继承」——外层文件夹定的约定，里层
自动适用。父子关系的单一真相源是 ``folders.rel_path`` 的段前缀（没有 ``parent_id``），
所以链的解析在 :meth:`FolderRepository.list_ancestor_chain_ids`，本模块只回答注入端的
两个问题：**这一回合读哪几个分区**、**从哪儿问出这条链**。

**存储不搬家**：规则与记忆仍是 documents 虚拟树、仍按 ``folder_id`` 分区，id 不变即
不失忆。变的只有读侧。

链一律**由外向里**返回（末位 = 当前文件夹），注入端按这个顺序拼，越靠后越近；冲突
以更近的为准，靠措辞 + 就近相关性解，不做硬覆盖结构（记忆系统 §一「冲突」）。

两条读法各有真相源：

- 云 API 回合读本机 DB。
- 桌面本机引擎（account 窄票）根本没有 folders 表，链由云在 ``/rules/list`` 一并算好
  随 prepare 快照下来。快照 miss（TTL 过期 / 未 warm）时退回「只当前层」——少继承是
  可解释的降级，凭空猜一条链不是。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentcore.memory.account_prepare_cache import AccountPrepareSnapshot

logger = get_logger(__name__)


def own_scope_chain(folder_id: str | None) -> tuple[str, ...]:
    """只有当前层的链（无文件夹 → 空）——所有降级路径的落点。"""
    return (folder_id,) if folder_id else ()


def ancestor_scopes(chain: Sequence[str]) -> tuple[str, ...]:
    """链上除当前层以外的祖先，仍是由外向里。"""
    return tuple(chain[:-1])


def snapshot_scope_chain(
    snapshot: AccountPrepareSnapshot | None, folder_id: str | None
) -> tuple[str, ...]:
    """warm 快照里的链；缺失 / 不含当前层 → 只当前层。

    「不含当前层」意味着这份快照不是给这个文件夹 warm 的（或云端太旧还不会算链），
    此时按它的祖先注入会把别的文件夹的约定塞进来，比不继承更糟。

    ``rules_payload.folder_chain: []`` 与 :func:`cloud_scope_chain` 同义：桌不在活树，
    不要退回只当前层。载荷没有该键时仍看 ``snapshot.folder_chain``（旧快照 / 单测）。
    """
    if not folder_id:
        return ()
    if snapshot is None:
        return own_scope_chain(folder_id)
    payload = snapshot.rules_payload
    raw = payload.get("folder_chain") if payload else None
    if isinstance(raw, list):
        # Explicit [] = desk gone (soft-delete). Missing key still falls back
        # through snapshot.folder_chain / current-only below.
        return cloud_scope_chain(payload, folder_id)
    chain = tuple(snapshot.folder_chain)
    return chain if folder_id in chain else own_scope_chain(folder_id)


def cloud_scope_chain(
    payload: Mapping[str, object], folder_id: str | None
) -> tuple[str, ...]:
    """``/rules/list`` 载荷里的 ``folder_chain``。

    旧云没有该键 / 垃圾值 → 只当前层。显式 ``[]`` = 这张桌不在活树里（软删），
    不要退回「只注当前层」——那会把已删桌的设定继续灌进去。
    """
    if not folder_id:
        return ()
    raw = payload.get("folder_chain")
    if isinstance(raw, list):
        chain = tuple(str(fid) for fid in raw if str(fid))
        if not chain:
            return ()
        return chain if folder_id in chain else own_scope_chain(folder_id)
    return own_scope_chain(folder_id)


async def db_scope_chain(
    user_id: str,
    folder_id: str | None,
    *,
    session: AsyncSession | None = None,
) -> tuple[str, ...]:
    """读本机 ``folders.rel_path`` 解析链。

    文件夹不存在或已软删 → 空链（这张桌的设定退出注入，全局层仍由调用方另载）。
    查链出错 → 只当前层，以免一次 DB 抖动把继承整段摘掉。

    请求路径上传自己的 ``session``：另开一个会读到另一个连接（集成测试里甚至是另一个
    schema），链解析不出来就退回不继承，症状是「外层规则时灵时不灵」。
    """
    if not folder_id:
        return ()
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.folders import FolderRepository

    try:
        if session is not None:
            ids = await FolderRepository(session).list_ancestor_chain_ids(
                folder_id, user_id=user_id
            )
        else:
            async with async_session_factory() as owned:
                ids = await FolderRepository(owned).list_ancestor_chain_ids(
                    folder_id, user_id=user_id
                )
    except Exception as e:  # noqa: BLE001 — 继承解析失败绝不能打断回合
        logger.warning(
            "memory.scope_chain_failed",
            user_id=user_id,
            folder_id=folder_id,
            error=str(e),
        )
        return own_scope_chain(folder_id)
    if not ids:
        return ()
    return tuple(ids) if folder_id in ids else own_scope_chain(folder_id)


async def resolve_scope_chain(
    user_id: str,
    folder_id: str | None,
    *,
    session: AsyncSession | None = None,
) -> tuple[str, ...]:
    """当前回合该注入的作用域链（有票读快照，无票读本机 DB）。

    给手上没有现成载荷 / 快照的调用方（如执行期的 ``consult`` 取正文）；已经握着
    快照或 ``/rules/list`` 载荷的注入路径直接用 :func:`snapshot_scope_chain` /
    :func:`cloud_scope_chain`，省一次缓存查询。
    """
    if not folder_id:
        return ()
    from agentcore.account.credentials import get_account_credentials

    if get_account_credentials() is None:
        return await db_scope_chain(user_id, folder_id, session=session)
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot

    return snapshot_scope_chain(
        get_account_rules_memory_snapshot(user_id, folder_id), folder_id
    )


__all__ = [
    "ancestor_scopes",
    "cloud_scope_chain",
    "db_scope_chain",
    "own_scope_chain",
    "resolve_scope_chain",
    "snapshot_scope_chain",
]
