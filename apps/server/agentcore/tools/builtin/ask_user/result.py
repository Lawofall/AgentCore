"""Map ask_user checkpoint responses to tool results (live + resume shared)."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.tools.protocol import ToolResult


def _option_label(opt: Any) -> str:
    if isinstance(opt, dict):
        return str(opt.get("label") or "").strip()
    return str(opt or "").strip()


def structured_options_summary(
    questions: list[dict[str, Any]] | None = None,
) -> str:
    """Join choice labels for continue/pause restatement (d4d5)."""
    chunks: list[str] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        labels: list[str] = []
        for opt in q.get("options") or []:
            label = _option_label(opt)
            if not label:
                continue
            labels.append(label)
        if not labels:
            continue
        prompt = str(q.get("prompt") or "").strip()
        joined = " / ".join(labels)
        chunks.append(f"{prompt}：{joined}" if prompt else joined)
    return "；".join(chunks)


def _note_user_consulted() -> None:
    """Remember that this coordination stretch heard the user, for the next tell."""
    try:
        from agentcore.runtime.coordination.session import active_coordination

        session = active_coordination()
    except Exception:  # noqa: BLE001 — asking must not fail if coordination is absent
        return
    if session is not None and session.active:
        session.user_consulted = True


def ask_user_tool_result(
    response: CheckpointResponse,
    *,
    questions: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Map the user's ask_user answer to the tool result the CEO loop consumes.

    The single source of truth for both the live tool (``AskUserTool.execute``) and
    a durable resume (``runtime/pipeline.resume_chat_pipeline``): submit / stop /
    timeout all feed ``CONTINUE`` results so the CEO resumes (stop is **拒答**, not
    empty-continue「按默认」；wire stays ``decision=stop``). Soft guidance on stop
    lets the model see the refuse and close / rephrase / proceed with assumptions —
    no in-band ``INTERACT`` terminal that skips the CEO round.

    答复正文 (α 答复模型): the desktop composes the user's per-question picks + style +
    free-form note into ONE readable ``note`` string (the picks live in the UI, so the
    answer is composed where the data is — no structured wire payload the only-reader CEO
    would just flatten back to prose anyway).

    Empty continue (no note/picks) with options → inject「复述并沿用上轮确认选项」
    so the CEO does not invent a new menu; that is not a pick of any one option.
    No options → legacy「按你提出的方向继续」. Leftover ``questions[].default`` is ignored.
    """
    decision = response.decision
    if decision is CheckpointDecision.ADJUST:
        raise ValueError("ask_user checkpoints do not accept ADJUST; use CONTINUE with note")
    picks = "、".join(response.selected)
    note = response.note.strip()
    if decision is CheckpointDecision.CONTINUE:
        if note and picks:
            output = f"用户选择：{picks}；并补充：\n{note}\n请据此继续。"
        elif note:
            # The desktop's composed answer (per-question picks + style + note) rides here.
            output = f"用户答复：\n{note}\n请据此继续。"
        elif picks:
            output = f"用户选择：{picks}。请按此继续。"
        else:
            options = structured_options_summary(questions)
            if options:
                output = (
                    f"用户确认继续。请复述并沿用上轮确认选项：{options}。"
                    "【禁止】空转确认、不承接选项；"
                    "【禁止】另拟一套，也【禁止】叠已结算的确认话术。"
                )
            else:
                output = "用户确认：按你提出的方向继续。"
        _note_user_consulted()
        return ToolResult(tool_call_id="", success=True, output=output)
    if decision is CheckpointDecision.STOP:
        # 拒答可见：回灌 CEO（对齐 OpenAI reject→resume）；非空 continue。
        # 拒答后默认收口——真实回合里「换假设继续」被当成了平级选项，模型接着又起了
        # 一轮工具，用户只能去按硬停止。收口是默认，继续是例外。
        head = "用户取消了澄清，未作答。"
        guidance = (
            "默认据此收口：用正文说清已完成什么、卡在哪、建议的下一步；"
            "【禁止】再弹 ask_user 追问（换个问法也不行）。"
            "仅当手上工作已能无歧义推进时才换假设继续，并在正文写明所换假设。"
        )
        output = (
            f"{head}用户留言：{note}\n{guidance}" if note else f"{head}\n{guidance}"
        )
        return ToolResult(tool_call_id="", success=True, output=output)
    # TIMEOUT — never silently picked a branch; let the CEO decide how to close.
    return ToolResult(
        tool_call_id="",
        success=True,
        output="用户未在时限内回应。请基于目前已掌握的信息，自行决定如何稳妥收尾。",
    )
