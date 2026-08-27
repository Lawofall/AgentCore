"""具名 playbook 登记表 — 权威 docs/03-AI核心/编排器与CEO主Agent.md 「具名 playbook」。

Default path is handwritten top-level ``tasks``. Named playbook is an XOR shortcut:
the CEO names one + fills slots; the builder emits the same ``tasks`` dict-list
``build_run_plan`` already consumes (build_run_plan → drive → executor → ceo_format).
Not a template engine; not a boss-facing menu.

Deliberately SMALL. A name is a frozen pipeline shape, not a scene label.
``build_app.intensity=lean|full`` is the one allowed greenfield structural slot
(this round: do not split into two names); other forks stay handwritten.
"""

from __future__ import annotations

import errno
import re
from typing import Any

from agentcore.runtime.runs.playbooks._common import (
    CODE_AUDIT_FANOUT,
    MAX_PLAYBOOK_FANOUT,
    USER_MESSAGE_MECH_KEY,
    Playbook,
    PlaybookBuilder,
    clean_str,
)
from agentcore.runtime.runs.playbooks.audit import code_audit
from agentcore.runtime.runs.playbooks.build_soft import (
    build_app,
    diagnose_fix_verify,
)
from agentcore.runtime.runs.playbooks.research import (
    cite_write_review,
    lens_crosscheck,
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
            "2 路并行交 CEO 收口；≥3 路才加主管速览；"
            "正交于 map_fanout（摸底）/ cite_write_review（成文审校）/"
            " diagnose_fix_verify（按症状修）"
        ),
        slots=(
            "scope(必填,审计范围路径或子系统;亦接受 topic/target) / "
            "modules(可选;探路后≥2 可独立并行产品缝则填短名/路径→2 路并行无主管、≥3 路才+主管速览,"
            f"先 2–3、能少则少;【禁】按目录树默认拆到上限;"
            "目录细拆仅当探路证明真并行且单缝扛不住;"
            f"上限 {CODE_AUDIT_FANOUT} 超限末槽折叠;"
            "单缝省略;playbook 不从 scope 自动拆;"
            "禁把多目录拼进 scope 冒充多模块;禁把长作文当模块名,侧重进 focus) / "
            "focus(可选,侧重如 security|eng|流式刷新) / "
            "k(可选,每模块 Phase B 定案上限,默认 8) / "
            "output_path(可选,单模块报告或汇总 code-audit-summary 覆盖路径)"
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
    "diagnose_fix_verify": Playbook(
        name="diagnose_fix_verify",
        summary=(
            "【无先验调查批】诊断(短)→修补→验证的单症状修码（runtime 错 / 缺 export / "
            "白屏挂载；短轮次；禁触顶后换马甲；playbook_args 须 verify="
            "CLI 或 UI 复现说明；已有调查批确认修→手写+continue_from，勿套本 playbook）"
        ),
        slots=(
            "problem(必填,错误症状/缺 export/白屏挂载等) / "
            "verify(必填,怎么算修好:CLI 命令或页面/UI 复现说明;"
            "例:verify=\"pytest tests/test_app.py -q\" 或 "
            "verify=\"打开 /app 白屏消失+snapshot 可见主内容\";"
            "亦接受 verify_command/acceptance) / "
            "target(可选,优先文件路径) / artifacts(可选,落盘路径数组)"
        ),
        build=diagnose_fix_verify,
    ),
    "build_app": Playbook(
        name="build_app",
        summary=(
            "绿场软件/SPA：intensity 编制档——"
            "默认 lean=scaffold→单实现(公共层+主流程)→smoke；"
            "full=scaffold→shared→N×module→integrate→smoke"
            "（full 五阶段不可跳；modules 扇出仅 full；禁扫用户原文猜档）"
        ),
        slots=(
            "app(必填,要搭建的应用/SPA简述——"
            "例:app=\"面向运营的 Vue3 数据看板\") / "
            "intensity(可选,编制档:lean 默认三节点立刻派;"
            "full=五阶段满档含 shared/多 module/integrate) / "
            "modules(可选,功能模块名数组;"
            "lean=覆盖清单不扇出;full=各派一名实现,默认仅总览页,超过 3 个折叠到末槽) / "
            "stack(可选,技术栈,默认 Vue3+Vite+TS) / "
            "root(可选,工程目录名,默认固定 app/；禁止从 app 简述派生 slug)"
        ),
        build=build_app,
    ),
    "lens_crosscheck": Playbook(
        name="lens_crosscheck",
        summary=(
            "异质透镜并行调研→汇总交叉验证（可产 motion_card 建议开辩；"
            "调研报告落盘 AgentCore/文档/research/；默认法律/品牌商业/舆情公关/文化社会）"
        ),
        slots=(
            "topic(必填,主题/事件) / lenses(可选,透镜名数组；"
            "超过扇出上限时末尾自动折叠到最后一节点并标注、不丢弃；"
            "默认法律·品牌商业·舆情公关·文化社会)"
        ),
        build=lens_crosscheck,
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
        "具名 playbook 快捷槽位对象（与 playbook 联用；默认手写 tasks 时勿传）。"
        "绿场必填 app——勿空对象。"
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
    user_message: str = "",
    conversation_id: str = "",  # noqa: ARG001 — call-site compat; DESIGN inject is executor-side
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand a named playbook + slot args into a ``tasks`` dict-list for ``build_run_plan``.

    ``user_message`` is the turn's raw user line (from DelegateTool), injected as a
    mechanism-only key for playbooks that need proposition fidelity (e.g. lens_crosscheck
    synthesizer). Not a CEO-facing slot.

    ``conversation_id`` is accepted for call-site compatibility. Website DESIGN
    inject is executor-side (``web_quality_scan`` workers), not playbook expansion.

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
    um = clean_str(user_message)
    if um:
        slot_args[USER_MESSAGE_MECH_KEY] = um
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
