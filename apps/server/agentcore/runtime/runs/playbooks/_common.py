"""Shared playbook primitives: clean/fold helpers, Playbook type, mechanism keys."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Cap the slot-driven fan-out (调研子方向 / 待比较选项) so a playbook can't silently balloon a
# batch;
# build_run_plan still enforces the global MAX_DELEGATION_TASKS on the expanded result as the real
# net. Kept modest because a playbook is a STANDARD shape, not a place to launch a huge swarm.
MAX_PLAYBOOK_FANOUT = 6
# code_audit only: packing cap for module slots (overflow folds into last). Not a target
# headcount — prompt / schema say start at 2–3 product seams. Do not raise MAX_PLAYBOOK_FANOUT.
CODE_AUDIT_FANOUT = 8

PlaybookBuilder = Callable[[dict[str, Any]], "tuple[list[dict[str, Any]], list[str]]"]


@dataclass(frozen=True)
class Playbook:
    """One named, instantiable team shape: ``build(slots) -> (tasks, errors)``.

    ``summary`` / ``slots`` are the human-facing one-liners surfaced in the ``delegate`` schema
    and the ``team_orchestration_advanced`` skill so the CEO knows the shape exists and what to
    pass; ``build`` is the pure expander.
    """

    name: str
    summary: str
    slots: str
    build: PlaybookBuilder


def clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def clean_str_list(value: Any, *, cap: int | None = None) -> list[str]:
    """Normalise a slot to a deduped list of non-empty strings (preserves order, drops
    non-strings / blanks). ``cap`` truncates when set; ``None`` keeps all. A non-list
    slot → ``[]`` so the builder's own required-slot check produces the user-facing
    error rather than a type crash."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = item.strip() if isinstance(item, str) else ""
        if s and s not in out:
            out.append(s)
        if cap is not None and len(out) >= cap:
            break
    return out


def fold_fanout_slots(
    items: list[str],
    *,
    limit: int = MAX_PLAYBOOK_FANOUT,
    label: str = "项",
) -> tuple[list[list[str]], str | None]:
    """Pack ``items`` into ≤``limit`` slots; overflow is merged into the last slot (not dropped).

    Each slot is a non-empty list of names (usually length 1). When folding occurs, the
    second return is a CEO/user-facing note describing what was merged; otherwise ``None``.
    ``label`` customises the note noun（角度 / 透镜 / 分区…）.
    """
    if not items:
        return [], None
    if len(items) <= limit:
        return [[x] for x in items], None
    head = items[: limit - 1]
    tail = items[limit - 1 :]
    slots = [[x] for x in head] + [list(tail)]
    note = (
        f"【扇出折叠】共 {len(items)} 个{label}，超过扇出上限 {limit}；"
        f"已将末尾 {len(tail)} 个合并到最后一个节点"
        f"（{' · '.join(tail)}），未丢弃。"
        "末节点职责须覆盖上述全部合并项。"
        "请在对用户的计划/结果说明中点明本次折叠及明细。"
    )
    return slots, note


# 成文综述学术检索加句（仅 cite_write_review 调研员；map_fanout 不盖）。
# 与 search_policy=academic_literature 配套：先论文库、搜废报缺口、禁脑补全面综述。
RESEARCHER_ACADEMIC_SEARCH_DISCIPLINE = (
    "【学术检索】优先论文库 / 预印本 / DOI（arxiv、PubMed、doi.org、CNKI 等）；"
    "百科 / 词典 / 门户命中过多视为搜废——须报告证据缺口并换论文站策略，"
    "禁止脑补成全面综述。"
)
