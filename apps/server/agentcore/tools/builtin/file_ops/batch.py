"""file_batch tool (multi op move/copy/delete/mkdir)."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.file_products import FileProduct, file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.host_path import GrantMode
from agentcore.workspace.limits import workspace_channel_failure_kind
from agentcore.workspace.protocol import (
    AlreadyExists,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

from .errors import (
    _error,
    _liveness_workspace_error,
    _op_liveness_timeout_error,
    _outside_workspace_msg,
)
from .integrity import prepared_write_relpath, write_scope_rejection
from .prepare_path import prepare_tool_path

logger = get_logger(__name__)

_BATCH_OPS = frozenset({"move", "copy", "delete", "mkdir"})
_BATCH_MAX_OPS = 50


def _workspace_item_fail(
    prefix: str, e: WorkspaceError
) -> tuple[str, str, list[FileProduct]]:
    if workspace_channel_failure_kind(e) == "presence":
        raise
    return "fail", f"{prefix}：{e}", []


def _stop_if_channel(
    exc_or_detail: BaseException | str,
    start: float,
    products: list[FileProduct],
) -> ToolResult | None:
    kind = workspace_channel_failure_kind(exc_or_detail)
    if kind == "presence":
        dead = _liveness_workspace_error(str(exc_or_detail), start)
        dead.file_products = products
        return dead
    if kind == "liveness":
        timed = _op_liveness_timeout_error(str(exc_or_detail), start)
        timed.file_products = products
        return timed
    return None


def _batch_op_label(item: dict[str, Any]) -> str:
    op = str(item.get("op", "")).strip()
    if op == "move":
        return f"move {item.get('source', '')} → {item.get('destination', '')}"
    if op == "copy":
        return f"copy {item.get('source', '')} → {item.get('destination', '')}"
    if op == "delete":
        perm = " (永久)" if item.get("permanent") else ""
        return f"delete {item.get('path', '')}{perm}"
    if op == "mkdir":
        return f"mkdir {item.get('path', '')}"
    return f"? {op}"


class FileBatchTool:
    """Apply multiple move/copy/delete/mkdir ops in one call (partial failure OK)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        # 一次调用可产多件（move / copy 逐件自报；mkdir / delete 没有产物）。
        file_products=FileProductsContract.SELF_REPORT,
        workspace_io=True,
        catalog_summary="工作区移动/复制/删除/建目录",
        blurb="一次处理多个路径的搬家或清理",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_batch",
            description="工作区 move / copy / delete / mkdir。",
            parameters={
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "description": "按顺序执行。",
                        "minItems": 1,
                        "maxItems": _BATCH_MAX_OPS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": ["move", "copy", "delete", "mkdir"],
                                },
                                "path": {
                                    "type": "string",
                                    "description": "delete / mkdir 的相对路径",
                                },
                                "source": {
                                    "type": "string",
                                    "description": "move / copy 的源相对路径",
                                },
                                "destination": {
                                    "type": "string",
                                    "description": (
                                        "move / copy 的目标相对路径"
                                        "（已存在则跳过、不覆盖）。"
                                    ),
                                },
                            },
                            "required": ["op"],
                        },
                    },
                },
                "required": ["operations"],
            },
            face=ToolFace.FILE,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        raw = arguments.get("operations")
        if not isinstance(raw, list) or not raw:
            return _error("operations 必须是非空数组", start)
        if len(raw) > _BATCH_MAX_OPS:
            return _error(f"operations 最多 {_BATCH_MAX_OPS} 项", start)

        lines: list[str] = [f"本次共 {len(raw)} 项："]
        ok_n = skip_n = fail_n = 0
        products: list[FileProduct] = []

        for i, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                fail_n += 1
                lines.append(f"{i}. 失败 · 条目必须是对象")
                continue
            op = str(item.get("op", "")).strip()
            label = _batch_op_label(item)
            if op not in _BATCH_OPS:
                fail_n += 1
                lines.append(f"{i}. 失败 · {label}：未知 op")
                continue
            try:
                status, detail, landed = await self._run_one(op, item, context)
            except Exception as e:  # noqa: BLE001 — batch must continue
                dead = _stop_if_channel(e, start, products)
                if dead is not None:
                    return dead
                fail_n += 1
                lines.append(f"{i}. 失败 · {label}：{e}")
                continue
            dead = _stop_if_channel(detail, start, products) if status == "fail" else None
            if dead is not None:
                return dead
            if status == "ok":
                ok_n += 1
                lines.append(f"{i}. 成功 · {detail}")
                products.extend(landed)
            elif status == "skip":
                skip_n += 1
                lines.append(f"{i}. 跳过 · {detail}")
            else:
                fail_n += 1
                lines.append(f"{i}. 失败 · {detail}")

        summary = f"完成：成功 {ok_n}，跳过 {skip_n}，失败 {fail_n}"
        lines.append(summary)
        return ToolResult(
            tool_call_id="",
            success=fail_n == 0,
            output="\n".join(lines),
            error="" if fail_n == 0 else summary,
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "ok": ok_n,
                "skip": skip_n,
                "fail": fail_n,
                "total": len(raw),
            },
            # 部分成功也如实记账：只报真正落地的那几件（跳过 / 失败项没有产物）。
            file_products=products,
        )

    async def _run_one(
        self, op: str, item: dict[str, Any], context: ToolContext
    ) -> tuple[str, str, list[FileProduct]]:
        """Run one op → ``(status, detail, products)``.

        ``products`` is what this op actually LANDED (交付物台账自报契约，见
        ``tools/file_products.py``)：只有 move / copy 成功时才有一件，且报的是
        sanitize 之后真正落盘的 destination——批量工具一次产多件，逐件自报。
        搬家 / 复制不是派生（源不是中间稿），一律不填 ``derived_from``。
        mkdir 建的是目录、delete 是删除，都没有产物；skip / fail 更没有。
        """
        if op == "mkdir":
            requested = str(item.get("path", "")).strip()
            if not requested:
                return "fail", "mkdir · path 不能为空", []
            prepared = await prepared_write_relpath(requested, context)
            if isinstance(prepared, ToolResult):
                return "fail", prepared.error or "mkdir · 路径失败", []
            path, rename_note = prepared
            if not path:
                detail = "mkdir .（工作区根已存在）"
                if rename_note:
                    detail = f"{detail}。{rename_note}"
                return "ok", detail, []
            scope_err = write_scope_rejection(context, path)
            if scope_err is not None:
                logger.info(
                    "write.scope_rejected",
                    path=path,
                    write_scope=getattr(context, "write_scope", None),
                    op=op,
                )
                return "fail", scope_err, []
            try:
                await context.backend.mkdir(path)
            except AlreadyExists:
                return "skip", f"mkdir {path}（已存在）", []
            except OutsideWorkspace as e:
                return (
                    "fail",
                    _outside_workspace_msg(
                        path, location=context.backend.location, reason=str(e)
                    ),
                    [],
                )
            except WorkspaceError as e:
                return _workspace_item_fail(f"mkdir {path}", e)
            detail = f"mkdir {path}"
            if rename_note:
                detail = f"{detail}。{rename_note}"
            return "ok", detail, []

        if op == "delete":
            requested = str(item.get("path", "")).strip()
            if not requested:
                return "fail", "delete · path 不能为空", []
            prepared = await prepared_write_relpath(requested, context)
            if isinstance(prepared, ToolResult):
                return "fail", prepared.error or "delete · 路径失败", []
            path, rename_note = prepared
            if not path:
                return "fail", "delete · path 不能为空", []
            scope_err = write_scope_rejection(context, path)
            if scope_err is not None:
                logger.info(
                    "write.scope_rejected",
                    path=path,
                    write_scope=getattr(context, "write_scope", None),
                    op=op,
                )
                return "fail", scope_err, []
            # Leftover ``permanent`` still hard-deletes and still trips always-confirm.
            permanent = bool(item.get("permanent", False))
            try:
                await context.backend.delete(path, permanent=permanent)
            except PathNotFound:
                return "skip", f"delete {path}（不存在）", []
            except OutsideWorkspace as e:
                return (
                    "fail",
                    _outside_workspace_msg(
                        path, location=context.backend.location, reason=str(e)
                    ),
                    [],
                )
            except WorkspaceError as e:
                return _workspace_item_fail(f"delete {path}", e)
            mode = "永久删除" if permanent else "可逆删除"
            detail = f"delete {path}（{mode}）"
            if rename_note:
                detail = f"{detail}。{rename_note}"
            return "ok", detail, []

        source = str(item.get("source", "")).strip()
        requested_dest = str(item.get("destination", "")).strip()
        if not source or not requested_dest:
            return "fail", f"{op} · source 与 destination 均为必填", []
        prepared_dest = await prepared_write_relpath(requested_dest, context)
        if isinstance(prepared_dest, ToolResult):
            return "fail", prepared_dest.error or f"{op} · 目标路径失败", []
        destination, rename_note = prepared_dest
        src_mode: GrantMode = "readonly" if op == "copy" else "organize"
        prepared_src = await prepare_tool_path(source, context, grant_mode=src_mode)
        if isinstance(prepared_src, ToolResult):
            return "fail", prepared_src.error or f"{op} · 源路径失败", []
        source = prepared_src
        if not destination:
            return "fail", f"{op} · source 与 destination 均为必填", []
        if source == destination:
            # Same as cleaned dest (idempotent rename / no-op move).
            detail = f"{op} {source} → {destination}（源与目标相同，无需操作）"
            if rename_note:
                detail = f"{detail}。{rename_note}"
            # 幂等成功也自报：文件就在 destination 上。
            return "ok", detail, [file_product(destination)]
        for p in (source, destination) if op == "move" else (destination,):
            scope_err = write_scope_rejection(context, p)
            if scope_err is not None:
                logger.info(
                    "write.scope_rejected",
                    path=p,
                    write_scope=getattr(context, "write_scope", None),
                    op=op,
                )
                return "fail", scope_err, []
        try:
            if op == "move":
                await context.backend.move(source, destination)
            else:
                await context.backend.copy(source, destination)
        except PathNotFound:
            return "fail", f"{op} {source} → {destination}：源不存在", []
        except AlreadyExists:
            # MVP conflict policy: skip into report (提案钉死).
            return "skip", f"{op} {source} → {destination}：目标已存在，未覆盖", []
        except OutsideWorkspace as e:
            return (
                "fail",
                (
                    f"{op} {source} → {destination}："
                    + _outside_workspace_msg(
                        str(e), location=context.backend.location, reason=str(e)
                    )
                ),
                [],
            )
        except WorkspaceError as e:
            return _workspace_item_fail(f"{op} {source} → {destination}", e)
        detail = f"{op} {source} → {destination}"
        if rename_note:
            detail = f"{detail}。{rename_note}"
        return "ok", detail, [file_product(destination)]
