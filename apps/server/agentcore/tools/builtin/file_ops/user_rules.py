"""Overlay: ``.agentcore/rules/`` file_* calls land on user-rule documents, not disk."""

from __future__ import annotations

import time
from typing import Any

from agentcore.account.credentials import AccountCloudError, get_account_credentials
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import DocumentRepository
from agentcore.memory.always_quota import AlwaysQuotaExceededError
from agentcore.memory.rule_files import (
    RULE_TREE_META_MSG,
    RULES_CATALOG_ROOT,
    RULES_DIR_REL,
    classify_rule_path,
    rule_entry_relpath,
)
from agentcore.memory.rules_injection import UserRuleMutationResult, mutate_user_rule
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.protocol import DirEntry
from agentcore.workspace.text_replace import (
    TextReplaceAmbiguous,
    TextReplaceNoMatch,
    TextReplaceOk,
    apply_text_replace,
)

from .errors import _error, _path_missing_error
from .integrity import (
    _mark_landed_files,
    _norm_rel_path,
    _reject_write_scope,
    classify_write_kind,
    format_artifact_manifest,
)
from .listing import empty_list_message, format_ls_lines

logger = get_logger(__name__)


def rule_folder_id(context: ToolContext) -> str | None:
    desk = (context.ownership_desk_id or "").strip() or None
    if desk:
        return desk
    auto = (context.auto_desk_folder_id or "").strip() or None
    return auto


def _result_from_cloud(
    payload: dict[str, Any],
    *,
    action: str,
    name: str,
    content: str,
) -> UserRuleMutationResult:
    catalog_raw = payload.get("catalog") or []
    catalog: tuple[tuple[str, str, str], ...] = ()
    if isinstance(catalog_raw, list):
        catalog = tuple(
            (
                str(item.get("name") or ""),
                str(item.get("apply") or ""),
                str(item.get("description") or ""),
            )
            for item in catalog_raw
            if isinstance(item, dict)
        )
    result = UserRuleMutationResult(
        action=str(payload.get("action") or action),
        changed=bool(payload.get("changed")),
        message=str(payload.get("message") or ""),
        name=str(payload.get("name") or name),
        apply=str(payload.get("apply") or ""),
        body=str(payload.get("body") or ""),
        catalog=catalog,
        content=content or None,
        ok=payload.get("ok") is not False,
    )
    if result.message:
        return result
    fallback: str
    if result.action == "list":
        fallback = result.message or "当前没有用户规则。"
    elif result.changed:
        fallback = f"已更新用户规则（action={result.action}）。"
    else:
        fallback = "用户规则未变更。"
    return UserRuleMutationResult(
        action=result.action,
        changed=result.changed,
        message=fallback,
        name=result.name,
        apply=result.apply,
        body=result.body,
        catalog=result.catalog,
        content=result.content,
        ok=result.ok,
    )


def _catalog_from_list_payload(
    payload: dict[str, Any], *, folder_id: str | None
) -> tuple[tuple[str, str, str], ...]:
    if folder_id:
        keys = ("project_rules", "project_on_demand_rules", "project_path_rules")
    else:
        keys = ("global_rules", "global_on_demand_rules", "global_path_rules")
    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    for key in keys:
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append((name, "", str(item.get("description") or "")))
    return tuple(rows)


async def _rewarm_account_rules_memory(
    creds: Any,
    *,
    user_id: str,
    folder_id: str | None,
) -> None:
    try:
        from agentcore.memory.account_prepare_cache import warm_account_rules_memory

        await warm_account_rules_memory(creds, user_id=user_id, folder_id=folder_id)
    except Exception as e:  # noqa: BLE001 — write already succeeded
        logger.warning(
            "memory.user_rule_rewarm_failed",
            user_id=user_id,
            folder_id=folder_id,
            error=str(e),
        )


