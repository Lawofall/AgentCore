"""CEO-only folder roster tool: list / resolve (cloud).

用户面只有一种容器——**文件夹**（工作区.md §5.4）。``Folder`` 实体仍在，但降级为
内部稳定引用：它记住「这个文件夹当前在树里的哪个位置」，所以改名 / 移动不断工作流触发、
记忆。对 AI 暴露的一律是文件夹口径，名册与 ``GET /folders`` 同形
（``FolderSummary`` 字段，含 ``rel_path``；无 OS 绝对路径）。

**嵌套是真的**：云文件夹落在 ``workspaces/<user>/tree/<rel_path>/``，父子关系由
``rel_path`` 前缀单一表达。因此 ``folders(action=resolve)`` 按**路径**解析而不只按名字——
``设计/图标`` 与 ``归档/图标`` 是两个文件夹，只按末段名匹配必然在嵌套账号上误命中。

新建 / 软删云文件夹是人侧（「我的文件」+ REST）。裸聊写盘缺桌由运行时自动建云桌。
彻底删（``/permanent`` 与回收站清盘）只能用户自己确认。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agentcore.api.schemas.conversations import FolderSummary
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.db.base import async_session_factory
from agentcore.db.errors import (
    DATABASE_UNAVAILABLE_CODE,
    DATABASE_UNAVAILABLE_MESSAGE,
    is_db_connectivity_error,
)
from agentcore.db.repositories import FolderRepository
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.cloud_tree import normalize_rel_path, rel_path_segments

logger = get_logger(__name__)

FOLDERS_TOOL_NAME = "folders"
_ACTION_LIST = "list"
_ACTION_RESOLVE = "resolve"

_AMBIGUOUS_HINT = (
    "多个命中：请用 ask_user（给出 options，multiple=false）让用户选一个；"
    "选项 label 须含**完整路径** rel_path（及 mode / local_subpath 等可区分信息）——"
    "只写末段名分不清 `设计/图标` 与 `归档/图标`；"
    "或改用更长的路径重新 resolve（如 `设计/图标` 而不是 `图标`）。"
    "禁止静默猜「最近」。"
)
_NOT_FOUND_HINT = (
    "零命中：请向用户确认文件夹名 / 路径，或用 folders 核对后再 ask_user；"
    "嵌套账号注意先确认层级（`设计/图标` ≠ 顶层 `图标`）。"
    "【勿】为过写盘闸而 ask_user 建夹——"
    "裸聊写盘：云会话由运行时自动建云文件夹；"
    "桌面本地对话已在本机 scratch（本次对话），勿再「云上做完再写入」当默认。"
    "用户要新开云文件夹：在「我的文件」新建（人侧）。"
    "用户点名本机目录：请人在 Composer 点该本机文件夹，"
    "或「新建或加入」里「从本机加入」（新开对话，即改电脑上的原件）；"
    "换设备走命令面板「云上做完再写入」。"
    "禁止静默猜「最近」。"
)
_EMPTY_LIST_HINT = (
    "当前账号下还没有文件夹。"
    "【勿】为过写盘闸而 ask_user 建夹——"
    "裸聊写盘：云会话由运行时自动建云文件夹；桌面本地对话已在本机 scratch。"
    "用户要新开云文件夹：在「我的文件」新建（人侧）；"
    "用户点名本机目录：Composer 点该本机文件夹或「从本机加入」（即改电脑上的原件）；"
    "换设备走命令面板「云上做完再写入」。"
)
_RESOLVED_TIP = (
    "空/近空先 ask_user 钉目标，勿连续 file_list 确认空；"
    "裸聊同回合仅此唯一目标时可省略 target（运行时继承）；"
    "队员坐该文件夹时读写范围 = 该层**及其子文件夹**。"
)


def folder_summary_dict(folder: Any) -> dict[str, Any]:
    """Same wire shape as ``GET /folders`` (``FolderSummary``)."""
    return FolderSummary.from_folder(folder).model_dump(mode="json")


@dataclass(frozen=True)
class ResolveOutcome:
    status: Literal["resolved", "ambiguous", "not_found"]
    matches: tuple[dict[str, Any], ...]


def folder_display_path(summary: Any) -> str:
    """The path a user would type for this folder (``设计/图标``).

    Falls back to the bare name for rows without a ``rel_path`` — legacy folders
    predating the cloud tree still have to be addressable.
    """
    if not isinstance(summary, dict):
        return ""
    rel = normalize_rel_path(str(summary.get("rel_path") or ""))
    return rel or str(summary.get("name") or "")


def resolve_folders_by_path(
    summaries: Sequence[dict[str, Any]],
    path: str,
) -> ResolveOutcome:
    """Match ``path`` against FolderSummary-shaped dicts (case-insensitive).

    Three passes, narrowest first — a full path beats a partial one, and a path
    beats a fuzzy name:

    1. **Exact path** — ``设计/图标`` hits exactly that folder.
    2. **Path suffix** — ``图标`` hits ``设计/图标``; ``设计/图标`` hits
       ``工作/设计/图标``. Compared segment-wise, so ``标`` never matches ``图标``
       here and ``报告`` never matches ``报告备份``.
    3. **Name substring** — only for a single-segment query, and only against the
       last segment: the old flat-roster behaviour, kept for「叫什么来着」.

    Never ranks by recency — unique ⇒ resolved; 0 ⇒ not_found; many ⇒ ambiguous.
    Two folders may legitimately share a last segment across levels, which is
    exactly why pass 1 exists and why ambiguity is reported with full paths.
    """
    needle = normalize_rel_path(path)
    if not needle:
        return ResolveOutcome(status="not_found", matches=())

    query_segments = tuple(seg.casefold() for seg in rel_path_segments(needle))
    paths = [(s, rel_path_segments(folder_display_path(s))) for s in summaries]

    def _folded(segments: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(seg.casefold() for seg in segments)

    exact = tuple(s for s, segs in paths if _folded(segs) == query_segments)
    if exact:
        return ResolveOutcome(
            status="resolved" if len(exact) == 1 else "ambiguous", matches=exact
        )

    depth = len(query_segments)
    suffix = tuple(
        s for s, segs in paths if depth and _folded(segs)[-depth:] == query_segments
    )
    if suffix:
        return ResolveOutcome(
            status="resolved" if len(suffix) == 1 else "ambiguous", matches=suffix
        )

    if depth == 1:
        lowered = query_segments[0]
        partial = tuple(
            s for s, segs in paths if segs and lowered in segs[-1].casefold()
        )
        if partial:
            return ResolveOutcome(
                status="resolved" if len(partial) == 1 else "ambiguous",
                matches=partial,
            )
    return ResolveOutcome(status="not_found", matches=())


async def _load_user_folder_summaries(user_id: str) -> list[dict[str, Any]]:
    from agentcore.folders.credentials import (
        FoldersCloudError,
        cloud_list_folders,
        get_folders_credentials,
    )

    creds = get_folders_credentials()
    if creds is not None:
        try:
            return await cloud_list_folders(creds)
        except FoldersCloudError:
            raise
        except Exception as e:  # noqa: BLE001 — normalize unexpected HTTP failures
            raise FoldersCloudError(str(e)) from e

    async with async_session_factory() as session:
        folders = await FolderRepository(session).list_by_user(user_id)
    return [folder_summary_dict(f) for f in folders]


async def create_cloud_folder(
    *,
    user_id: str,
    name: str,
    parent_id: str | None = None,
    parent_rel_path: str | None = None,
) -> dict[str, Any]:
    """Account-level cloud Folder create — same semantics as ``POST /folders`` mode=cloud.

    Does **not** touch any Conversation row (no ``folder_id`` rebind, no new session).
    With folders narrow-ticket creds (sidecar), calls the cloud HTTP API instead of
    the local FolderRepository. Shared by the file-hub REST path and bare-chat auto desk.

    The two backends address the parent differently (HTTP takes ``parent_id`` and
    derives the prefix server-side; the repository takes the prefix directly), so
    callers pass **both** off one already-resolved parent rather than letting each
    path look the parent up again and possibly disagree. Omit both for top level.
    """
    from agentcore.folders.credentials import (
        FoldersCloudError,
        cloud_create_cloud_folder,
        get_folders_credentials,
    )

    creds = get_folders_credentials()
    if creds is not None:
        try:
            return await cloud_create_cloud_folder(
                creds, name=name, parent_id=parent_id
            )
        except FoldersCloudError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FoldersCloudError(str(e)) from e

    async with async_session_factory() as session:
        folder = await FolderRepository(session).create(
            user_id=user_id,
            name=name,
            local_root_id=None,
            local_subpath=None,
            parent_rel_path=parent_rel_path,
        )
    return folder_summary_dict(folder)


async def load_folder_summary(*, user_id: str, folder_id: str) -> dict[str, Any] | None:
    """Accepted-member single-folder fetch (``FolderSummary`` shape).

    ``None`` ⇒ 不存在 **或** 调用者不是已接受成员 — the two are deliberately
    indistinguishable (IDOR-safe, same posture as ``GET /folders/{id}``). Sidecar
    turns go through the folders narrow ticket; cloud API processes use the
    in-process repository.
    """
    from agentcore.folders.credentials import (
        FoldersCloudError,
        cloud_get_folder,
        get_folders_credentials,
    )

    creds = get_folders_credentials()
    if creds is not None:
        try:
            return await cloud_get_folder(creds, folder_id=folder_id)
        except FoldersCloudError:
            raise
        except Exception as e:  # noqa: BLE001 — normalize unexpected HTTP failures
            raise FoldersCloudError(str(e)) from e

    async with async_session_factory() as session:
        from agentcore.folders.desk import resolve_desk_access

        access = await resolve_desk_access(session, folder_id=folder_id, user_id=user_id)
        if access is None:
            return None
        return FolderSummary.from_folder(
            access.folder,
            my_role=access.role,
            my_state=access.state,
        ).model_dump(mode="json")


async def soft_delete_folder(*, user_id: str, folder_id: str) -> bool:
    """Soft-delete one folder — same semantics as ``DELETE /v1/folders/{id}``.

    Blast radius is exactly: the folder row (and its nested children) stamped
    ``deleted_at``, the directory parked in the tombstone area so the name frees up
    immediately, member conversations archived in place (membership kept), soft
    pointers (bare-chat auto desk) NULLed. Server-side workspace +
    snapshots are reclaimed later by the retention sweeper. The user's OS directory
    behind ``local_root_id`` is never touched, and the ``/permanent`` twin is
    unreachable from here by construction.

    Goes through :func:`soft_delete_folder_tree`, not the bare repository call: the
    directory has to leave the visible tree with the row, or the name stays occupied
    for the whole retention window and the next folder of that name lands on the
    deleted one's files (工作区.md §5.4).

    ``False`` ⇒ nothing matched (unknown id / not the caller's folder). Raises
    ``WorkspaceBusyError`` when a turn holds the workspace lock.
    """
    from agentcore.folders.credentials import (
        FoldersCloudError,
        cloud_soft_delete_folder,
        get_folders_credentials,
    )
    from agentcore.folders.tree_ops import soft_delete_folder_tree

    creds = get_folders_credentials()
    if creds is not None:
        try:
            deleted = await cloud_soft_delete_folder(creds, folder_id=folder_id)
        except FoldersCloudError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FoldersCloudError(str(e)) from e
        if deleted:
            from agentcore.memory.account_prepare_cache import (
                hibernate_folder_injection_cache,
            )

            await hibernate_folder_injection_cache(
                user_id, [folder_id], rewarm=True
            )
        return deleted

    async with async_session_factory() as session:
        repo = FolderRepository(session)
        subtree_ids = await repo.list_live_subtree_ids(
            folder_id, user_id=user_id
        )
        deleted = await soft_delete_folder_tree(
            session, user_id=user_id, folder_id=folder_id
        )
    if deleted:
        from agentcore.memory.account_prepare_cache import (
            hibernate_folder_injection_cache,
        )

        await hibernate_folder_injection_cache(user_id, subtree_ids)
    return deleted


def _is_folders_cloud_failure(exc: BaseException) -> bool:
    from agentcore.folders.credentials import FoldersCloudError

    return isinstance(exc, FoldersCloudError)


def _json_output(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


class FoldersTool:
    """CEO-only: list the live folder roster, or resolve a path to a Folder id."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
        catalog_summary="列出或解析云文件夹",
        blurb="查云端文件夹名单，或解析某个文件夹",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=FOLDERS_TOOL_NAME,
            description=(
                "文件夹名册（rel_path）。名册不常驻，跨桌先列。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [_ACTION_LIST, _ACTION_RESOLVE],
                        "description": (
                            "list 列出名册；resolve 按路径解析为 id（嵌套同名须完整路径）。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "resolve：相对云盘树根的 POSIX，或可唯一对上的名字。"
                        ),
                    },
                },
                "required": ["action"],
            },
            face=ToolFace.FOLDER,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = str(arguments.get("action") or "").strip()
        if action == _ACTION_LIST:
            return await self._execute_list(arguments, context)
        if action == _ACTION_RESOLVE:
            return await self._execute_resolve(arguments, context)
        if not action:
            error = "action 为必填（list / resolve）。"
        else:
            error = f"action `{action}` 不在允许列表（list / resolve）。"
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error=error,
        )

    async def _execute_list(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        del arguments  # no params
        try:
            folders = await _load_user_folder_summaries(context.user_id)
        except Exception as e:  # noqa: BLE001 — tool failure must not crash the turn
            cloud_fail = _is_folders_cloud_failure(e)
            logger.warning(
                "folders.list_failed",
                user_id=context.user_id,
                error=str(e),
                db_unreachable=is_db_connectivity_error(e),
                folders_cloud_failed=cloud_fail,
            )
            if is_db_connectivity_error(e):
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"列出文件夹失败。{DATABASE_UNAVAILABLE_MESSAGE}",
                    error=DATABASE_UNAVAILABLE_CODE,
                    failure_code=DATABASE_UNAVAILABLE_CODE,
                )
            if cloud_fail:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"列出文件夹失败。{e}",
                    error=getattr(e, "code", "folders_cloud_failed"),
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="列出文件夹失败，请稍后再试。",
                error=str(e),
            )

        logger.info(
            "folders.listed",
            user_id=context.user_id,
            count=len(folders),
            run_id=context.run_id,
        )
        payload = {"folders": folders, "count": len(folders)}
        if not folders:
            text = _EMPTY_LIST_HINT + "\n" + _json_output(payload)
        else:
            text = f"共 {len(folders)} 个文件夹：\n" + _json_output(payload)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=text,
            display={"count": len(folders)},
        )

    async def _execute_resolve(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = str(arguments.get("path") or "").strip()
        if not path:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="缺少 path（要解析的文件夹路径或名字）。",
                error="missing path",
            )

        try:
            folders = await _load_user_folder_summaries(context.user_id)
        except Exception as e:  # noqa: BLE001
            cloud_fail = _is_folders_cloud_failure(e)
            logger.warning(
                "folders.resolve_failed",
                user_id=context.user_id,
                error=str(e),
                db_unreachable=is_db_connectivity_error(e),
                folders_cloud_failed=cloud_fail,
            )
            if is_db_connectivity_error(e):
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"解析文件夹失败。{DATABASE_UNAVAILABLE_MESSAGE}",
                    error=DATABASE_UNAVAILABLE_CODE,
                    failure_code=DATABASE_UNAVAILABLE_CODE,
                )
            if cloud_fail:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"解析文件夹失败。{e}",
                    error=getattr(e, "code", "folders_cloud_failed"),
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="解析文件夹失败，请稍后再试。",
                error=str(e),
            )

        outcome = resolve_folders_by_path(folders, path)
        logger.info(
            "folders.resolved",
            user_id=context.user_id,
            status=outcome.status,
            match_count=len(outcome.matches),
            run_id=context.run_id,
        )

        if outcome.status == "resolved":
            folder = outcome.matches[0]
            context.turn_target_desk.note_folder(
                folder.get("id") if isinstance(folder.get("id"), str) else None
            )
            payload: dict[str, Any] = {
                "status": "resolved",
                "query": path,
                "folder": folder,
            }
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    "唯一命中，可直接用于后续派工"
                    f"（{_RESOLVED_TIP}）：\n" + _json_output(payload)
                ),
                display={
                    "status": "resolved",
                    "folder_id": folder.get("id"),
                    "name": folder.get("name"),
                    "rel_path": folder_display_path(folder),
                    "mode": folder.get("mode"),
                },
            )

        if outcome.status == "ambiguous":
            payload = {
                "status": "ambiguous",
                "query": path,
                "matches": list(outcome.matches),
                "hint": _AMBIGUOUS_HINT,
            }
            return ToolResult(
                tool_call_id="",
                success=True,
                output=_AMBIGUOUS_HINT + "\n" + _json_output(payload),
                display={
                    "status": "ambiguous",
                    "match_count": len(outcome.matches),
                },
            )

        payload = {
            "status": "not_found",
            "query": path,
            "matches": [],
            "hint": _NOT_FOUND_HINT,
        }
        return ToolResult(
            tool_call_id="",
            success=True,
            output=_NOT_FOUND_HINT + "\n" + _json_output(payload),
            display={"status": "not_found", "match_count": 0},
        )
