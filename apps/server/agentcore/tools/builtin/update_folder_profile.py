"""update_folder_profile — CEO explore-act close-out writes folder memory.

Product exception to §1.5: mid-turn write of ``ai_maintained=true`` folder profile,
optional ``导航.md``, and optional folder ``主题/<slug>.md``. ``remember`` stays
user-rules-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.memory.explore_profile import (
    MAX_EXPLORE_TOPICS,
    compute_workspace_explore_fingerprint,
    filter_topics_by_scope_cap,
    parse_explore_topics,
    record_explore_closeout,
    resolve_folder_workspace_key,
    write_folder_navigation,
    write_folder_profile_cas,
    write_folder_topics_replace,
)
from agentcore.memory.store import MemoryStore, default_memory_store
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

UPDATE_FOLDER_PROFILE_TOOL_NAME = "update_folder_profile"

_OUTPUT_LIMIT = 12000

_PROFILE_UPDATED_OPEN = "<文件夹画像已更新>"
_PROFILE_UPDATED_CLOSE = "</文件夹画像已更新>"


@dataclass
class UpdateFolderProfileTool:
    """CEO-only: merge-write folder ``画像.md`` (+ optional 导航 / topic notes)."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.MEMORY,
    )

    folder_id: str | None = None
    store: MemoryStore | None = None
    # Precomputed workspace identity for 过期再探 meta; resolved on write if None.
    workspace_key: str | None = None
    # Live prompt holders (delegate / debate) whose ``_system_prompt`` is hot-patched
    # so the next worker LLM round sees the new folder profile this turn.
    prompt_holders: list[Any] = field(default_factory=list)

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=UPDATE_FOLDER_PROFILE_TOOL_NAME,
            description=(
                "把探索幕（或用户点名「先了解」）汇总的文件夹简报写入当前文件夹约定记忆 "
                "「AgentCore/记忆/画像.md」（AI 维护），并可同写短入口「记忆/导航.md」。"
                "仅在有文件夹的对话中可用；画像按固定小节合并更新——有证据的小节替换/增补，"
                "无新证据的小节保留，禁止整篇无故清空。"
                "导航为短入口（一句话定位 +「我要…→先读/先查」路由表）；厚内容放主题条目，"
                "勿把长文塞进导航。"
                "默认只写画像（+建议写导航）；仅当可独立复用的子系统/域≥2 且全塞进画像会臃肿时，"
                f"才用可选 topics（单次软顶 {MAX_EXPLORE_TOPICS}，超额截断）拆到"
                "「记忆/主题/<slug>.md」（整文件覆盖该主题；主题 on_demand，不进 always）。"
                "厚背景资料/域知识一律走 topics 条目，**不要**写成工作区文件（`AgentCore/文档/` "
                "只放 research/debate/reviews 阶段产物）。"
                "用 Markdown「## 小节」+「- 要点」格式。建议覆盖：这个文件夹装的是什么、"
                "技术栈/包管理、关键入口与怎么跑（仅来自清单/README）、风险与边界（有证据才写）、"
                "工作区已有约定摘录（如 AGENTS.md 要点，不改源文件）。"
                "禁止臆造；禁止把单次任务过程写入画像/主题；禁止用 remember 写文件夹简报；"
                "禁止写用户仓根 AGENTS.md/docs。"
                "写入成功后：若用户原请求含实质活 → **必须立刻继续**（直答或再 delegate），"
                "禁止以「已建档/已了解，需要我继续吗」收尾；仅当用户本条只要求了解时可停。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "文件夹画像全文或待合并小节（Markdown：## 小节 + - 要点）。"
                            "空仓禁止编造假画像。"
                        ),
                    },
                    "navigation": {
                        "type": "string",
                        "description": (
                            "可选。文件夹短入口「记忆/导航.md」全文（一句话定位 + 任务路由表）。"
                            "省略则不改已有导航；有实质探索收尾时建议写入。"
                        ),
                    },
                    "topics": {
                        "type": "array",
                        "description": (
                            "可选。按需拆出的文件夹主题笔记；默认省略。"
                            f"每项 slug 为短英文/拼音 id（如 desktop）；"
                            f"单次软顶 {MAX_EXPLORE_TOPICS}（超额截断+warning，不硬拒）。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "slug": {
                                    "type": "string",
                                    "description": "主题文件名 slug（主题/<slug>.md）。",
                                },
                                "content": {
                                    "type": "string",
                                    "description": (
                                        "该主题 Markdown 全文（可复用域事实，非任务过程）。"
                                    ),
                                },
                            },
                            "required": ["slug", "content"],
                        },
                    },
                },
                "required": ["content"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        content = str(arguments.get("content") or "").strip()
        if not self.folder_id:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="当前是裸聊（没有文件夹），不能写文件夹画像。",
                error="no_folder",
            )
        if not content:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="缺少 content。",
                error="缺少 content。",
            )

        topics, topic_warnings = parse_explore_topics(arguments.get("topics"))
        store = self.store if self.store is not None else default_memory_store()
        if topics:
            topics, cap_warnings = await filter_topics_by_scope_cap(
                store,
                context.user_id,
                self.folder_id,
                topics,
                max_topic_files=settings.memory_max_topic_files,
            )
            topic_warnings.extend(cap_warnings)
        try:
            ok, resulting, conflict = await write_folder_profile_cas(
                store=store,
                user_id=context.user_id,
                folder_id=self.folder_id,
                new_markdown=content,
            )
        except Exception as e:  # noqa: BLE001 - tool failure must not crash the turn
            logger.warning(
                "memory.explore_profile_failed",
                user_id=context.user_id,
                error=str(e),
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="写入文件夹画像失败，请稍后再试。",
                error=str(e),
            )

        if conflict:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="写入冲突（画像刚被别处更新），请基于最新内容重试。",
                error="conflict",
                display={"written": False, "conflict": True, "kind": "folder_profile"},
            )
        if not ok or not resulting.strip():
            return ToolResult(
                tool_call_id="",
                success=False,
                output="内容无效或为空，未写入（禁止空仓刷假画像）。",
                error="empty_or_invalid",
                display={"written": False, "kind": "folder_profile"},
            )

        # Profile landed → clear explore-pending so same-turn delivery delegates
        # regain structured form=files → files_written inference + full write_scope.
        context.cold_start_explore_pending = False
        context.write_scope = "project"

        nav_path: str | None = None
        navigation = str(arguments.get("navigation") or "").strip()
        if navigation:
            try:
                nav_path = await write_folder_navigation(
                    store=store,
                    user_id=context.user_id,
                    folder_id=self.folder_id,
                    markdown=navigation,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "memory.explore_navigation_failed",
                    user_id=context.user_id,
                    error=str(e),
                )
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=(
                        "文件夹画像已写入，但导航写入失败："
                        f"{e}。可稍后重试 navigation，或先继续用户原请求。"
                    ),
                    error=str(e),
                    display={
                        "written": True,
                        "navigation_written": False,
                        "kind": "folder_profile",
                        "chars": len(resulting),
                    },
                )

        topic_paths: list[str] = []
        if topics:
            try:
                topic_paths = await write_folder_topics_replace(
                    store=store,
                    user_id=context.user_id,
                    folder_id=self.folder_id,
                    topics=topics,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "memory.explore_topic_failed",
                    user_id=context.user_id,
                    error=str(e),
                )
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=(
                        "文件夹画像已写入，但主题写入失败："
                        f"{e}。可稍后重试 topics，或先继续用户原请求。"
                    ),
                    error=str(e),
                    display={
                        "written": True,
                        "topics_written": False,
                        "kind": "folder_profile",
                        "chars": len(resulting),
                    },
                )

        self._hot_refresh_prompts(resulting, topic_paths, nav_path)
        # Persist workspace identity + fingerprint; clear R2 dirty.
        try:
            key: str | None
            if self.workspace_key:
                key = self.workspace_key
            else:
                from agentcore.conversation.scratch import resolve_conversation_local_binding

                injected = bool(getattr(context, "folder_binding_injected", False))
                binding = None
                if injected:
                    binding = resolve_conversation_local_binding(
                        local_root_id=getattr(context, "folder_local_root_id", None),
                        local_subpath=getattr(context, "folder_local_subpath", None),
                    )
                key = await resolve_folder_workspace_key(
                    self.folder_id,
                    binding=binding,
                    binding_injected=injected,
                )
            if key:
                fingerprint = await compute_workspace_explore_fingerprint(context.backend)
                await record_explore_closeout(
                    store,
                    context.user_id,
                    self.folder_id,
                    workspace_key=key,
                    fingerprint=fingerprint,
                )
        except Exception as e:  # noqa: BLE001 - meta write must not fail the tool
            logger.warning(
                "memory.explore_workspace_key_failed",
                user_id=context.user_id,
                error=str(e),
            )
        clipped = (
            resulting
            if len(resulting) <= _OUTPUT_LIMIT
            else resulting[:_OUTPUT_LIMIT] + "\n…"
        )
        extra_lines: list[str] = []
        if nav_path:
            extra_lines.append(f"已写入导航（always）：{nav_path}。")
        if topic_paths:
            names = "、".join(topic_paths)
            extra_lines.append(f"已写入主题（on_demand，按需 consult）：{names}。")
        topic_line = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
        warn_line = ""
        if topic_warnings:
            warn_line = "\n注意：" + "；".join(topic_warnings)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=(
                "已更新文件夹画像（约定树 AgentCore/记忆/画像.md）。"
                f"{topic_line}{warn_line}\n"
                "若用户原请求含实质活 → **立刻继续**处理（直答或再 delegate）；"
                "禁止以「已建档/已了解，需要我继续吗」收尾。"
                "仅当用户本条只要求了解这个文件夹时可停在简短建档说明。\n\n"
                f"{clipped}"
            ),
            display={
                "written": True,
                "conflict": False,
                "kind": "folder_profile",
                "chars": len(resulting),
                "topics": [p for p in topic_paths],
                "navigation": nav_path,
            },
        )

    def _hot_refresh_prompts(
        self,
        profile_markdown: str,
        topic_paths: list[str] | None = None,
        nav_path: str | None = None,
    ) -> None:
        """Append / replace a same-turn visibility block on live worker system prompts."""
        notes: list[str] = []
        if nav_path:
            notes.append(f"另已写入文件夹导航 {nav_path}（下回合 always 注入）")
        if topic_paths:
            notes.append(
                "另已写入文件夹主题 "
                + "、".join(topic_paths)
                + "，worker 一般不必读；CEO 可 consult"
            )
        topic_note = ("\n（" + "；".join(notes) + "）\n") if notes else ""
        block = (
            f"\n\n{_PROFILE_UPDATED_OPEN}\n"
            "（当前文件夹画像刚由探索幕写入，本回合内以此为准）\n"
            f"{topic_note}"
            f"{profile_markdown.strip()}\n"
            f"{_PROFILE_UPDATED_CLOSE}"
        )
        for holder in self.prompt_holders:
            current = getattr(holder, "_system_prompt", None)
            if not isinstance(current, str) or not current:
                continue
            # Replace a prior mid-turn patch if the CEO writes twice.
            start = current.find(_PROFILE_UPDATED_OPEN)
            if start != -1:
                end = current.find(_PROFILE_UPDATED_CLOSE, start)
                if end != -1:
                    current = (
                        current[:start].rstrip()
                        + current[end + len(_PROFILE_UPDATED_CLOSE) :]
                    )
            holder._system_prompt = current.rstrip() + block


def build_update_folder_profile_tool(
    *,
    folder_id: str | None = None,
    store: MemoryStore | None = None,
    prompt_holders: list[Any] | None = None,
    workspace_key: str | None = None,
) -> UpdateFolderProfileTool:
    return UpdateFolderProfileTool(
        folder_id=folder_id,
        store=store,
        prompt_holders=list(prompt_holders or ()),
        workspace_key=workspace_key,
    )