async def _mutate(
    context: ToolContext,
    *,
    action: str,
    name: str = "",
    content: str = "",
) -> UserRuleMutationResult:
    folder_id = rule_folder_id(context)
    creds = get_account_credentials()
    if creds is not None:
        from agentcore.account.credentials import (
            cloud_delete_user_rule,
            cloud_list_user_rules,
            cloud_read_user_rule,
            cloud_write_user_rule,
        )

        if action == "write":
            payload = await cloud_write_user_rule(
                creds,
                name=name,
                content=content,
                folder_id=folder_id,
            )
            return _result_from_cloud(payload, action=action, name=name, content=content)
        if action == "read":
            payload = await cloud_read_user_rule(
                creds, name=name, folder_id=folder_id
            )
            return _result_from_cloud(payload, action=action, name=name, content=content)
        if action == "delete":
            payload = await cloud_delete_user_rule(
                creds, name=name, folder_id=folder_id
            )
            return _result_from_cloud(payload, action=action, name=name, content=content)
        payload = await cloud_list_user_rules(creds, folder_id=folder_id)
        catalog = _catalog_from_list_payload(payload, folder_id=folder_id)
        if not catalog:
            message = "当前没有用户规则。"
        else:
            lines = ["当前用户规则："]
            lines.extend(f"- {n}" for n, _a, _d in catalog)
            message = "\n".join(lines)
        return UserRuleMutationResult(
            action="list",
            changed=False,
            message=message,
            catalog=catalog,
            ok=True,
        )

    async with async_session_factory() as session:
        return await mutate_user_rule(
            DocumentRepository(session),
            context.user_id,
            folder_id=folder_id,
            action=action,
            name=name or None,
            content=content or None,
        )


def _mutate_error(exc: BaseException, start: float) -> ToolResult:
    if isinstance(exc, AlwaysQuotaExceededError):
        return _error(exc.message, start, contract_failure=True)
    if isinstance(exc, AccountCloudError):
        if exc.code == "ALWAYS_QUOTA_EXCEEDED":
            return _error(exc.message, start, contract_failure=True)
        logger.warning("memory.user_rule_failed", error=str(exc))
        return _error("用户规则写入失败，请稍后再试。", start)
    logger.warning("memory.user_rule_failed", error=str(exc))
    return _error("用户规则写入失败，请稍后再试。", start)


async def maybe_user_rule_write(
    *,
    requested_path: str,
    content: str,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    kind, name = classify_rule_path(requested_path)
    if kind is None:
        return None
    if kind in ("agentcore_root", "rules_dir", "invalid") or not name:
        return _error(
            f"用户规则必须是 {RULES_DIR_REL} 下的一篇 markdown（一个主题一篇）。",
            start,
            contract_failure=True,
        )
    scope_denied = _reject_write_scope(context, requested_path, start)
    if scope_denied is not None:
        return scope_denied
    body = content if isinstance(content, str) else str(content or "")
    if not body.strip():
        return _error("缺少 content。", start, contract_failure=True)
    rel_path = rule_entry_relpath(name)
    try:
        result = await _mutate(context, action="write", name=name, content=body)
    except Exception as e:  # noqa: BLE001
        return _mutate_error(e, start)
    if not result.ok:
        return _error(result.message, start, contract_failure=True)
    if result.changed:
        logger.info(
            "memory.user_rule_written",
            user_id=context.user_id,
            action="write",
            name=result.name or name,
        )
        creds = get_account_credentials()
        if creds is not None:
            await _rewarm_account_rules_memory(
                creds,
                user_id=context.user_id,
                folder_id=rule_folder_id(context),
            )
    kind_label = classify_write_kind(body)
    output = format_artifact_manifest(
        path=rel_path,
        content=body,
        chars_written=len(body),
        kind=kind_label,
        action="write",
    )
    if result.message:
        output = f"{output}\n{result.message}"
    _mark_landed_files(context, _norm_rel_path(rel_path), kind=kind_label)
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
        file_products=[file_product(rel_path)],
        metadata={"already_applied": not result.changed},
    )


