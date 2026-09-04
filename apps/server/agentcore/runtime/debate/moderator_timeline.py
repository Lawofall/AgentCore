"""主持人结构化 complete → 既有 run 时间线 delta（思考整段 + 人读 markdown）。

无新事件族：思考走 ``run_reasoning_delta``，人读产物走 ``run_output_delta``，都挂主持人
run_id。连续 content 在 sink 里会合并——靠小标题分段；中间夹 reasoning 则自然切开。
庭前取证 / 辩手发言 / 原始 JSON 不进这条时间线。
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.debate.moderator_brief import _as_handoffs, _normalize_confidence
from agentcore.runtime.debate.moderator_common import _as_bool, _as_str, _as_str_list
from agentcore.runtime.events import run_output_delta, run_reasoning_delta

_HANDOFF_LABEL = {
    "value": "需你定夺",
    "fact": "事实分歧",
    "question": "待解问题",
}


def format_moderator_output(step: str, data: dict[str, Any], *, round_no: int) -> str:
    """从已解析 JSON 抽人读正文；认不出的步（合并 finding / 点名 / crux…）返回空串。"""
    if step == "frame":
        return _format_frame(data, round_no=round_no)
    if step == "cross_exam":
        return _format_questions("质询题", data.get("questions"))
    if step == "witness_exam":
        return _format_questions("质询题", data.get("questions"))
    if step == "assess":
        return _format_assess(data, round_no=round_no)
    if step == "brief":
        return _format_verdict(data)
    return ""


def emit_moderator_complete(
    sink: Any,
    *,
    run_id: str,
    step: str,
    data: dict[str, Any],
    reasoning: str | None,
    round_no: int,
) -> None:
    """思考整段先发，再发人读 content。缺 sink / run_id 由调用方跳过。"""
    think = (reasoning or "").strip()
    if think:
        sink.emit(run_reasoning_delta(run_id, run_id, think))
    body = format_moderator_output(step, data, round_no=round_no).strip()
    if body:
        # 前导换行：无 reasoning 时与上一段 content 合并后标题仍另起一段。
        sink.emit(run_output_delta(run_id, run_id, f"\n\n{body}"))


def _format_frame(data: dict[str, Any], *, round_no: int) -> str:
    parts: list[str] = []
    opening = _as_str(data.get("opening"))
    if opening:
        parts.append(f"## 开场\n\n{opening}")
    focus = _as_str(data.get("focus"))
    if focus:
        n = round_no if round_no > 0 else 1
        parts.append(f"## 第 {n} 轮焦点\n\n{focus}")
    return "\n\n".join(parts)


def _format_questions(title: str, raw: Any) -> str:
    if not isinstance(raw, dict) or not raw:
        return ""
    blocks: list[str] = []
    for key, qs in raw.items():
        items = _as_str_list(qs)
        if not items:
            continue
        numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(items, start=1))
        blocks.append(f"**{key}**\n{numbered}")
    if not blocks:
        return ""
    return f"## {title}\n\n" + "\n\n".join(blocks)


def _format_assess(data: dict[str, Any], *, round_no: int) -> str:
    parts: list[str] = []
    summary = _as_str(data.get("summary"))
    if summary:
        parts.append(f"## 小结\n\n{summary}")
    if not _as_bool(data.get("converged"), False):
        nxt = _as_str(data.get("next_focus"))
        if nxt:
            n = (round_no if round_no > 0 else 1) + 1
            parts.append(f"## 第 {n} 轮焦点\n\n{nxt}")
    return "\n\n".join(parts)


def _format_verdict(data: dict[str, Any]) -> str:
    """终审只写用户面：倾向 / 胜负手 / 置信 / 交接。无比分、不倒灌 CEO 全文。"""
    lines: list[str] = []
    leaning = _as_str(data.get("leaning"))
    if leaning:
        lines.append(f"**倾向**：{leaning}")
    decisive = _as_str(data.get("decisive"))
    if decisive:
        lines.append(f"**胜负手**：{decisive}")
    confidence = _normalize_confidence(_as_str(data.get("confidence")))
    if confidence:
        lines.append(f"**置信**：{confidence}")
    handoff_lines: list[str] = []
    for item in _as_handoffs(data):
        if not item.text:
            continue
        label = _HANDOFF_LABEL.get(item.kind, "待解问题")
        handoff_lines.append(f"- {label}：{item.text}")
    if handoff_lines:
        lines.append("**交接**：\n" + "\n".join(handoff_lines))
    if not lines:
        return ""
    return "## 终审\n\n" + "\n\n".join(lines)
