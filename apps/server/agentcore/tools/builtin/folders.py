"""CEO-only folder roster tools: list / resolve / create / delete (cloud).

用户面只有一种容器——**文件夹**（双模式工作区 §5.4）。``Folder`` 实体仍在，但降级为
内部稳定引用：它记住「这个文件夹当前在树里的哪个位置」，所以改名 / 移动不断站立任务、
记忆与白板归属。对 AI 暴露的一律是文件夹口径，名册与 ``GET /folders`` 同形
（``FolderSummary`` 字段，含 ``rel_path``；无 OS 绝对路径）。

**嵌套是真的**：云文件夹落在 ``workspaces/<user>/tree/<rel_path>/``，父子关系由
``rel_path`` 前缀单一表达。因此 ``resolve_folder`` 按**路径**解析而不只按名字——
``设计/图标`` 与 ``归档/图标`` 是两个文件夹，只按末段名匹配必然在嵌套账号上误命中；
``create_folder`` 同理能用 ``parent_path`` 指定挂在哪一层。

``delete_folder``（软删，等价 ``DELETE /v1/folders/{id}``）只按 ``folder_id`` 删——
名字只在同层唯一，跨层同名合法（``设计/图标`` 与 ``归档/图标``），按名删必然误删。
每次调用逐个弹审批卡（恒确认，见 ``runtime.always_confirm``），没有「一卡放行 N 个」
的批量形态。彻底删（``/permanent``）**不**暴露给 AI：只能用户在桌面弹窗里勾选确认。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agentcore.api.schemas.conversations import FolderSummary
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
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

LIST_FOLDERS_TOOL_NAME = "list_folders"
RESOLVE_FOLDER_TOOL_NAME = "resolve_folder"
CREATE_FOLDER_TOOL_NAME = "create_folder"
DELETE_FOLDER_TOOL_NAME = "delete_folder"

# Keys a model reaches for when it means「按文件夹名删」. Present ⇒ refuse loudly
# instead of silently ignoring the arg and deleting whatever id came along.
_NAME_SHAPED_ARG_KEYS = (
    "name",
    "names",
    "path",
    "paths",
    "folder",
    "folders",
    "folder_name",
    "folder_path",
)
_DELETE_BY_NAME_REFUSED = (
    "delete_folder 只按 folder_id 删，【不接受文件夹名 / 路径】——跨层同名合法"
    "（`设计/图标` 与 `归档/图标`），按名删必然误删。请先 list_folders / resolve_folder "
    "拿到该文件夹的 id 再调用；多个命中先 ask_user（kind=choice，选项须含完整路径）"
    "让用户选，禁止静默猜「最近」。"
)
_DELETE_MISSING_ID = (
    "缺少 folder_id（要删除的文件夹 id）。先 list_folders / resolve_folder 拿 id；"
    "本工具不接受文件夹名 / 路径。"
)
_DELETE_NOT_FOUND = (
    "文件夹不存在或不属于当前账号，【没有删除任何东西】。"
    "请用 list_folders 核对后再试，勿凭记忆里的 id 重试。"
)
_DELETE_DONE_HINT = (
    "软删：文件夹连同它的子文件夹一起进最近删除，成员对话就地归档（不删除对话本身）；"
    "目录移出用户树到墓碑区，所以同层这个名字立刻可以再用；"
    "云端工作区文件与快照由保留期清理任务在保留期后自动回收；"
    "本机目录（本机文件夹背后那个真实目录）分毫未动。"
    "【彻底删除】不在 AI 能力内——只能用户自己在桌面弹窗里勾选确认。"
    "一次只删一个：还要删别的请再发一次 delete_folder（各自弹各自的审批卡）。"
)

_AMBIGUOUS_HINT = (
    "多个命中：请用 ask_user（kind=choice，multiple=false）让用户选一个；"
    "选项 label 须含**完整路径** rel_path（及 mode / local_subpath 等可区分信息）——"
    "只写末段名分不清 `设计/图标` 与 `归档/图标`；"
    "或改用更长的路径重新 resolve（如 `设计/图标` 而不是 `图标`）。"
    "禁止静默猜「最近」；禁止用 open_local_project 冒充选已有文件夹（那会新会话）。"
)
_NOT_FOUND_HINT = (
    "零命中：请向用户确认文件夹名 / 路径，或用 list_folders 核对后再 ask_user；"
    "嵌套账号注意先确认层级（`设计/图标` ≠ 顶层 `图标`）。"
    "【勿】为过写盘闸而 create_folder / ask_user 建夹——"
    "裸聊写盘缺桌由运行时自动建云文件夹。"
    "仅当用户明确要求新建云文件夹（可带名）或显式多线先建时，才用 create_folder"
    "（同指挥面登记，不改本会话归属、不新开会话）；"
    "本机目录进桌：**推荐** Composer「导入到云」；"
    "本机传统 open_local_project / register_local_project / bind_local_folder "
    "合法非默认（≠离线），勿与云平级主推；"
    "禁止静默猜「最近」。"
)
_EMPTY_LIST_HINT = (
    "当前账号下还没有文件夹。"
    "【勿】为过写盘闸而 create_folder / ask_user 建夹——"
    "裸聊写盘缺桌由运行时自动建云文件夹。"
    "仅当用户明确要求新建云文件夹（可带名）或显式多线先建时，才用 create_folder"
    "（同指挥面）；"
    "本机目录进桌：**推荐** Composer「导入到云」——"
    "勿默认催 open_local_project / register_local_project"
    "（本机传统合法非默认，≠离线）。"
    "HOW→consult(team_cross_folder)。"
)
_RESOLVED_TIP = (
    "空/近空先 ask_user 钉目标，勿连续 file_list 确认空；"
    "裸聊同回合仅此唯一目标时可省略 target（运行时继承）；"
    "队员坐该文件夹时读写范围 = 该层**及其子文件夹**；"
    "HOW→consult(team_cross_folder)。"
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
    the local FolderRepository. Shared by ``create_folder`` and bare-chat auto desk.

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
    """Owner-scoped single-folder fetch (``FolderSummary`` shape).

    ``None`` ⇒ 不存在 **或** 不属于该账号 — the two are deliberately indistinguishable
    (IDOR-safe, same posture as ``GET /folders/{id}``). Sidecar turns go through the
    folders narrow ticket; cloud API processes use the in-process repository.
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
        folder = await FolderRepository(session).get_by_id(folder_id, user_id=user_id)
    return folder_summary_dict(folder) if folder is not None else None