async def maybe_user_rule_str_replace(
    *,
    requested_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    kind, name = classify_rule_path(requested_path)
    if kind is None:
        return None
    if kind != "rule_file" or not name:
        return _error(
            f"用户规则必须是 {RULES_DIR_REL} 下的一篇 markdown。",
            start,
            contract_failure=True,
        )
    scope_denied = _reject_write_scope(context, requested_path, start)
    if scope_denied is not None:
        return scope_denied
    rel_path = rule_entry_relpath(name)
    try:
        current = await _mutate(context, action="read", name=name)
    except Exception as e:  # noqa: BLE001
        return _mutate_error(e, start)
    if not current.ok or not current.body:
        return _path_missing_error(f"路径不存在：{rel_path}", start, path=rel_path)
    applied = apply_text_replace(
        current.body, old_string, new_string, all_=replace_all
    )
    if isinstance(applied, TextReplaceNoMatch):
        return _error(
            f"在 {rel_path} 中找不到 old_string；它必须与规则正文完全一致，"
            "包括空白与缩进。请先 read 再 edit。",
            start,
            contract_failure=True,
        )
    if isinstance(applied, TextReplaceAmbiguous):
        return _error(
            f"{rel_path} 中 old_string 出现 {applied.count} 次，默认只改一处。"
            "请加上下文使锚唯一，或设 replace_all=true。",
            start,
            contract_failure=True,
        )
    if not isinstance(applied, TextReplaceOk):
        return _error("替换失败。", start)
    try:
        result = await _mutate(
            context, action="write", name=name, content=applied.content
        )
    except Exception as e:  # noqa: BLE001
        return _mutate_error(e, start)
    if not result.ok:
        return _error(result.message, start, contract_failure=True)
    if result.changed:
        creds = get_account_credentials()
        if creds is not None:
            await _rewarm_account_rules_memory(
                creds,
                user_id=context.user_id,
                folder_id=rule_folder_id(context),
            )
    kind_label = classify_write_kind(applied.content)
    output = format_artifact_manifest(
        path=rel_path,
        content=applied.content,
        chars_written=len(applied.content),
        kind=kind_label,
        action="write",
    )
    _mark_landed_files(context, _norm_rel_path(rel_path), kind=kind_label)
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
        file_products=[file_product(rel_path)],
        metadata={"replacements": applied.count},
    )


