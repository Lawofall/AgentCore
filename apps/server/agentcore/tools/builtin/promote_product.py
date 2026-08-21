"""promote_product — 成品归位：把 CEO 认定的成品从 ``.agentcore`` 移进用户工作区。

AI 团队的产物默认落在 ``AgentCore/文档/``（工作稿 / research / reviews / debate）：
用户侧叫 **``.agentcore``**，用户不该去里面翻。收口前 CEO 用本工具把「用户真正要的那几份」搬进
工作区——**移动，不是标记**。标记在离开产品 UI 那刻即失效（打成 ZIP 里没有标记、
合回本机也没有），文件真在哪儿才是唯一跨得过边界的事实。

与交付（``delivered_files`` / ``file_acceptance``）正交：交付答**质量**（这份验收过
没过），归位答**位置**（这份是不是用户要的成品）。因此只有 ``accepted`` 的产物可归位，
而归位本身不改任何验收结论；判据取本回合最近一次 ``delivery_status``，本工具**不重算
验收**，读不到对账就诚实报错（见 :mod:`agentcore.runtime.delegate.promotion`）。

零归位是合法状态（多幕协作的中间幕）：本工具不参与收口闸门，调不调、搬几个都不拦
`finish_guard`；「收口须说清本轮归位了什么、可答无」是提示词层的事
（``.cursor/rules/intercept-discipline.mdc`` 阶梯 1）。目标已有同名文件 → 跳过并说明，
**绝不覆盖**：用户工作区里的同名文件可能是用户自己的。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.file_ops.errors import _outside_workspace_msg
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.protocol import (
    AlreadyExists,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)
from agentcore.workspace.stage_dirs import AGENTCORE_ROOT, DOCS_PREFIX
from agentcore.workspace.write_claims import normalize_ownership_path

logger = get_logger(__name__)

PROMOTE_PRODUCT_TOOL_NAME = "promote_product"

_DOCS_SOURCE_PREFIX = f"{DOCS_PREFIX}/"
_INTERNAL_DEST_PREFIX = f"{AGENTCORE_ROOT}/"
# 一次收口能搬的成品上限——够任何真实交付，又不至于把整个 `.agentcore` 倒进工作区根。
_MAX_PATHS = 24


def _norm(path: Any) -> str:
    """Workspace-relative form used for comparison —— 与台账改写同一把尺。

    用 ``normalize_ownership_path``（``promotion.promotion_key`` 也用它）才能保证「匹配
    得上就一定改写得到」：两边各归一各的，模型抄成 ``./a/b`` 时会出现搬了却没改写的
    悬空引用。空串保持空（调用方据此过滤无效入参）。
    """
    text = str(path or "").strip()
    return normalize_ownership_path(text) if text else ""


class PromoteProductTool:
    """Move accepted products out of the AI workspace into the user's workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
        # 真会落盘（搬进来的成品就是本回合交付物的新位置），自报 ``to`` 路径。
        file_products=FileProductsContract.SELF_REPORT,
    )

    def __init__(
        self,
        *,
        on_promoted: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """``on_promoted`` re-publishes the rewritten delivery reconciliation.

        A narrow callback, not the ``EventSink`` (引擎纯化, twin of ``on_note`` /
        ``on_escalate``): the tool decides *what moved*, the caller owns event
        shape. ``None`` (zero-arg construction for catalog / tests) simply skips
        the re-publish — the move and the ledger rewrite still happen.
        """
        self._on_promoted = on_promoted

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=PROMOTE_PRODUCT_TOOL_NAME,
            description=(
                "成品归位：把【路径已核】的成品从 `.agentcore`（盘上 AgentCore/文档/）"
                "【移动】到用户工作区，让用户一眼看见。收口前调用；"
                "先问用户要不要、下一轮再搬也可以（本会话此前批次路径已核的成品仍可归位）。\n"
                "- paths：要归位的产物路径（AgentCore/文档/ 下的相对路径，取自交付清单）。\n"
                "- dest：可选目标目录；省略 = 工作区根（裸聊 / 新建工作区首选）。"
                "代码仓等已有结构的工作区请指定子目录（如 docs/），避免污染根目录。\n"
                "只有交付对账里 status=accepted 的产物可归位；路径未核 / 不在 `.agentcore` / "
                "目标已存在同名文件的会被跳过并说明原因（【绝不覆盖】用户已有文件）。"
                "归位是移动：原路径之后不复存在，交付清单会同步改写到新路径。\n"
                "不搬也合法（多幕协作的中间幕常常无成品可交）——但收口时请明确说明"
                "本轮归位了哪些文件，没有就直说没有。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("要归位的产物路径列表（AgentCore/文档/ 下的相对路径）"),
                    },
                    "dest": {
                        "type": "string",
                        "description": ("目标目录（工作区相对路径）；省略则落在工作区根目录"),
                    },
                },
                "required": ["paths"],
            },
            category=ToolCategory.FILESYSTEM,
            # CEO 面不持 GRANTABLE：归位只在工作区内搬路径已核产物、不覆盖、不删除。
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        from agentcore.runtime.delegate.promotion import (
            has_delivery_reconciliation,
            hydrate_reconciliation_from_journal,
            promotable_paths,
            record_promotions,
        )

        start = time.monotonic()
        raw_paths = arguments.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            return _fail(
                "paths 不能为空：请列出要归位的产物路径（AgentCore/文档/ 下的相对路径）",
                start,
            )
        requested = [_norm(p) for p in raw_paths if _norm(p)]
        if not requested:
            return _fail("paths 里没有有效路径", start)
        if len(requested) > _MAX_PATHS:
            return _fail(
                f"一次最多归位 {_MAX_PATHS} 个成品（收到 {len(requested)} 个）。"
                "请只挑用户真正要的成品，其余留在 `.agentcore`。",
                start,
            )

        dest_dir, dest_error = _resolve_dest(arguments.get("dest"))
        if dest_error is not None:
            return _fail(dest_error, start)

        ledger = context.promotion_ledger
        # 本回合台账优先；取不到才回退本会话最近一条 durable 对账（「批次收尾 →
        # ask_user → 续跑归位」是主流路径，续跑换了新 ToolContext）。两者都是**已算好
        # 的验收结果**，本工具不重算，也不放行未验收路径。
        if not has_delivery_reconciliation(ledger):
            await hydrate_reconciliation_from_journal(
                ledger, conversation_id=context.conversation_id
            )
        if not has_delivery_reconciliation(ledger):
            return _fail(
                "本会话还没有交付对账，取不到「路径已核」清单，无法判断哪些产物可归位。"
                "请先完成派工并拿到交付状态后再归位；确无成品可交时，"
                "直接在收口里说明「本轮无成品归位」即可。",
                start,
            )

        # 台账原样 → 归一形：搬家按台账里那个字符串记 ``from``，改写才对得上（模型
        # 抄路径时的 `./` / `\` 差异只影响匹配，不该让台账留悬空引用）。
        accepted = {_norm(p): p for p in promotable_paths(ledger)}
        moves: list[tuple[str, str]] = []
        skipped: list[tuple[str, str]] = []
        seen: set[str] = set()

        for requested_path in requested:
            if requested_path in seen:
                skipped.append((requested_path, "重复出现，已按第一次处理"))
                continue
            seen.add(requested_path)
            reason = _ineligible_reason(requested_path, accepted)
            if reason is not None:
                skipped.append((requested_path, reason))
                continue
            source = accepted[requested_path]
            name = requested_path.rsplit("/", 1)[-1]
            target = f"{dest_dir}/{name}" if dest_dir else name
            try:
                if await context.backend.exists(target):
                    skipped.append((source, f"目标已存在同名文件 `{target}`，未覆盖"))
                    continue
                await context.backend.move(source, target)
            except AlreadyExists:
                skipped.append((source, f"目标已存在同名文件 `{target}`，未覆盖"))
                continue
            except PathNotFound:
                skipped.append((source, "源文件不存在（可能已被移动或删除）"))
                continue
            except OutsideWorkspace as e:
                skipped.append((
                    source,
                    _outside_workspace_msg(
                        target, location=context.backend.location, reason=str(e)
                    ),
                ))
                continue
            except WorkspaceError as exc:
                dead = _maybe_channel_dead(exc, start)
                if dead is not None:
                    return dead
                logger.warning(
                    "promote_product.move_failed",
                    source=source,
                    target=target,
                    error=str(exc),
                )
                skipped.append((source, "移动失败（工作区通道报错）"))
                continue
            moves.append((source, target))

        payload = record_promotions(ledger, moves)
        if payload is not None and self._on_promoted is not None:
            try:
                self._on_promoted(payload)
            except Exception:  # noqa: BLE001 — 台账已改写，重发失败不该让归位失败
                logger.warning("promote_product.republish_failed", exc_info=True)
        logger.info(
            "promote_product.done",
            promoted=len(moves),
            skipped=len(skipped),
            dest=dest_dir or "<root>",
        )
        return ToolResult(
            tool_call_id="",
            success=True,
            output=_receipt(moves, skipped),
            duration_ms=int((time.monotonic() - start) * 1000),
            # 搬家不是派生（源不是中间稿），只报落地路径，不填 derived_from。
            file_products=[file_product(dst) for _, dst in moves],
        )


