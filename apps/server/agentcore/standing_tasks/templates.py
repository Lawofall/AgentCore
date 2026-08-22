"""System standing-task templates (预制管家任务).

Phase 1: ``daily_conversation_review`` — opt-in daily butler report over recent
chats; writes only after the user confirms in the standing turn (ask_user).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agentcore.workspace.stage_dirs import REVIEWS_DIR

TemplateKey = Literal["daily_conversation_review"]

DAILY_CONVERSATION_REVIEW: TemplateKey = "daily_conversation_review"

_KNOWN: frozenset[str] = frozenset({DAILY_CONVERSATION_REVIEW})

# Prefer ask on file writes so doc drafts go through the standing approvals gate.
# Must stay legal: command=auto ∧ file_write=ask is rejected by PermissionAxes.
DEFAULT_TEMPLATE_AXES: dict[str, str] = {
    "file_write": "ask",
    "command": "ask",
    "host": "ask",
}

_DEFAULT_LOOKBACK_HOURS = 24
_MAX_LOOKBACK_HOURS = 168  # 7d hard cap


@dataclass(frozen=True, slots=True)
class TemplateCatalogItem:
    key: TemplateKey
    title: str
    description: str
    default_name: str
    default_cron: str  # UTC; desktop may rewrite from local time picker


def list_catalog() -> list[TemplateCatalogItem]:
    return [
        TemplateCatalogItem(
            key=DAILY_CONVERSATION_REVIEW,
            title="每日对话复盘",
            description=(
                "每天自动检索近期对话，整理成管家式复盘报告；"
                "你确认后才更新记忆、写入文档草稿或采纳规则建议。"
            ),
            default_name="每日对话复盘",
            # 01:00 UTC ≈ 09:00 CST — desktop overwrites from local time on ensure/edit.
            default_cron="0 1 * * *",
        ),
    ]


def is_known_template(key: str | None) -> bool:
    return bool(key) and key in _KNOWN


def normalize_template_config(raw: object | None) -> dict[str, Any]:
    """Validate / normalize ``template_config`` for daily review (and future keys)."""
    src = raw if isinstance(raw, dict) else {}
    include_global = bool(src.get("include_global", True))
    folder_ids_raw = src.get("folder_ids") or []
    folder_ids: list[str] = []
    if isinstance(folder_ids_raw, list):
        seen: set[str] = set()
        for item in folder_ids_raw:
            fid = str(item or "").strip()
            if fid and fid not in seen:
                seen.add(fid)
                folder_ids.append(fid)
    raw_lookback = src.get("lookback_hours", _DEFAULT_LOOKBACK_HOURS)
    if raw_lookback is None or raw_lookback == "":
        lookback = _DEFAULT_LOOKBACK_HOURS
    else:
        try:
            lookback = int(raw_lookback)
        except (TypeError, ValueError):
            lookback = _DEFAULT_LOOKBACK_HOURS
    lookback = max(1, min(lookback, _MAX_LOOKBACK_HOURS))
    if not include_global and not folder_ids:
        # At least one scope facet — default back to global.
        include_global = True
    return {
        "include_global": include_global,
        "folder_ids": folder_ids,
        "lookback_hours": lookback,
    }


def daily_review_goal() -> str:
    """Canonical goal text stored on the standing-task row (system-owned)."""
    return (
        "你是用户的系统管家，执行「每日对话复盘」。\n"
        "\n"
        "【流程】\n"
        "1. 按下方「本次作用域」用 delegate 派出查阅员："
        "search_conversations（设 updated_within_hours）→ 对重要场次 read_conversation。\n"
        "2. 整理「今日复盘」：时段摘要（白话）+ 可落盘提案列表。\n"
        "3. 有提案时：调用 ask_user，**必须** card=\"daily_review\"，恰好 1 题、"
        "multiple=true；每个 option 含：\n"
        "   - label：短标题\n"
        "   - detail：一行说明\n"
        "   - review_kind：preference | profile | topic | rule | doc\n"
        "   - body：要写入的正文\n"
        "   - topic 另加 slug；doc 可加 path（默认 "
        f"{REVIEWS_DIR}/YYYY-MM-DD.md）；preference/profile 可加 section\n"
        "   确认后由**服务端直接落盘**，你不要再 remember / file_write。\n"
        "4. 若查阅后无可沉淀信号：不要弹 daily_review 卡；白话说明「今日无新料」后收工。\n"
        "\n"
        "【约束】\n"
        "- 禁止静默改用户硬规则；规则只作为 review_kind=rule 提案，由用户勾选后服务端写入。\n"
        "- 不要替代日常语义巩固；只做跨场复盘与可见报告。\n"
        "- 对外说话用白话，不报内部工具名。\n"
    )


def build_scope_briefing(
    config: dict[str, Any],
    *,
    folder_names: dict[str, str] | None = None,
) -> str:
    """Appended to the fire message so the CEO sees today's scope."""
    cfg = normalize_template_config(config)
    names = folder_names or {}
    lines = [
        "【本次作用域】",
        f"- 回看窗口：近 {cfg['lookback_hours']} 小时",
        f"- 包含全局裸聊：{'是' if cfg['include_global'] else '否'}",
    ]
    folder_ids: list[str] = list(cfg["folder_ids"])
    if folder_ids:
        lines.append("- 包含文件夹：")
        for fid in folder_ids:
            label = names.get(fid) or fid
            lines.append(f"  - {label}（folder_id=`{fid}`）")
        lines.append(
            "查阅时：对每个文件夹分别 search_conversations"
            "（scope=folder + folder_id，并设 updated_within_hours）；"
            "若含裸聊再搜 scope=global_chats。"
        )
    elif cfg["include_global"]:
        lines.append(
            "- 仅裸聊：search_conversations(scope=global_chats, updated_within_hours=…)"
        )
    else:
        lines.append("- （无有效作用域，请 ask_user 请用户改配置）")
    lines.append(
        f"- 文档落点目录：`{REVIEWS_DIR}/`（确认后再写）"
    )
    return "\n".join(lines)


def compose_template_fire_message(
    *,
    template_key: str,
    goal: str,
    template_config: dict[str, Any] | None,
    folder_names: dict[str, str] | None = None,
    event_text: str | None = None,
) -> str:
    """Build the user message for a templated standing-task fire."""
    from agentcore.standing_tasks.webhook import build_fire_message

    base = goal.strip() or (
        daily_review_goal() if template_key == DAILY_CONVERSATION_REVIEW else goal
    )
    if template_key == DAILY_CONVERSATION_REVIEW:
        briefing = build_scope_briefing(
            template_config or {}, folder_names=folder_names
        )
        base = f"{base.rstrip()}\n\n{briefing}"
    return build_fire_message(goal=base, event_text=event_text)