async def soft_delete_folder(*, user_id: str, folder_id: str) -> bool:
    """Soft-delete one folder — same semantics as ``DELETE /v1/folders/{id}``.

    Blast radius is exactly: the folder row (and its nested children) stamped
    ``deleted_at``, the directory parked in the tombstone area so the name frees up
    immediately, member conversations archived in place (membership kept), soft
    pointers (boards / bare-chat auto desk) NULLed. Server-side workspace +
    snapshots are reclaimed later by the retention sweeper. The user's OS directory
    behind ``local_root_id`` is never touched, and the ``/permanent`` twin is
    unreachable from here by construction.

    Goes through :func:`soft_delete_folder_tree`, not the bare repository call: the
    directory has to leave the visible tree with the row, or the name stays occupied
    for the whole retention window and the next folder of that name lands on the
    deleted one's files (双模式工作区 §5.4).

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
            return await cloud_soft_delete_folder(creds, folder_id=folder_id)
        except FoldersCloudError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FoldersCloudError(str(e)) from e

    async with async_session_factory() as session:
        return await soft_delete_folder_tree(
            session, user_id=user_id, folder_id=folder_id
        )


def looks_like_folder_id(value: str) -> bool:
    """True when ``value`` is a Folder id (``core.types.new_id`` ⇒ UUID4 string).

    The structural half of「拒绝按名删」: a folder name or path never parses as a
    UUID, so a name-shaped argument is rejected before any lookup or delete happens.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _is_folders_cloud_failure(exc: BaseException) -> bool:
    from agentcore.folders.credentials import FoldersCloudError

    return isinstance(exc, FoldersCloudError)