def _ineligible_reason(path: str, accepted: dict[str, str]) -> str | None:
    """Why ``path`` cannot be promoted (``None`` = eligible)."""
    if not path.startswith(_DOCS_SOURCE_PREFIX):
        return (
            f"不在 `.agentcore`（`{_DOCS_SOURCE_PREFIX}` 下）"
            "——已在工作区里的文件无需归位"
        )
    if path not in accepted:
        return "不在本回合交付对账的路径已核清单里（路径未核或本回合未产出）"
    return None


def _resolve_dest(raw: Any) -> tuple[str, str | None]:
    """Sanitize the optional destination directory → ``(dest_dir, error)``.

    Empty / omitted ⇒ ``""`` = 工作区根（裸聊自动建桌场景用户第一眼即见）。
    """
    from agentcore.workspace._paths import sanitize_write_relpath

    if raw is None:
        return "", None
    if not isinstance(raw, str):
        return "", "dest 必须是字符串（工作区相对目录路径）"
    text = _norm(raw)
    if not text or text == ".":
        return "", None
    cleaned = _norm(sanitize_write_relpath(text))
    if not cleaned or cleaned == "." or cleaned.startswith("../") or cleaned == "..":
        return "", f"dest `{raw}` 不是合法的工作区相对目录"
    if cleaned == AGENTCORE_ROOT or cleaned.startswith(_INTERNAL_DEST_PREFIX):
        return "", (
            f"dest 不能落在 `{_INTERNAL_DEST_PREFIX}` 内——那是 `.agentcore`，"
            "归位的目的正是把成品搬出来。请给用户工作区里的目录，或省略 dest 落在根目录。"
        )
    return cleaned, None


