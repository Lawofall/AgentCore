"""具名 playbook 登记表 — 权威 docs/03-AI核心/编排器与CEO主Agent.md 「具名 playbook」。

Default path is handwritten top-level ``tasks``. Named playbook is an XOR shortcut:
the CEO names one + fills slots; the builder emits the same ``tasks`` dict-list
``build_run_plan`` already consumes (build_run_plan → drive → executor → ceo_format).
Not a template engine; not a boss-facing menu.

Deliberately SMALL. A name is a frozen pipeline shape, not a scene label.
"""

from __future__ import annotations

import errno
import re
from typing import Any

from agentcore.runtime.runs.playbooks._common import (
    CODE_AUDIT_FANOUT,
    MAX_PLAYBOOK_FANOUT,
    Playbook,
    PlaybookBuilder,
)
from agentcore.runtime.runs.playbooks.audit import code_audit
from agentcore.runtime.runs.playbooks.research import (
    cite_write_review,
    map_fanout,
)

PLAYBOOKS: dict[str, Playbook] = {
    "code_audit": Playbook(
        name="code_audit",
        summary=(
            "【代码审计】A 宽扫→B 定案两阶段；强制字段/严重度/checklist/人审骨架；"
            "报告落 AgentCore/文档/reviews/；扇出靠 CEO 填 modules（不从 scope 自动拆；"
            f"先按产品缝 2–3、能少则少；目录细拆仅当探路证明真并行且单缝扛不住；"
            f"上限 {CODE_AUDIT_FANOUT}）；"
            "多路并行交 CEO 收口（引擎合并台账）；"
            "正交于 map_fanout（摸底）/ cite_write_review（成文审校）"
        ),
        slots=(
            "scope(必填,审计范围路径或子系统;亦接受 topic/target) / "
            "modules(可选;探路后≥2 可独立并行产品缝则填短名/路径→多路并行交 CEO 收口,"
            f"先 2–3、能少则少;【禁】按目录树默认拆到上限;"
            "目录细拆仅当探路证明真并行且单缝扛不住;"
            f"上限 {CODE_AUDIT_FANOUT} 超限末槽折叠;"
            "单缝省略;playbook 不从 scope 自动拆;"
            "禁把多目录拼进 scope 冒充多模块;禁把长作文当模块名,侧重进 focus) / "
            "focus(可选,侧重如 security|eng|流式刷新) / "
            "k(可选,每模块 Phase B 定案上限,默认 8) / "
            "output_path(可选,单模块报告覆盖路径)"
        ),
        build=code_audit,
    ),
    "map_fanout": Playbook(
        name="map_fanout",
        summary=(
            "【对齐推进·A 摸清】一起弄懂/多路摸清/讨论对齐且确有 ≥2 独立缝时："
            "N 路并行摸底→方向笔记落盘→交回 CEO 对话综述；无提纲/撰稿/审校"
            "（未明示成文勿升 cite_write_review；仅确有 ≥2 独立缝才用本套餐，人数跟缝走、能少则少；"
            "够用即停：一页地图（定位/技术栈或手段/进度/开放问题）；"
            "默认自己干、禁开局长通读再招人；handoff 短摘要必交；"
            "笔记 file_write（过长分段），禁写进用户可见回复；"
            "出处写路径、禁凑编号；不套上网检索套话；"
            "angles 每条一句目标，禁模块清单/大纲/必读文件；"
            "只读摸底禁改业务代码）"
        ),
        slots=(
            "topic(必填,主题) / angles(必填,≥2 个可并行摸底方向;人数跟缝走、能少则少；"
            "每条一句目标；讨论对齐/摸清用本槽，勿当成长文大纲或必读书单扇出；"
            "超过扇出上限时末尾自动折叠到最后一节点并标注、不丢弃)"
        ),
        build=map_fanout,
    ),
    "cite_write_review": Playbook(
        name="cite_write_review",
        summary=(
            "【成文专线·B/重】仅用户明示成文且需正式长文/可提交"
            "（或已确认要审校满编）时用：调研→提纲→写作→审校"
            "（N 路并行调研汇拢成纲再成文；成篇验收钉死单一主文件 `.md`；"
            "PDF/Word → consult(team_delivery_env)）。"
            "讨论/形态未定勿首派；普通构想勿默认学术审校；"
            "一起弄懂/多路摸清/仅提论文开源当资料请用 map_fanout"
        ),
        slots=(
            "topic(必填,正式长文/可提交主题;讨论或形态未定请先 map_fanout) / "
            "angles(可选,调研子方向数组,各派一名调研员;"
            "仅明示成文且走本专线后再扇出；宜少；"
            "超过扇出上限时末尾自动折叠到最后一节点并标注、不丢弃) / "
            "checkpoint(可选,成纲后写作前暂停过目,默认 true) / audience(可选,读者) / "
            "deliverable(可选,产出形态) / "
            "output_path(可选,成篇主文件路径,默认 AgentCore/文档/research/报告.md；验收只认此路径)"
        ),
        build=cite_write_review,
    ),
}


