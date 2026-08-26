"""Official playbook → user-workflow copy catalog (read-only templates).

User workflows are **not** registered into ``PLAYBOOKS``. 「使用」= expand once and
persist a definition snapshot under ``user_workflows``.

Product rule: the template catalog is a **curated subset** of ``PLAYBOOKS``,
not 1:1. Templates = short, repeatable, boss-facing shapes for toolbox copy /
standing tasks. CEO shapes stay the full runtime vocabulary (narrow / recovery /
continuation playbooks stay out of the catalog).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agentcore.runtime.runs.playbooks import PLAYBOOKS, expand_playbook
from agentcore.workflows.definition import (
    WorkflowDefinitionError,
    tasks_dropped_meta_keys,
    tasks_to_workflow_definition,
)

# Desktop template UI only has string textareas; builders expect list for
# parallel_brief.angles / compare_options.options.
_LIST_SLOT_SPLIT = re.compile(r"[,，、\n]+")

# playbook → slot key that UI may send as a single string.
_LIST_SLOTS: dict[str, str] = {
    "parallel_brief": "angles",
    "compare_options": "options",
}


def _coerce_list_slot(val: Any) -> Any:
    """Accept JSON array text or comma/顿号/newline-separated strings → list."""
    if isinstance(val, list) or not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return val
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    parts = [p.strip() for p in _LIST_SLOT_SPLIT.split(s) if p.strip()]
    return parts if parts else val


@dataclass(frozen=True, slots=True)
class PlaybookSlotChoice:
    """One allowed value of an enumerated slot (UI renders a picker, not a textbox)."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class PlaybookTemplateSlot:
    """Machine-readable primary slot — the single source clients render forms from."""

    key: str
    label: str
    required: bool
    hint: str | None = None
    choices: tuple[PlaybookSlotChoice, ...] = ()


# Curated official catalog (product): membership, order and the slots each template
# asks for. Other PLAYBOOKS names → explicit reject; intentionally NOT 1:1 with
# PLAYBOOKS — see module docstring. Builders may accept more slots than listed here.
_SLOT_DEFS: dict[str, tuple[PlaybookTemplateSlot, ...]] = {
    "parallel_brief": (
        PlaybookTemplateSlot(
            key="topic", label="主题", required=True, hint="要摸底对齐的主题"
        ),
        PlaybookTemplateSlot(
            key="angles",
            label="方向",
            required=True,
            hint="≥2 个可并行方向；数组或逗号/顿号分隔，如：法律、品牌、舆情",
        ),
    ),
    "research_report": (
        PlaybookTemplateSlot(
            key="topic", label="主题", required=True, hint="调研 / 报告主题"
        ),
    ),
    "build_app": (
        PlaybookTemplateSlot(
            key="app", label="应用", required=True, hint="要搭建的应用 / SPA 简述"
        ),
    ),
    "compare_options": (
        PlaybookTemplateSlot(
            key="question",
            label="问题",
            required=True,
            hint="要决策的问题，如：选 Postgres 还是 MySQL",
        ),
        PlaybookTemplateSlot(
            key="options",
            label="选项",
            required=True,
            hint="≥2 个待比较选项；数组或逗号/顿号分隔",
        ),
    ),
}

WORKFLOW_PLAYBOOK_IDS: tuple[str, ...] = tuple(_SLOT_DEFS)

_KNOWN: frozenset[str] = frozenset(WORKFLOW_PLAYBOOK_IDS)

# Required primary slots the API asks the user to fill (derived — never hand-listed).
PRIMARY_SLOTS: dict[str, tuple[str, ...]] = {
    pid: tuple(s.key for s in slots if s.required) for pid, slots in _SLOT_DEFS.items()
}

# Soft optional defaults merged under user slots (user wins). Builders already default
# many optionals when omitted; these only make the snapshot more explicit / stable.
_DEFAULT_OPTIONAL_SLOTS: dict[str, dict[str, Any]] = {
    "research_report": {"checkpoint": True},
}

_TITLE: dict[str, str] = {
    "parallel_brief": "多角摸底",
    "research_report": "调研报告成文",
    "build_app": "从零搭应用",
    "compare_options": "方案对比选型",
}

# User-facing blurbs (not raw CEO/runtime summaries).
_SUMMARY: dict[str, str] = {
    "parallel_brief": (
        "多人并行摸清议题并写一页方向笔记；适合「先弄懂」。"
        "要交长文/落盘报告请用「调研报告成文」。"
    ),
    "research_report": (
        "调研→提纲→写作→审校；仅当你明确要落盘成文或交报告时用。"
        "只想弄懂议题请用「多角摸底」。"
    ),
    "build_app": (
        "从零搭一个小应用：脚手架→模块→联调→冒烟；默认瘦启动。"
    ),
    "compare_options": (
        "多路并行评估各选项再汇总对比推荐；适合「选哪个 / A 还是 B」。"
        "只想摸清议题请用「多角摸底」。"
    ),
}

_DEGRADE_NOTE = (
    "复制后可在画布调整拆法；部分执行细项（工具白名单等）不会带入快照。"
)