def _receipt(moves: list[tuple[str, str]], skipped: list[tuple[str, str]]) -> str:
    """Model-facing receipt: what landed where, what did not, and why."""
    lines: list[str] = []
    if moves:
        lines.append(f"已归位 {len(moves)} 个成品（原路径已不存在）：")
        lines.extend(f"- {src} → {dst}" for src, dst in moves)
    else:
        lines.append("本次没有归位任何成品。")
    if skipped:
        lines.append(f"跳过 {len(skipped)} 个：")
        lines.extend(f"- {src}：{reason}" for src, reason in skipped)
    lines.append(
        "收口时请说明本轮归位了哪些文件（用上面的新路径）；确实没有可归位的成品就直说没有。"
    )
    return "\n".join(lines)


def _fail(message: str, start: float) -> ToolResult:
    """Argument / precondition rejection the CEO can fix by changing the call."""
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=message,
        duration_ms=int((time.monotonic() - start) * 1000),
        contract_failure=True,
        failure_message=message,
    )


def _maybe_channel_dead(exc: WorkspaceError, start: float) -> ToolResult | None:
    """Reuse the shared workspace-liveness mapping (sticky dead / op timeout)."""
    from agentcore.tools.builtin.file_ops.errors import _maybe_channel_dead_error

    return _maybe_channel_dead_error(exc, start)
