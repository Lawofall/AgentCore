"""remember — the user gives an explicit directive, so the CEO records it as a USER RULE.

The memory system splits how durable knowledge is written (Agent记忆与知识系统 §5.7 用户规则
入口① / §1.5 显式记住例外):

- **explicit user directive → user rule** (this tool): when the user clearly says「记住…」「以后
  都要…」「别再…」「改为…」「忘掉…」, the CEO records / mutates a ``role='rule',
  ai_maintained=false`` document — the user OWNS it, so the offline consolidation never rewrites
  it, and it injects with authoritative wording ahead of AI memory (§二 两档措辞). Effect is
  immediate: next turn's ``<rules>``.
- **inferred preference → offline consolidation** (NOT this tool): preferences merely observed in
  conversation stay with the two-layer consolidation pass, which writes ``ai_maintained=true``
  memory. The tool description steers the model to that split.

Same master-switch neutrality as user rules generally: a user rule is the user's own instruction,
not AI memory, so it is recorded whenever the user asks — turning off「AI 记忆」silences AI-grown
memory, not the user's explicit rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.account.credentials import AccountCloudError
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import DocumentRepository
from agentcore.memory.always_quota import AlwaysQuotaExceededError
from agentcore.memory.rules_injection import UserRuleMutationResult, mutate_user_rule
from agentcore.tools.builtin.file_ops import has_omission_marker
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

# Trailing ellipsis suffixes (check longer first). Distinct from mid-body
# ``has_omission_marker`` — bare ``...`` / ``…`` / ``……`` at end only.
_TRAILING_ELLIPSIS_SUFFIXES = ("……", "...", "…")
_INCOMPLETE_CONTENT_MSG = (
    "拒绝写入：规则正文不完整。请写完整一句陈述句，勿用省略号收口或中间省略标记。"
)


def _is_incomplete_rule_content(content: str) -> bool:
    """True when add/replace content looks truncated (mid markers or trailing ellipsis)."""
    if not content:
        return False
    if has_omission_marker(content):
        return True
    return any(content.endswith(s) for s in _TRAILING_ELLIPSIS_SUFFIXES)


@dataclass
class RememberTool:
    """CEO-only: record / mutate an explicit user directive as a user rule (immediate effect)."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.MEMORY,
    )

    # The conversation's folder (None for a bare chat). A ``scope='folder'`` directive routes
    # the rule to this folder's layer; without a folder it stays global.
    folder_id: str | None = None

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="remember",
            description=(
                "把用户明确下达的指令记为「用户规则」——长期生效、注入后续每一轮对话，"
                "权威性高于 AI 记忆。仅当用户清楚地说「记住…」「以后都要…」「以后别…」"
                "「改为…」「忘掉…」「现在有哪些规则」等明确指令时使用；普通对话里推测出来的偏好"
                "不要用本工具，交给会话结束后的离线巩固。"
                "action：add 追加（默认）；replace 按 replaces 归一化匹配删旧再写新"
                "（旧条不存在则只追加，且须诚实说明）；"
                "forget 删除；list 列出当前作用域规则（不写盘）。"
                "写入/删除后立即生效，下一轮对话即注入。"
                "禁止把文件夹调研简报 / 技术栈盘点 / 探索幕产出写成规则——"
                "那是文件夹画像，须用 update_folder_profile。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "forget", "list"],
                        "description": (
                            "add=追加（默认）；replace=替换旧条；forget=删除；list=列出当前规则。"
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "规则正文（一句陈述句，用用户的语言）。"
                            "add/replace 为新规则；forget 为要删的旧规则；list 不需要。"
                        ),
                    },
                    "replaces": {
                        "type": "string",
                        "description": (
                            "replace 时要去掉的旧规则原文（归一化匹配；可删掉所有同 key 条）。"
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "folder"],
                        "description": (
                            "global=对所有对话生效（默认）；folder=仅当前文件夹生效。"
                        ),
                    },
                },
                "required": [],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = str(arguments.get("action") or "add").strip().lower() or "add"
        content_raw = arguments.get("content")
        content = str(content_raw).strip() if content_raw is not None else ""
        replaces_raw = arguments.get("replaces")
        replaces = str(replaces_raw).strip() if replaces_raw is not None else None
        scope_token = str(arguments.get("scope") or "global").strip().lower()
        # folder scope only when the conversation is actually in a folder; else global.
        folder_id = self.folder_id if scope_token == "folder" and self.folder_id else None

        if action != "list" and not content:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="缺少 content。",
                error="缺少 content。",
            )
        # Content integrity (add/replace only): reject half-finished rules.
        # list/forget are unaffected (forget has no "new rule body" semantics).
        if action in ("add", "replace") and _is_incomplete_rule_content(content):
            return ToolResult(
                tool_call_id="",
                success=False,
                output=_INCOMPLETE_CONTENT_MSG,
                error=_INCOMPLETE_CONTENT_MSG,
            )
        if action == "replace" and not (replaces or "").strip():
            return ToolResult(
                tool_call_id="",
                success=False,
                output="replace 需要 replaces（要替换掉的旧规则）。",
                error="replace 需要 replaces。",
            )

        creds = None
        try:
            from agentcore.account.credentials import (
                cloud_remember_rule,
                get_account_credentials,
            )

            creds = get_account_credentials()
            if creds is not None:
                payload = await cloud_remember_rule(
                    creds,
                    content=content or None,
                    folder_id=folder_id,
                    action=action,
                    replaces=replaces,
                )
                result = UserRuleMutationResult(
                    action=str(payload.get("action") or action),
                    changed=bool(payload.get("changed")),
                    message=str(payload.get("message") or ""),
                    markdown=str(payload.get("rules_markdown") or "")
                    if action == "list"
                    else "",
                    content=content or None,
                )
                if not result.message:
                    result = UserRuleMutationResult(
                        action=result.action,
                        changed=result.changed,
                        message=_fallback_cloud_message(result),
                        markdown=result.markdown,
                        content=result.content,
                    )
            else:
                async with async_session_factory() as session:
                    result = await mutate_user_rule(
                        DocumentRepository(session),
                        context.user_id,
                        folder_id=folder_id,
                        action=action,
                        content=content or None,
                        replaces=replaces,
                    )
        except AlwaysQuotaExceededError as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                output=e.message,
                error=e.message,
            )
        except AccountCloudError as e:
            if e.code == "ALWAYS_QUOTA_EXCEEDED":
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=e.message,
                    error=e.message,
                )
            logger.warning("memory.remember_failed", user_id=context.user_id, error=str(e))
            return ToolResult(
                tool_call_id="",
                success=False,
                output="记住失败，请稍后再试。",
                error=str(e),
            )
        except Exception as e:  # noqa: BLE001 - a tool failure must not crash the turn
            logger.warning("memory.remember_failed", user_id=context.user_id, error=str(e))
            return ToolResult(
                tool_call_id="",
                success=False,
                output="记住失败，请稍后再试。",
                error=str(e),
            )

        if result.message.startswith("不支持的 action") or result.message.startswith(
            "缺少 content"
        ) or result.message.startswith("replace 需要"):
            return ToolResult(
                tool_call_id="",
                success=False,
                output=result.message,
                error=result.message,
            )

        if result.changed:
            logger.info(
                "memory.remember_written",
                user_id=context.user_id,
                scope="folder" if folder_id else "global",
                action=result.action,
            )
            # Ticketed turns inject from the prepare snapshot; re-seed the
            # conversation folder's key so the next turn sees this write.
            if creds is not None:
                await _rewarm_account_rules_memory(
                    creds,
                    user_id=context.user_id,
                    folder_id=self.folder_id,
                )

        display: dict[str, Any] = {
            "remembered": result.changed and result.action == "add",
            "changed": result.changed,
            "action": result.action,
            "content": content or None,
            "kind": "user_rule",
        }
        if result.removed:
            display["removed"] = list(result.removed)
        if result.action == "list":
            display["rules_markdown"] = result.markdown

        return ToolResult(
            tool_call_id="",
            success=True,
            output=result.message,
            display=display,
        )


def _fallback_cloud_message(result: UserRuleMutationResult) -> str:
    if result.action == "list":
        body = (result.markdown or "").strip()
        return f"当前用户规则：\n{body}" if body else "当前暂无用户规则。"
    if result.changed:
        return f"已更新用户规则（action={result.action}）。"
    return "用户规则未变更。"


async def _rewarm_account_rules_memory(
    creds: Any,
    *,
    user_id: str,
    folder_id: str | None,
) -> None:
    """Best-effort re-seed of the ticketed prepare snapshot after a rule write."""
    try:
        from agentcore.memory.account_prepare_cache import warm_account_rules_memory

        await warm_account_rules_memory(creds, user_id=user_id, folder_id=folder_id)
    except Exception as e:  # noqa: BLE001 — remember already succeeded
        logger.warning(
            "memory.remember_rewarm_failed",
            user_id=user_id,
            folder_id=folder_id,
            error=str(e),
        )


def build_remember_tool(*, folder_id: str | None = None) -> RememberTool:
    return RememberTool(folder_id=folder_id)
