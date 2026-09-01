"""file_batch tool (multi op move/copy/delete/mkdir)."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.file_products import FileProduct, file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.limits import is_presence_disconnected_detail
from agentcore.workspace.protocol import (
    AlreadyExists,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

from .errors import _error, _liveness_workspace_error, _outside_workspace_msg
from .integrity import _prepare_write_relpath, write_scope_rejection

logger = get_logger(__name__)

_BATCH_OPS = frozenset({"move", "copy", "delete", "mkdir"})
_BATCH_MAX_OPS = 50


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
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_batch",
            description=(
                "一次提交多条工作区文件操作（move / copy / delete / mkdir）。"
                f"最多 {_BATCH_MAX_OPS} 项。逐项执行：单项失败不中断整批，回执如实"
                "列出成功 / 跳过 / 失败。目标同名冲突 = 跳过并入报告。"
                "整理方案确认后传入 organize_plan_id：仅允许方案内条目，且跳过二次审批。"
                "删除默认可逆；区外 permanent=true 一律拒绝。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "description": "按顺序执行的操作列表",
                        "minItems": 1,
                        "maxItems": _BATCH_MAX_OPS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": ["move", "copy", "delete", "mkdir"],
                                    "description": "操作类型",
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
                                    "description": "move / copy 的目标相对路径",
                                },
                                "permanent": {
                                    "type": "boolean",
                                    "description": "仅 delete：true = 永久删除（区外禁止）",
                                    "default": False,
                                },
                            },
                            "required": ["op"],
                        },
                    },
                    "organize_plan_id": {
                        "type": "string",
                        "description": (
                            "整理方案卡确认后返回的 plan_id。携带时：范围校验仅允许方案内"
                            "条目，并跳过 GRANTABLE 二次审批；执行成功项写入可撤销日志。"
                        ),
                    },
                    "organize_undo": {
                        "type": "boolean",
                        "description": (
                            "true = 撤销本会话最近一次整理（逆回放 move/mkdir；删除项只提示"
                            "去回收站）。单次有效。勿与 operations / organize_plan_id 同用。"
                        ),
                        "default": False,
                    },
                },
                "required": [],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        if bool(arguments.get("organize_undo")):
            return await self._undo(context, start)

        raw = arguments.get("operations")
        if not isinstance(raw, list) or not raw:
            return _error("operations 必须是非空数组（撤销请用 organize_undo=true）", start)
        if len(raw) > _BATCH_MAX_OPS:
            return _error(f"operations 最多 {_BATCH_MAX_OPS} 项", start)

        plan_id = str(arguments.get("organize_plan_id") or "").strip()
        if plan_id:
            from agentcore.workspace.organize_plan_store import get_plan, ops_within_plan

            plan = get_plan(plan_id)
            if plan is None or plan.conversation_id != context.conversation_id:
                return _error(f"整理方案不存在或已失效：{plan_id}", start)
            scope_err = ops_within_plan(plan, [i for i in raw if isinstance(i, dict)])
            if scope_err:
                return _error(scope_err, start)

        lines: list[str] = [f"本次共 {len(raw)} 项："]
        ok_n = skip_n = fail_n = 0
        successes: list[dict[str, Any]] = []
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
                fail_n += 1
                lines.append(f"{i}. 失败 · {label}：{e}")
                continue
            if status == "fail" and is_presence_disconnected_detail(detail):
                # Presence disconnect: stop the batch and stamp family retire.
                dead = _liveness_workspace_error(detail, start)
                # 中途中断不抹账：前面成功的那几件确实躺在盘上（漏账才是事故）。
                dead.file_products = products
                return dead
            if status == "ok":
                ok_n += 1
                lines.append(f"{i}. 成功 · {detail}")
                successes.append(item)
                products.extend(landed)
            elif status == "skip":
                skip_n += 1
                lines.append(f"{i}. 跳过 · {detail}")
            else:
                fail_n += 1
                lines.append(f"{i}. 失败 · {detail}")

        if plan_id and successes:
            from agentcore.workspace import organize_journal

            organize_journal.record_batch(
                conversation_id=context.conversation_id,
                plan_id=plan_id,
                successes=successes,
            )
            lines.append(
                f"已记录整理日志（plan={plan_id}）。可用 file_batch(organize_undo=true) 撤销"
                "本次 move/mkdir；删除项请到系统回收站手动恢复。"
            )

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
                "organize_plan_id": plan_id or None,
            },
            # 部分成功也如实记账：只报真正落地的那几件（跳过 / 失败项没有产物）。
            file_products=products,
        )

    async def _undo(self, context: ToolContext, start: float) -> ToolResult:
        from agentcore.workspace import organize_journal
        from agentcore.workspace.organize_plan_store import deactivate_plan

        journal = organize_journal.get_journal(context.conversation_id)
        if journal is None:
            return _error("没有可撤销的整理记录", start)
        if journal.undone:
            return _error("本次整理已撤销过（仅单次有效）", start)
        undo_ops, deletes = organize_journal.build_undo_operations(journal)
        lines: list[str] = ["撤销本次整理："]
        ok_n = skip_n = fail_n = 0
        products: list[FileProduct] = []
        for i, item in enumerate(undo_ops, start=1):
            op = str(item.get("op", "")).strip()
            try:
                status, detail, landed = await self._run_one(op, item, context)
            except Exception as e:  # noqa: BLE001
                fail_n += 1
                lines.append(f"{i}. 失败 · {e}")
                continue
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
        if deletes:
            lines.append(
                "以下删除项未自动还原，请到系统回收站手动恢复：\n"
                + "\n".join(f"- {p}" for p in deletes)
            )
        organize_journal.mark_undone(context.conversation_id)
        deactivate_plan(journal.plan_id)
        summary = f"撤销完成：成功 {ok_n}，跳过 {skip_n}，失败 {fail_n}"
        lines.append(summary)
        return ToolResult(
            tool_call_id="",
            success=fail_n == 0,
            output="\n".join(lines),
            error="" if fail_n == 0 else summary,
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={"ok": ok_n, "skip": skip_n, "fail": fail_n, "undo": True},
            # 逆回放也是搬家：文件此刻落在还原后的路径上，与正向 move 同口径自报。
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
            path, rename_note = await _prepare_write_relpath(
                requested, context, register_bare=True
            )
            if not path:
                detail = "mkdir .（工作区根已存在）"
                if rename_note:
                    detail = f"{detail}。{rename_note}"
                return "ok", detail, []
            scope_err = write_scope_rejection(context, path)
            if scope_err is not None:
                logger.info(
                    "file_write.scope_rejected",
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
                return "fail", f"mkdir {path}：{e}", []
            detail = f"mkdir {path}"
            if rename_note:
                detail = f"{detail}。{rename_note}"
            return "ok", detail, []

        if op == "delete":
            requested = str(item.get("path", "")).strip()
            if not requested:
                return "fail", "delete · path 不能为空", []
            path, rename_note = await _prepare_write_relpath(
                requested, context, register=False
            )
            if not path:
                return "fail", "delete · path 不能为空", []
            scope_err = write_scope_rejection(context, path)
            if scope_err is not None:
                logger.info(
                    "file_write.scope_rejected",
                    path=path,
                    write_scope=getattr(context, "write_scope", None),
                    op=op,
                )
                return "fail", scope_err, []
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
                return "fail", f"delete {path}：{e}", []
            mode = "永久删除" if permanent else "可逆删除"
            detail = f"delete {path}（{mode}）"
            if rename_note:
                detail = f"{detail}。{rename_note}"
            return "ok", detail, []

        source = str(item.get("source", "")).strip()
        requested_dest = str(item.get("destination", "")).strip()
        if not source or not requested_dest:
            return "fail", f"{op} · source 与 destination 均为必填", []
        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        # Dest first: empty-desk first shot may register; source then shares that slug.
        destination, rename_note = await _prepare_write_relpath(requested_dest, context)
        source, _src_note = await rewrite_project_shell_relpath(
            source, context, register=False
        )
        if not destination:
            return "fail", f"{op} · source 与 destination 均为必填", []
        if source == destination:
            # Same as cleaned dest (e.g. flat → nested dossier request): idempotent OK.
            detail = f"{op} {source} → {destination}（源与目标相同，无需操作）"
            if rename_note:
                detail = f"{detail}。{rename_note}"
            # 幂等成功也自报：文件就在 destination 上，与单支 file_move / file_copy 同口径。
            return "ok", detail, [file_product(destination)]
        for p in (source, destination) if op == "move" else (destination,):
            scope_err = write_scope_rejection(context, p)
            if scope_err is not None:
                logger.info(
                    "file_write.scope_rejected",
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
            return "skip", f"{op} {source} → {destination}：目标已存在", []
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
            return "fail", f"{op} {source} → {destination}：{e}", []
        detail = f"{op} {source} → {destination}"
        if rename_note:
            detail = f"{detail}。{rename_note}"
        return "ok", detail, [file_product(destination)]