def _json_output(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


class ListFoldersTool:
    """CEO-only: list the authenticated user's live folders (名册 + 树中位置)."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=LIST_FOLDERS_TOOL_NAME,
            description=(
                "文件夹名册（rel_path）。清单已有 id 勿列；当前桌→file_list。"
                "按路径定位→resolve_folder。HOW→consult(team_cross_folder)。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
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


class ResolveFolderTool:
    """CEO-only: resolve a spoken / typed folder path to a Folder id."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=RESOLVE_FOLDER_TOOL_NAME,
            description=(
                "按路径解析已有文件夹为 id。嵌套同名须传完整路径。"
                "HOW→consult(team_cross_folder)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "文件夹路径（POSIX、相对云盘树根，如 `设计/图标`）"
                            "或用户口述的单个名字（精确或可唯一子串）。"
                            "匹配顺序：完整路径精确命中 → 路径后缀"
                            "（`图标` 命中 `设计/图标`）→ 单段名子串。"
                            "已知层级时传完整路径，歧义最少。"
                        ),
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
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


class CreateFolderTool:
    """CEO-only: create a cloud folder on the account (同指挥面先建后干).

    Mirrors ``POST /v1/folders`` with ``mode=cloud``, optionally nested under an
    existing folder. Returns FolderSummary-shaped payload for subsequent
    ``resolve_folder`` / ``delegate(target_folder_id=…)``. Never mutates the current
    conversation's ``folder_id`` or starts a new session. Local register-stay-command
    -surface is bucket D — not this tool.
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=CREATE_FOLDER_TOOL_NAME,
            description=(
                "仅用户明确新建云文件夹或显式多线先建时用；mode=cloud 可派工容器≠mkdir。"
                "≠open_local_project（会新会话）。"
                "HOW→consult(team_cross_folder)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "新文件夹显示名（单段，不含 `/`；同层重名会自动加序号后缀）。"
                        ),
                    },
                    "parent_path": {
                        "type": "string",
                        "description": (
                            "可选。挂在这个已有文件夹下面（POSIX 路径，如 `设计`、"
                            "`工作/设计`）。省略 = 建在云盘顶层。"
                            "解析规则同 resolve_folder；零命中或多命中会失败并让你先 "
                            "list_folders / resolve_folder 确认。"
                        ),
                    },
                },
                "required": ["name"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        name = str(arguments.get("name") or "").strip()
        if not name:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="缺少 name（新文件夹名称）。",
                error="missing name",
            )

        parent_path = str(arguments.get("parent_path") or "").strip()
        parent: dict[str, Any] | None = None
        if parent_path:
            parent, err = await self._resolve_parent(parent_path, context)
            if err is not None:
                return err

        # Account API only — never rebind conversation.folder_id (context.conversation_id
        # is intentionally unused beyond logging).
        try:
            folder = await create_cloud_folder(
                user_id=context.user_id,
                name=name,
                parent_id=str(parent.get("id")) if parent else None,
                parent_rel_path=folder_display_path(parent) if parent else None,
            )
        except Exception as e:  # noqa: BLE001
            cloud_fail = _is_folders_cloud_failure(e)
            logger.warning(
                "folders.create_failed",
                user_id=context.user_id,
                conversation_id=context.conversation_id or None,
                error=str(e),
                folders_cloud_failed=cloud_fail,
            )
            if cloud_fail:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"创建云文件夹失败。{e}",
                    error=getattr(e, "code", "folders_cloud_failed"),
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="创建云文件夹失败，请稍后再试。",
                error=str(e),
            )

        folder_id = folder.get("id") if isinstance(folder.get("id"), str) else None
        context.turn_target_desk.note_folder(folder_id)
        context.note_turn_created_folder(folder_id)
        rel_path = folder_display_path(folder)
        logger.info(
            "folders.created",
            user_id=context.user_id,
            folder_id=folder_id,
            nested=bool(parent),
            conversation_id=context.conversation_id or None,
            conversation_untouched=True,
            run_id=context.run_id,
        )
        payload = {
            "status": "created",
            "folder": folder,
            "conversation_untouched": True,
            "hint": (
                "文件夹已建在账号云盘里；本会话归属/默认桌未改。"
                "可直接用返回的 id 作为 delegate target_folder_id；"
                "裸聊同回合仅此一个目标时也可省略（运行时继承）。"
                "多个目标同回合仍须各 task 显式点名。"
                "同层重名会自动加序号，落地名以返回的 name / rel_path 为准。"
            ),
        }
        where = f"（路径 {rel_path}）" if rel_path else ""
        return ToolResult(
            tool_call_id="",
            success=True,
            output=(
                f"已创建云文件夹{where}（同指挥面；未改会话归属、未新开会话）：\n"
                + _json_output(payload)
            ),
            display={
                "status": "created",
                "folder_id": folder.get("id"),
                "name": folder.get("name"),
                "rel_path": rel_path,
                "mode": folder.get("mode"),
                "conversation_untouched": True,
            },
        )

    async def _resolve_parent(
        self, parent_path: str, context: ToolContext
    ) -> tuple[dict[str, Any] | None, ToolResult | None]:
        """Resolve ``parent_path`` to one live folder, or explain why it can't be."""
        try:
            folders = await _load_user_folder_summaries(context.user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "folders.create_failed",
                user_id=context.user_id,
                phase="parent_lookup",
                error=str(e),
                folders_cloud_failed=_is_folders_cloud_failure(e),
            )
            return None, ToolResult(
                tool_call_id="",
                success=False,
                output=f"解析上级文件夹 `{parent_path}` 失败，未创建任何东西。{e}",
                error="parent_lookup_failed",
            )

        outcome = resolve_folders_by_path(folders, parent_path)
        if outcome.status == "resolved":
            return outcome.matches[0], None
        if outcome.status == "ambiguous":
            candidates = "、".join(folder_display_path(m) for m in outcome.matches)
            return None, ToolResult(
                tool_call_id="",
                success=False,
                output=(
                    f"上级文件夹 `{parent_path}` 命中多个（{candidates}），"
                    "【没有创建任何东西】。请传更完整的 parent_path，"
                    "或先 ask_user（kind=choice，选项带完整路径）让用户选。"
                ),
                error="parent_ambiguous",
            )
        return None, ToolResult(
            tool_call_id="",
            success=False,
            output=(
                f"上级文件夹 `{parent_path}` 不存在，【没有创建任何东西】。"
                "请先 list_folders 核对路径；确实要建在顶层就省略 parent_path，"
                "【不要】为了过参数而临时另建一层。"
            ),
            error="parent_not_found",
        )


def _delete_failure_result(exc: Exception, *, deleted_unknown: bool) -> ToolResult:
    """Map an infra failure to a tool result, honest about what did / didn't happen.

    ``deleted_unknown`` ⇒ the failure hit the delete call itself, so the model must
    re-check the roster rather than assume either outcome.
    """
    from agentcore.workspace.locks import WorkspaceBusyError

    tail = (
        "该文件夹是否已删除【无法确定】，请 list_folders 核对后再决定是否重试。"
        if deleted_unknown
        else "未删除任何东西。"
    )
    if isinstance(exc, WorkspaceBusyError):
        # Expected and retriable: the directory cannot move out from under a running
        # turn. Nothing was deleted regardless of which phase raised.
        return ToolResult(
            tool_call_id="",
            success=False,
            output=(
                "该文件夹的工作区正忙（有回合在跑），【未删除任何东西】。"
                "等这轮跑完再发一次 delete_folder（会重新弹审批卡）。"
            ),
            error="workspace_busy",
        )
    if is_db_connectivity_error(exc):
        return ToolResult(
            tool_call_id="",
            success=False,
            output=f"删除文件夹失败。{DATABASE_UNAVAILABLE_MESSAGE}{tail}",
            error=DATABASE_UNAVAILABLE_CODE,
            failure_code=DATABASE_UNAVAILABLE_CODE,
        )
    if _is_folders_cloud_failure(exc):
        return ToolResult(
            tool_call_id="",
            success=False,
            output=f"删除文件夹失败。{exc}{tail}",
            error=getattr(exc, "code", "folders_cloud_failed"),
        )
    return ToolResult(
        tool_call_id="",
        success=False,
        output=f"删除文件夹失败，请稍后再试。{tail}",
        error=str(exc),
    )


class DeleteFolderTool:
    """CEO-only: soft-delete ONE existing folder, by id, behind its own approval card.

    Equivalent to ``DELETE /v1/folders/{folder_id}`` (软删；嵌套子文件夹跟着走). Three
    shapes are deliberately absent: 按名 / 按路径删 (跨层同名合法 ⇒ 必然误删), 批量删
    (one card must never authorise N deletions), and 彻底删 (``/permanent`` stays a
    user-only desktop dialog). ``GRANTABLE`` + 恒确认 (``runtime.always_confirm``) so no
    turn / kickoff / session grant can swallow the card — several deletions in one
    round each prompt.
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=DELETE_FOLDER_TOOL_NAME,
            description=(
                "删除当前账号下的一个【已有文件夹】（软删；等同侧栏删除 / "
                "DELETE /folders/{id}）。语义：文件夹连同其子文件夹进最近删除，"
                "成员对话就地归档（对话本身不删），目录移出用户树到墓碑区"
                "（所以同层这个名字立刻可以再用），"
                "云端工作区文件与快照在保留期后由清理任务回收；"
                "**不动**本机目录（本机文件夹背后那个真实目录分毫未动）。"
                "【只按 folder_id】——跨层同名合法（`设计/图标` 与 `归档/图标`），"
                "按名删必然误删：先 list_folders / resolve_folder 拿 id；"
                "多命中先 ask_user（kind=choice，选项带完整路径）让用户选。"
                "【一次一个】每次调用逐个弹审批卡；要删多个就发多次调用，"
                "禁止拼成一次批量、也禁止用「本轮内都允许」代替逐个确认。"
                "【连子文件夹一起】删的是整棵子树，不是只删这一层——"
                "用户只想删里面某一层时请点名那一层的 id。"
                "【彻底删除做不到】/permanent 只能用户在桌面弹窗里勾选确认，"
                "别向用户承诺 AI 能彻底清盘。"
                "误删风险高：用户没点名要删就不要删；不确定删哪个先 ask_user。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "要删除的文件夹 id（UUID，来自 list_folders / resolve_folder / "
                            "create_folder 返回的 id 字段）。【不接受名字或路径】。"
                        ),
                    },
                },
                "required": ["folder_id"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        folder_id = str(arguments.get("folder_id") or "").strip()
        if not folder_id or not looks_like_folder_id(folder_id):
            # A name-shaped ``folder_id``, or a name passed under any of the keys a
            # model reaches for — both mean「按名删」and must be refused, not ignored.
            by_name = bool(folder_id) or any(
                arguments.get(key) for key in _NAME_SHAPED_ARG_KEYS
            )
            logger.info(
                "folders.delete_refused",
                user_id=context.user_id,
                reason="by_name" if by_name else "missing_folder_id",
                run_id=context.run_id,
            )
            if by_name:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=_DELETE_BY_NAME_REFUSED,
                    error="delete_by_name_refused",
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output=_DELETE_MISSING_ID,
                error="missing folder_id",
            )

        # Name the target before touching it: the tool回显 (and the approval card the
        # engine already rendered) must say WHICH folder — a bare UUID is unauditable.
        try:
            folder = await load_folder_summary(
                user_id=context.user_id, folder_id=folder_id
            )
        except Exception as e:  # noqa: BLE001 — tool failure must not crash the turn
            logger.warning(
                "folders.delete_failed",
                user_id=context.user_id,
                folder_id=folder_id,
                phase="load",
                error=str(e),
                db_unreachable=is_db_connectivity_error(e),
                folders_cloud_failed=_is_folders_cloud_failure(e),
            )
            return _delete_failure_result(e, deleted_unknown=False)

        if folder is None:
            logger.info(
                "folders.delete_missed",
                user_id=context.user_id,
                folder_id=folder_id,
                run_id=context.run_id,
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output=_DELETE_NOT_FOUND,
                error="folder_not_found",
            )

        try:
            deleted = await soft_delete_folder(
                user_id=context.user_id, folder_id=folder_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "folders.delete_failed",
                user_id=context.user_id,
                folder_id=folder_id,
                phase="delete",
                error=str(e),
                db_unreachable=is_db_connectivity_error(e),
                folders_cloud_failed=_is_folders_cloud_failure(e),
            )
            return _delete_failure_result(e, deleted_unknown=True)

        if not deleted:
            # Raced with a sidebar delete between load and delete.
            logger.info(
                "folders.delete_missed",
                user_id=context.user_id,
                folder_id=folder_id,
                run_id=context.run_id,
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output=_DELETE_NOT_FOUND,
                error="folder_not_found",
            )

        folder_name = str(folder.get("name") or "")
        rel_path = folder_display_path(folder)
        logger.info(
            "folders.deleted",
            user_id=context.user_id,
            folder_id=folder_id,
            mode=folder.get("mode"),
            permanent=False,
            run_id=context.run_id,
        )
        payload = {
            "status": "deleted",
            "permanent": False,
            "folder": folder,
            "hint": _DELETE_DONE_HINT,
        }
        return ToolResult(
            tool_call_id="",
            success=True,
            output=(
                f"已删除文件夹「{rel_path or folder_name}」（软删，id={folder_id}）：\n"
                + _json_output(payload)
            ),
            display={
                "status": "deleted",
                "folder_id": folder_id,
                "name": folder_name,
                "rel_path": rel_path,
                "mode": folder.get("mode"),
                "permanent": False,
            },
        )