async def maybe_user_rule_delete(
    *,
    requested_path: str,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    kind, name = classify_rule_path(requested_path)
    if kind is None:
        return None
    if kind != "rule_file" or not name:
        return _error(RULE_TREE_META_MSG, start, contract_failure=True)
    scope_denied = _reject_write_scope(context, requested_path, start)
    if scope_denied is not None:
        return scope_denied
    rel_path = rule_entry_relpath(name)
    try:
        result = await _mutate(context, action="delete", name=name)
    except Exception as e:  # noqa: BLE001
        return _mutate_error(e, start)
    if not result.ok:
        return _path_missing_error(f"路径不存在：{rel_path}", start, path=rel_path)
    if result.changed:
        creds = get_account_credentials()
        if creds is not None:
            await _rewarm_account_rules_memory(
                creds,
                user_id=context.user_id,
                folder_id=rule_folder_id(context),
            )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=result.message or f"已删除 {rel_path}",
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def maybe_user_rule_read(
    *,
    requested_path: str,
    offset: object,
    limit: object,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    kind, name = classify_rule_path(requested_path)
    if kind is None:
        return None
    if kind != "rule_file" or not name:
        return _error(
            f"{requested_path} 不是一篇用户规则。请读 {RULES_DIR_REL}/ 下的 markdown。",
            start,
            contract_failure=True,
        )
    rel_path = rule_entry_relpath(name)
    try:
        result = await _mutate(context, action="read", name=name)
    except Exception as e:  # noqa: BLE001
        return _mutate_error(e, start)
    if not result.ok or not result.body:
        return _path_missing_error(f"路径不存在：{rel_path}", start, path=rel_path)
    from .read import _file_read_ok, _format_line_window, _select_line_window

    lines = result.body.splitlines()
    selected, start_line, end_line, total, cap_kind = _select_line_window(
        lines, offset=offset, limit=limit
    )
    output = _format_line_window(
        selected,
        start_line=start_line,
        end_line=end_line,
        total_lines=total,
        cap_kind=cap_kind,
    )
    return _file_read_ok(output, start)


async def maybe_user_rule_list(
    *,
    directory: str,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    kind, _name = classify_rule_path(directory)
    if kind is None:
        return None
    if kind == "invalid":
        return _error(RULE_TREE_META_MSG, start, contract_failure=True)
    if kind != "rules_dir":
        return None
    try:
        result = await _mutate(context, action="list")
    except Exception as e:  # noqa: BLE001
        return _mutate_error(e, start)
    entries = [
        DirEntry(path=rule_entry_relpath(name), is_dir=False)
        for name, _apply, _desc in result.catalog
        if name
    ]
    output = format_ls_lines(entries) if entries else empty_list_message(RULES_DIR_REL)
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def merge_rule_dir_entries(directory: str, entries: list[DirEntry]) -> list[DirEntry]:
    """Ensure ``.agentcore/rules`` appears when listing ``.agentcore``."""
    kind, _name = classify_rule_path(directory)
    if kind != "agentcore_root":
        return entries
    needle = RULES_DIR_REL.replace("\\", "/")
    for entry in entries:
        path = (entry.path or "").replace("\\", "/").rstrip("/")
        if path == needle or path.endswith("/rules") or path == "rules":
            return entries
    merged = list(entries)
    merged.append(DirEntry(path=RULES_DIR_REL, is_dir=True))
    merged.sort(key=lambda e: e.path.replace("\\", "/").lower())
    return merged


def synthetic_agentcore_listing(start: float) -> ToolResult:
    """``.agentcore`` is not on disk — still show the virtual rules dir."""
    output = format_ls_lines([DirEntry(path=RULES_DIR_REL, is_dir=True)])
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def maybe_user_rule_mkdir(
    *,
    requested_path: str,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    kind, _name = classify_rule_path(requested_path)
    if kind is None:
        return None
    if kind not in ("agentcore_root", "rules_dir"):
        return _error(RULE_TREE_META_MSG, start, contract_failure=True)
    scope_denied = _reject_write_scope(context, requested_path, start)
    if scope_denied is not None:
        return scope_denied
    rel = RULES_DIR_REL if kind == "rules_dir" else RULES_CATALOG_ROOT
    return ToolResult(
        tool_call_id="",
        success=True,
        output=f"目录已就绪：{rel}（用户规则不落工作区盘）",
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def maybe_user_rule_copy_or_move(source: str, destination: str) -> str | None:
    """Reject copy/move that touches the virtual rules tree."""
    src_kind, _s = classify_rule_path(source)
    dst_kind, _d = classify_rule_path(destination)
    if src_kind is None and dst_kind is None:
        return None
    return RULE_TREE_META_MSG


def batch_op_rule_block(op: str, item: dict[str, Any]) -> str | None:
    """Fail a file_batch item that targets the virtual rules tree (except mkdir)."""
    if op == "mkdir":
        kind, _name = classify_rule_path(str(item.get("path") or ""))
        if kind is None:
            return None
        if kind in ("agentcore_root", "rules_dir"):
            return None
        return RULE_TREE_META_MSG
    if op == "delete":
        kind, _name = classify_rule_path(str(item.get("path") or ""))
        if kind is None:
            return None
        return RULE_TREE_META_MSG
    blocked = maybe_user_rule_copy_or_move(
        str(item.get("source") or ""),
        str(item.get("destination") or ""),
    )
    return blocked