@dataclass(frozen=True, slots=True)
class PlaybookTemplateItem:
    id: str
    title: str
    summary: str
    # Prose one-liner for chat / help copy; ``slots`` is what clients render.
    primary_slots: str
    slots: tuple[PlaybookTemplateSlot, ...]


class PlaybookTemplateError(ValueError):
    """Unknown / not-in-catalog playbook, missing primary slot, or expand failure."""


def is_workflow_playbook(name: str | None) -> bool:
    return bool(name) and name in _KNOWN


def _slot_help(slot: PlaybookTemplateSlot) -> str:
    parts = ["必填" if slot.required else "可选"]
    if slot.hint:
        parts.append(slot.hint)
    if slot.choices:
        allowed = " / ".join(f"{c.value}={c.label}" for c in slot.choices)
        parts.append(f"可选值：{allowed}")
    return f"{slot.key}（{'，'.join(parts)}）"


def primary_slots_help(playbook: str) -> str:
    """Render the prose slot help from the structured slots (same single source)."""
    return "；".join(_slot_help(s) for s in _SLOT_DEFS.get(playbook, ()))


def list_playbook_templates() -> list[PlaybookTemplateItem]:
    out: list[PlaybookTemplateItem] = []
    for pid in WORKFLOW_PLAYBOOK_IDS:
        pb = PLAYBOOKS.get(pid)
        base_summary = _SUMMARY.get(pid) or (pb.summary if pb is not None else "")
        summary = f"{base_summary}（{_DEGRADE_NOTE}）" if base_summary else _DEGRADE_NOTE
        out.append(
            PlaybookTemplateItem(
                id=pid,
                title=_TITLE.get(pid, pid),
                summary=summary,
                primary_slots=primary_slots_help(pid),
                slots=_SLOT_DEFS[pid],
            )
        )
    return out


def merge_playbook_slots(playbook: str, slots: dict[str, Any] | None) -> dict[str, Any]:
    """Validate catalog membership + primary slots; merge soft optional defaults."""
    if not is_workflow_playbook(playbook):
        if playbook in PLAYBOOKS:
            raise PlaybookTemplateError(
                f"playbook「{playbook}」暂未列入官方工作流模板；"
                f"可用：{', '.join(WORKFLOW_PLAYBOOK_IDS)}"
            )
        raise PlaybookTemplateError(
            f"未知 playbook「{playbook}」；可用：{', '.join(WORKFLOW_PLAYBOOK_IDS)}"
        )
    if slots is not None and not isinstance(slots, dict):
        raise PlaybookTemplateError("slots 必须是对象")

    merged: dict[str, Any] = dict(_DEFAULT_OPTIONAL_SLOTS.get(playbook) or {})
    merged.update(dict(slots or {}))

    # List slots: coerce UI string → list before missing/expand checks.
    list_key = _LIST_SLOTS.get(playbook)
    if list_key is not None and list_key in merged:
        merged[list_key] = _coerce_list_slot(merged[list_key])

    missing: list[str] = []
    for key in PRIMARY_SLOTS[playbook]:
        val = merged.get(key)
        if isinstance(val, str):
            if not val.strip():
                missing.append(key)
        elif val is None or isinstance(val, list) and len(val) == 0:
            missing.append(key)
        # Non-empty list / other types → leave to expand_playbook errors.
    if missing:
        help_text = primary_slots_help(playbook)
        raise PlaybookTemplateError(f"缺少主槽：{', '.join(missing)}；需要 {help_text}")

    for slot in _SLOT_DEFS[playbook]:
        if not slot.choices:
            continue
        val = merged.get(slot.key)
        if not isinstance(val, str) or not val.strip():
            continue
        allowed = [c.value for c in slot.choices]
        if val.strip() not in allowed:
            raise PlaybookTemplateError(
                f"槽位 {slot.key} 只接受：{' / '.join(allowed)}；收到「{val.strip()}」"
            )
    return merged


def instantiate_from_playbook(
    playbook: str,
    slots: dict[str, Any] | None,
    *,
    name: str | None = None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Expand official playbook → workflow definition snapshot.

    Returns ``(name, description, definition)`` ready for ``UserWorkflowRepository.create``.
    """
    merged = merge_playbook_slots(playbook, slots)
    tasks, errors = expand_playbook(playbook, merged)
    if errors:
        raise PlaybookTemplateError("；".join(errors))
    if not tasks:
        raise PlaybookTemplateError(f"playbook「{playbook}」展开结果为空")

    try:
        definition = tasks_to_workflow_definition(tasks)
    except WorkflowDefinitionError as e:
        raise PlaybookTemplateError(str(e)) from e

    title = _TITLE.get(playbook, playbook)
    primary_key = PRIMARY_SLOTS[playbook][0]
    primary_val = merged.get(primary_key)
    primary_label = (
        primary_val.strip()
        if isinstance(primary_val, str) and primary_val.strip()
        else ""
    )
    resolved_name = (name or "").strip() or (
        f"{title} · {primary_label}" if primary_label else title
    )

    dropped = tasks_dropped_meta_keys(tasks)
    desc_parts = [f"由官方模板「{title}」（{playbook}）复制"]
    if dropped:
        desc_parts.append(f"已降级字段：{', '.join(dropped)}")
    description = "；".join(desc_parts)

    return resolved_name, description, definition