def available_playbooks() -> str:
    """One-line ``name（summary）`` listing for schema / skill / error messages — single source so
    the available set never drifts between the registry and what the CEO is told."""
    return "；".join(f"{p.name}（{p.summary}）" for p in PLAYBOOKS.values())


def playbook_args_schema_description() -> str:
    """``delegate.playbook_args`` schema description.

    Always-on path skips consult, so schema must carry required keys *and*
    a short cue for fan-out-critical optional slots the engine will not infer
    (``code_audit.modules``). Cap / fold / omit HOW lives in slots + skill.
    """
    cues: list[str] = []
    for p in PLAYBOOKS.values():
        req = re.findall(r"(\w+)\(必填", p.slots)
        if req:
            cues.append(f"{p.name}→{'/'.join(req)}")
    required_cues = "；".join(cues)
    return (
        "具名 playbook 快捷槽位对象（与 playbook 联用）。"
        f"必填槽：{required_cues}。"
        "code_audit modules：整仓按产品缝扇出（先 2–3，不从 scope 自动拆；勿按目录填满上限）。"
        "其余可选槽→consult(team_orchestration_advanced)。"
    )


def collect_playbook_notes(tasks: list[dict[str, Any]]) -> list[str]:
    """Deduped ``playbook_note`` strings from expanded tasks (CEO-facing fold/merge notices)."""
    notes: list[str] = []
    seen: set[str] = set()
    for t in tasks:
        raw = t.get("playbook_note")
        if not isinstance(raw, str):
            continue
        note = raw.strip()
        if note and note not in seen:
            seen.add(note)
            notes.append(note)
    return notes


def expand_playbook(
    name: str,
    args: dict[str, Any] | None,
    *,
    user_message: str = "",  # noqa: ARG001 — call-site compat
    conversation_id: str = "",  # noqa: ARG001 — call-site compat
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand a named playbook + slot args into a ``tasks`` dict-list for ``build_run_plan``.

    ``user_message`` and ``conversation_id`` are accepted for call-site compatibility;
    no current playbook consumes them.

    Returns ``(tasks, errors)``; a non-empty ``errors`` means the instantiation is rejected (unknown
    name, bad args type, missing required slot, or missing packaged internal resource) and the
    caller must NOT run it — mirroring
    ``build_run_plan``'s reject-on-error contract so the delegate entry handles both the same
    way."""
    pb = PLAYBOOKS.get(name)
    if pb is None:
        return [], [f"未知 playbook『{name}』；可用：{available_playbooks()}"]
    if args is not None and not isinstance(args, dict):
        return [], [f"playbook_args 必须是对象；{pb.name} 槽位：{pb.slots}"]
    slot_args: dict[str, Any] = dict(args or {})
    try:
        return pb.build(slot_args)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno != errno.ENOENT:
            raise
    return [], [
        f"playbook『{pb.name}』内部打包资源缺失，无法实例化；"
        "请去掉 playbook，改为手写 tasks 数组。"
    ]


__all__ = [
    "CODE_AUDIT_FANOUT",
    "MAX_PLAYBOOK_FANOUT",
    "PLAYBOOKS",
    "Playbook",
    "PlaybookBuilder",
    "available_playbooks",
    "collect_playbook_notes",
    "expand_playbook",
    "playbook_args_schema_description",
]
